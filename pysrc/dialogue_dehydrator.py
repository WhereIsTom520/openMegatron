import os
import json
import asyncio
import re
import logging
import hashlib
import uuid
import time
from enum import Enum
from typing import List, Dict, Optional, Any, Tuple
from pydantic import BaseModel, Field
from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, RateLimitError
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type
from neo4j import AsyncGraphDatabase
import asyncpg
from pgvector.asyncpg import register_vector
from sentence_transformers import SentenceTransformer
import redis.asyncio as aioredis
from redis.exceptions import TimeoutError as RedisTimeoutError, ConnectionError as RedisConnectionError
from logging_setup import configure_module_logger

try:
    import tomllib
except ImportError:
    import tomli as tomllib

os.environ["TQDM_DISABLE"] = "1"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

for lib in ["sentence_transformers", "transformers", "torch", "urllib3", "asyncpg", "neo4j"]:
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = configure_module_logger(__name__, "dialogue_dehydrator.log")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)
logging.getLogger("asyncpg").setLevel(logging.WARNING)

class PipelineStage(str, Enum):
    PENDING = "pending"
    DEHYDRATION = "dehydration"
    TRACKING = "tracking"
    RESOLUTION = "resolution"
    EXTRACTION = "extraction"
    COMPLETED = "completed"
    FAILED = "failed"

class DecisionPoint(BaseModel):
    has_shift: bool
    before_state: Optional[str] = None
    after_state: Optional[str] = None

class NodeExt(BaseModel):
    id: str
    label: str
    properties: Dict[str, Any] = Field(default_factory=dict)

class EdgeExt(BaseModel):
    source: str
    target: str
    type: str
    properties: Dict[str, Any] = Field(default_factory=dict)

class GraphExtraction(BaseModel):
    nodes: List[NodeExt]
    edges: List[EdgeExt]

class PipelineState(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    dialogue_id: str
    raw_text: str
    owner_id: str = "SYSTEM"
    scope: str = "shared"
    current_stage: PipelineStage = PipelineStage.PENDING
    core_sentences: List[str] = Field(default_factory=list)
    resolution_map: Dict[str, str] = Field(default_factory=dict)
    resolved_text: str = ""
    decision_point: Optional[DecisionPoint] = None
    extracted_graph: Optional[GraphExtraction] = None
    error_msg: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    memory_card: Dict[str, Any] = Field(default_factory=dict)
    memory_links: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    evolution_suggestions: List[Dict[str, Any]] = Field(default_factory=list)

RETRY_EXC = (APIConnectionError, APITimeoutError, RateLimitError, json.JSONDecodeError)

def load_config(config_path: str = "model.toml") -> dict:
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "rb") as f:
        config_data = tomllib.load(f)
    llm_block = config_data.get("llm", {})
    active_provider = llm_block.get("active_provider", "openai")
    provider_config = llm_block.get(active_provider, {})
    return {
        "llm": {
            "api_key": provider_config.get("api_key", ""),
            "base_url": provider_config.get("base_url"),
            "model": provider_config.get("model", "gpt-4o-mini"),
            "extra_params": provider_config.get("extra_params", {})
        },
        "neo4j": config_data.get("neo4j", {}),
        "postgres": config_data.get("postgres", {}),
        "embedding": config_data.get("embedding", {}),
        "rerank": config_data.get("rerank", {}),
        "filesystem": config_data.get("filesystem", {}),
        "redis": config_data.get("redis", {}),
        "runtime": config_data.get("runtime", {})
    }

def escape_like_pattern(s: str) -> str:
    return s.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

def config_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes", "on")
    return bool(value)

def config_mode(value: Any, default: str = "auto") -> str:
    if value is None:
        return default
    if isinstance(value, bool):
        return "on" if value else "off"
    value = str(value).strip().lower()
    if value in ("1", "true", "yes", "on", "always"):
        return "on"
    if value in ("0", "false", "no", "off", "never", "disabled"):
        return "off"
    return value or default

class KnowledgePipeline:
    def __init__(self, config: dict, concurrency: int = 3, batch_size: int = 5, flush_timeout: int = 60):
        self.cfg = config
        self.llm_cfg = config["llm"].copy()
        embed_cfg = config.get("embedding", {})
        fs_cfg = config.get("filesystem", {})
        runtime_cfg = config.get("runtime", {})
        self.concurrency = concurrency
        self.batch_size = batch_size
        self.flush_timeout = flush_timeout
        memory_cfg = config.get("memory", {})
        self.llm_cache_enabled = config_bool(runtime_cfg.get("llm_cache_enabled", True), True)
        self.llm_cache_ttl_seconds = int(runtime_cfg.get("llm_cache_ttl_seconds", 3600))
        self.llm_max_tokens = int(runtime_cfg.get("dehydrator_llm_max_tokens", 4096))
        self.llm_temperature = float(runtime_cfg.get("dehydrator_llm_temperature", 0.1))
        self.amem_enabled = config_bool(runtime_cfg.get("amem_enabled", memory_cfg.get("amem_enabled", True)), True)
        self.amem_note_card_enabled = config_bool(
            runtime_cfg.get("amem_note_card_enabled", memory_cfg.get("note_card_enabled", True)),
            True
        )
        self.amem_link_generation_mode = config_mode(
            runtime_cfg.get("amem_link_generation_mode", memory_cfg.get("link_generation_mode", "auto")),
            "auto"
        )
        self.amem_link_generation_enabled = self.amem_link_generation_mode != "off"
        self.amem_evolution_mode = config_mode(
            runtime_cfg.get("amem_evolution_mode", memory_cfg.get("amem_evolution_mode", "auto")),
            "auto"
        )
        self.amem_evolution_enabled = self.amem_evolution_mode != "off"
        self.amem_link_top_k = int(runtime_cfg.get("amem_link_top_k", memory_cfg.get("link_top_k", 3)))
        self.amem_link_confidence_threshold = float(
            runtime_cfg.get("amem_link_confidence_threshold", memory_cfg.get("link_confidence_threshold", 0.72))
        )
        self.amem_min_neighbor_similarity = float(
            runtime_cfg.get("amem_min_neighbor_similarity", memory_cfg.get("min_neighbor_similarity", 0.72))
        )
        self.memory_outbox_max_attempts = int(runtime_cfg.get("memory_outbox_max_attempts", 5))
        self.task_queue = asyncio.Queue()
        self.result_queue = asyncio.Queue()
        self._workers = []
        self._markdown_lock = asyncio.Lock()
        self._buffer_lock = asyncio.Lock()
        self.neo4j_driver = None
        self.pg_pool = None
        self.memory_db = None
        self.embed_path = embed_cfg.get("model_path", "")
        self.ledger_path = fs_cfg.get("ledger_path", "MEMORY_LEDGER.md")
        self._message_buffers: Dict[str, List[Tuple[str, str, str]]] = {}
        self._flush_tasks: Dict[str, asyncio.Task] = {}
        self.redis_client = None
        self.use_redis = False
        self.redis_cfg = config.get("redis", {})
        self.task_queue_key = "knowledge_pipeline:tasks"
        self.redis_blpop_timeout = int(self.redis_cfg.get("blpop_timeout", 5))
        if self.embed_path:
            try:
                self.emb_model = SentenceTransformer(self.embed_path)
                self.embed_dim = self.emb_model.get_sentence_embedding_dimension()
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                self.emb_model = None
                self.embed_dim = embed_cfg.get("dim", 1024)
        else:
            self.emb_model = None
            self.embed_dim = embed_cfg.get("dim", 1024)

    async def __aenter__(self):
        self.model = self.llm_cfg.pop("model", "gpt-4o-mini")
        self.extra_params = self.llm_cfg.pop("extra_params", {})
        if not str(self.llm_cfg.get("api_key") or "").strip():
            self.llm_cfg["api_key"] = "missing-api-key"
        self.client = AsyncOpenAI(**self.llm_cfg)
        await self._init_neo4j()
        await self._init_postgres()
        await self._init_redis()
        try:
            from memory import MemoryDatabases
            self.memory_db = MemoryDatabases(self.cfg)
            await self.memory_db.connect_pg()
        except ImportError:
            logger.warning("MemoryDatabases module not found, context retrieval disabled.")
            self.memory_db = None
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        for task in self._flush_tasks.values():
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await self._flush_all_buffers()
        if not self.use_redis:
            try:
                await asyncio.wait_for(self.task_queue.join(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Queue not empty after 10s, forcing worker cancellation.")
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        await self.client.close()
        if self.neo4j_driver:
            await self.neo4j_driver.close()
        if self.pg_pool:
            await self.pg_pool.close()
        if self.memory_db:
            await self.memory_db.close()
        if self.redis_client:
            if hasattr(self.redis_client, "aclose"):
                await self.redis_client.aclose()
            else:
                await self.redis_client.close()

    async def _init_neo4j(self):
        try:
            neo4j_cfg = self.cfg.get("neo4j", {})
            self.neo4j_driver = AsyncGraphDatabase.driver(
                neo4j_cfg.get("uri", "bolt://localhost:7687"),
                auth=(neo4j_cfg.get("user", "neo4j"), neo4j_cfg.get("password", "root")),
                max_connection_lifetime=3600,
                max_connection_pool_size=self.concurrency * 2
            )
            async with self.neo4j_driver.session() as session:
                await session.run("RETURN 1")
            logger.info("Neo4j connected.")
        except Exception as e:
            logger.error(f"Neo4j connection failed: {e}")
            self.neo4j_driver = None

    async def _init_postgres(self):
        try:
            pg_cfg = self.cfg.get("postgres", {})
            pg_dsn = pg_cfg.get("dsn")
            if not pg_dsn:
                pg_dsn = f"postgresql://{pg_cfg.get('user', 'postgres')}:{pg_cfg.get('password', 'password')}@{pg_cfg.get('host', 'localhost')}:{pg_cfg.get('port', 5432)}/{pg_cfg.get('database', 'postgres')}"
            self.pg_pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=self.concurrency * 2)
            async with self.pg_pool.acquire() as conn:
                await conn.execute('CREATE EXTENSION IF NOT EXISTS vector;')
                check_sql = "SELECT column_name FROM information_schema.columns WHERE table_name='episodic_memory' AND column_name='scope';"
                row = await conn.fetchrow(check_sql)
                if not row:
                    logger.warning("Table schema mismatch detected. Re-creating table episodic_memory...")
                    await conn.execute('DROP TABLE IF EXISTS episodic_memory;')
                await conn.execute(f'''
                    CREATE TABLE IF NOT EXISTS episodic_memory (
                        id VARCHAR PRIMARY KEY,
                        text TEXT,
                        embedding vector({self.embed_dim}),
                        owner_id VARCHAR,
                        scope VARCHAR DEFAULT 'shared',
                        session_id VARCHAR,
                        metadata JSONB DEFAULT '{{}}'::jsonb,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                ''')
                await conn.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS session_id VARCHAR;")
                await conn.execute("ALTER TABLE episodic_memory ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;")
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS topic_index (
                        id SERIAL PRIMARY KEY,
                        entity VARCHAR(255),
                        topic VARCHAR(255),
                        episodic_id VARCHAR(255),
                        text TEXT,
                        session_id VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS memory_links (
                        source_id VARCHAR NOT NULL,
                        target_id VARCHAR NOT NULL,
                        relation VARCHAR DEFAULT 'related',
                        confidence DOUBLE PRECISION DEFAULT 0.0,
                        reason TEXT,
                        metadata JSONB DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (source_id, target_id, relation)
                    );
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS memory_evolution_log (
                        id VARCHAR PRIMARY KEY,
                        source_id VARCHAR NOT NULL,
                        target_id VARCHAR,
                        action VARCHAR,
                        reason TEXT,
                        suggested_metadata JSONB DEFAULT '{}'::jsonb,
                        applied BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS memory_outbox (
                        id VARCHAR PRIMARY KEY,
                        event_type VARCHAR NOT NULL,
                        dialogue_id VARCHAR,
                        message_id VARCHAR,
                        payload JSONB NOT NULL,
                        status VARCHAR DEFAULT 'pending',
                        attempts INTEGER DEFAULT 0,
                        last_error TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                ''')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_entity ON topic_index(entity);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_topic ON topic_index(topic);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_session ON topic_index(session_id);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_links_source ON memory_links(source_id);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_id);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_links_confidence ON memory_links(confidence);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_evolution_source ON memory_evolution_log(source_id);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_evolution_target ON memory_evolution_log(target_id);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_outbox_status ON memory_outbox(status);')
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_outbox_dialogue ON memory_outbox(dialogue_id);')
            logger.info("PostgreSQL connected.")
        except Exception as e:
            logger.error(f"PostgreSQL connection failed: {e}")
            self.pg_pool = None

    async def _init_redis(self):
        if not self.redis_cfg:
            logger.info("No Redis config found, using in-memory queue.")
            return
        try:
            self.redis_client = aioredis.Redis(
                host=self.redis_cfg.get("host", "localhost"),
                port=self.redis_cfg.get("port", 6379),
                password=self.redis_cfg.get("password") or None,
                db=self.redis_cfg.get("db", 0),
                decode_responses=True,
                socket_connect_timeout=float(self.redis_cfg.get("socket_connect_timeout", 3)),
                socket_timeout=float(self.redis_cfg.get("socket_timeout", max(self.redis_blpop_timeout + 5, 10))),
                health_check_interval=int(self.redis_cfg.get("health_check_interval", 30)),
            )
            await self.redis_client.ping()
            self.use_redis = True
            logger.info("Redis connected, persistent queue enabled.")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, falling back to memory queue.")
            self.redis_client = None
            self.use_redis = False

    @retry(stop=stop_after_attempt(5), wait=wait_random_exponential(min=3, max=20), retry=retry_if_exception_type(RETRY_EXC))
    async def _call_llm(self, system_prompt: str, user_prompt: str, response_model=None) -> Any:
        cache_key = None
        if self.use_redis and self.llm_cache_enabled and self.llm_cache_ttl_seconds > 0:
            raw_key = f"{system_prompt}|||{user_prompt}"
            cache_key = "llm:cache:" + hashlib.sha256(raw_key.encode()).hexdigest()
            cached = await self.redis_client.get(cache_key)
            if cached:
                try:
                    logger.debug("LLM cache hit")
                    data = json.loads(cached)
                    return response_model(**data) if response_model else data
                except Exception:
                    logger.warning("Corrupted LLM cache entry ignored.")
                    await self.redis_client.delete(cache_key)
        call_params = self.extra_params.copy()
        call_params["max_tokens"] = self.llm_max_tokens
        call_params["temperature"] = self.llm_temperature
        if response_model:
            res = await self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                response_format=response_model,
                **call_params
            )
            parsed_obj = res.choices[0].message.parsed
            data = parsed_obj.model_dump() if hasattr(parsed_obj, "model_dump") else parsed_obj.dict()
            result_obj = parsed_obj
        else:
            res = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"},
                **call_params
            )
            content = res.choices[0].message.content.strip()
            if content.startswith("```json"):
                content = content[7:]
            elif content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            data = json.loads(content.strip())
            result_obj = data
        if self.use_redis and cache_key and self.llm_cache_enabled and self.llm_cache_ttl_seconds > 0:
            try:
                await self.redis_client.setex(cache_key, self.llm_cache_ttl_seconds, json.dumps(data, ensure_ascii=False))
            except Exception as e:
                logger.warning(f"Failed to cache LLM result: {e}")
        return result_obj

    async def _write_to_markdown_ledger(self, state: PipelineState):
        async with self._markdown_lock:
            def write_file():
                with open(self.ledger_path, "a", encoding="utf-8") as f:
                    f.write(f"\n## 记录时间/会话: {state.dialogue_id}\n")
                    if state.core_sentences:
                        f.write("### 核心事实\n")
                        for sentence in state.core_sentences:
                            f.write(f"- {sentence}\n")
                    if state.extracted_graph and state.extracted_graph.edges:
                        f.write("### 实体关系图谱\n")
                        for edge in state.extracted_graph.edges:
                            f.write(f"- [{edge.source}] --({edge.type})--> [{edge.target}]\n")
                    if state.decision_point and state.decision_point.has_shift:
                        f.write("### 状态转变\n")
                        f.write(f"- {state.decision_point.before_state} -> {state.decision_point.after_state}\n")
            await asyncio.to_thread(write_file)

    async def _resolve_references(self, raw_text: str) -> Tuple[str, Dict[str, str]]:
        prompt = '请对以下文本进行指代消解，将代词替换为对应的实体。严格返回 JSON: {"resolved_text": "消解后的文本", "map": {"代词": "实体"}}。'
        data = await self._call_llm(prompt, raw_text)
        return data.get("resolved_text", raw_text), data.get("map", {})

    async def _extract_topics(self, sentences: List[str]) -> List[str]:
        if not sentences:
            return []
        prompt = '提取核心话题（短语表示），最多5个。返回 JSON: {"topics": ["话题1", "话题2"]}'
        data = await self._call_llm(prompt, "\n".join(sentences))
        return data.get("topics", [])

    async def _link_entities(self, entities: List[str]) -> List[str]:
        if not self.neo4j_driver:
            return entities
        linked = []
        for ent in entities:
            try:
                async with self.neo4j_driver.session() as session:
                    result = await session.run(
                        """
                        MATCH (n)
                        WHERE any(key IN keys(n) 
                                  WHERE key IN ['id', 'name'] 
                                  AND toLower(toString(n[key])) CONTAINS toLower($ent))
                        RETURN n.id AS id, n.name AS name
                        LIMIT 1
                        """,
                        ent=ent
                    )
                    record = await result.single()
                    if record:
                        matched = record.get("id") or record.get("name")
                        linked.append(matched if matched else ent)
                    else:
                        linked.append(ent)
            except Exception as e:
                logger.warning(f"Entity linking failed for '{ent}': {e}")
                linked.append(ent)
        return linked

    async def _retrieve_context(self, entities: List[str], resolved_text: str, owner_id: str) -> str:
        if not entities or not self.memory_db or not self.memory_db.pg_pool or not self.emb_model:
            return ""
        episodic_texts = []
        semantic_texts = []
        for ent in entities[:3]:
            try:
                emb_vec = (await asyncio.to_thread(self.emb_model.encode, [ent]))[0].tolist()
                episodic = await self.memory_db.get_episodic_decay(emb_vec, owner_id, limit=3)
                if episodic:
                    episodic_texts.extend([e for e in episodic if isinstance(e, str)])
            except Exception as e:
                logger.warning(f"Episodic retrieval failed for '{ent}': {e}")
        try:
            semantic = await self.memory_db.get_semantic_graph(entities[:3])
            if semantic:
                semantic_texts = [s for s in semantic if isinstance(s, str)]
        except Exception as e:
            logger.warning(f"Semantic retrieval failed: {e}")
        all_texts = list(set(episodic_texts + semantic_texts))
        if all_texts:
            return "【相关历史记忆】\n" + "\n".join(f"- {t}" for t in all_texts[:5]) + "\n"
        return ""

    async def _construct_memory_card(self, state: PipelineState) -> Dict[str, Any]:
        fallback = {
            "keywords": list(dict.fromkeys((state.entities or []) + (state.topics or [])))[:12],
            "tags": (state.topics or [])[:8],
            "context": (state.resolved_text or state.raw_text or "")[:300]
        }
        if not self.amem_enabled or not self.amem_note_card_enabled:
            return fallback
        prompt = (
            "Build an A-MEM style memory note card. Return JSON only: "
            "{\"keywords\": [\"...\"], \"tags\": [\"...\"], \"context\": \"one compact sentence\"}. "
            "Keywords should be concrete concepts, tools, people, projects, or methods. "
            "Tags should be stable categories. Keep context factual and non-speculative."
        )
        user_prompt = json.dumps(
            {
                "resolved_text": state.resolved_text,
                "core_sentences": state.core_sentences,
                "entities": state.entities,
                "topics": state.topics
            },
            ensure_ascii=False
        )
        try:
            data = await self._call_llm(prompt, user_prompt)
            keywords = data.get("keywords", []) if isinstance(data, dict) else []
            tags = data.get("tags", []) if isinstance(data, dict) else []
            context = data.get("context", "") if isinstance(data, dict) else ""
            return {
                "keywords": [str(x).strip() for x in keywords if str(x).strip()][:12] or fallback["keywords"],
                "tags": [str(x).strip() for x in tags if str(x).strip()][:8] or fallback["tags"],
                "context": str(context or fallback["context"])[:500]
            }
        except Exception as e:
            logger.warning(f"A-MEM note construction failed for {state.dialogue_id}: {e}")
            return fallback

    def _memory_embedding_text(self, sentence: str, state: PipelineState) -> str:
        card = state.memory_card or {}
        keywords = ", ".join([str(x) for x in card.get("keywords", [])])
        tags = ", ".join([str(x) for x in card.get("tags", [])])
        context = str(card.get("context", ""))
        return f"{sentence}\nKeywords: {keywords}\nTags: {tags}\nContext: {context}"

    def _should_generate_memory_links(self, state: PipelineState) -> bool:
        if not self.amem_enabled or not self.amem_link_generation_enabled:
            return False
        if self.amem_link_generation_mode == "on":
            return True
        if not state.core_sentences:
            return False
        text = f"{state.resolved_text}\n{' '.join(state.core_sentences)}".lower()
        if len(text) < 80:
            return False
        structural_markers = [
            "因为", "导致", "但是", "相比", "关联", "依赖", "改进", "演化", "矛盾",
            "causes", "because", "depends", "related", "contradict", "improve"
        ]
        if any(marker in text for marker in structural_markers):
            return True
        if len(state.core_sentences) >= 3:
            return True
        if len(set((state.entities or []) + (state.topics or []))) >= 4:
            return True
        card = state.memory_card or {}
        return len(card.get("keywords", []) or []) >= 5

    async def _find_neighbor_memories(self, conn, embedding: List[float], owner_id: str, exclude_prefix: str) -> List[Dict[str, Any]]:
        if not self.amem_enabled or not self.amem_link_generation_enabled or self.amem_link_top_k <= 0:
            return []
        max_distance = max(0.0, min(1.0, 1.0 - self.amem_min_neighbor_similarity))
        try:
            rows = await conn.fetch(
                """
                SELECT id, text, metadata, (embedding <=> $1) AS distance
                FROM episodic_memory
                WHERE id NOT LIKE $2 AND (scope = 'shared' OR owner_id = $3)
                  AND (embedding <=> $1) <= $5
                ORDER BY embedding <=> $1
                LIMIT $4
                """,
                embedding,
                f"{exclude_prefix}%",
                owner_id,
                self.amem_link_top_k,
                max_distance
            )
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"A-MEM neighbor search failed: {e}")
            return []

    async def _analyze_memory_links(
        self,
        state: PipelineState,
        source_id: str,
        sentence: str,
        neighbors: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        if not self.amem_enabled or not self.amem_link_generation_enabled or not neighbors:
            return [], []
        candidate_payload = [
            {
                "id": str(n.get("id")),
                "text": str(n.get("text", ""))[:500],
                "metadata": n.get("metadata") or {},
                "distance": float(n.get("distance") or 0.0)
            }
            for n in neighbors
        ]
        prompt = (
            "You link A-MEM note cards. Return JSON only with this schema: "
            "{\"links\": [{\"target_id\": string, \"relation\": string, \"confidence\": number, \"reason\": string}], "
            "\"evolution_actions\": [{\"target_id\": string, \"action\": string, \"reason\": string, \"suggested_metadata\": object}]}. "
            "Only use target_id values from candidates. Allowed relations: similar, elaborates, supports, contradicts, causes, precedes, part_of. "
            "Allowed actions: strengthen, update_neighbor, merge, prune, none. Evolution actions are audit suggestions only; do not invent facts."
        )
        user_prompt = json.dumps(
            {
                "new_memory": {"id": source_id, "text": sentence, "card": state.memory_card},
                "candidates": candidate_payload
            },
            ensure_ascii=False
        )
        try:
            data = await self._call_llm(prompt, user_prompt)
        except Exception as e:
            logger.warning(f"A-MEM link analysis failed for {source_id}: {e}")
            return [], []
        candidate_ids = {str(n.get("id")) for n in neighbors}
        links = []
        for raw in (data.get("links", []) if isinstance(data, dict) else []):
            target_id = str(raw.get("target_id", "")).strip()
            if target_id not in candidate_ids:
                continue
            try:
                confidence = float(raw.get("confidence", 0.0))
            except Exception:
                confidence = 0.0
            if confidence < self.amem_link_confidence_threshold:
                continue
            relation = re.sub(r"[^a-zA-Z0-9_]", "_", str(raw.get("relation") or "related"))[:40] or "related"
            links.append({
                "source_id": source_id,
                "target_id": target_id,
                "relation": relation,
                "confidence": max(0.0, min(confidence, 1.0)),
                "reason": str(raw.get("reason", ""))[:500]
            })
        evolutions = []
        if self.amem_evolution_enabled and self.amem_evolution_mode != "off":
            for raw in (data.get("evolution_actions", []) if isinstance(data, dict) else []):
                target_id = str(raw.get("target_id", "")).strip()
                action = re.sub(r"[^a-zA-Z0-9_]", "_", str(raw.get("action") or "none"))[:40] or "none"
                if target_id not in candidate_ids or action == "none":
                    continue
                evo_payload = f"{source_id}:{target_id}:{action}:{raw.get('reason', '')}"
                evolutions.append({
                    "id": f"evo_{hashlib.sha256(evo_payload.encode('utf-8')).hexdigest()[:24]}",
                    "source_id": source_id,
                    "target_id": target_id,
                    "action": action,
                    "reason": str(raw.get("reason", ""))[:800],
                    "suggested_metadata": raw.get("suggested_metadata") if isinstance(raw.get("suggested_metadata"), dict) else {}
                })
        return links, evolutions

    async def _process_state(self, state: PipelineState) -> PipelineState:
        try:
            if len(state.raw_text) > 1500:
                logger.info(f"Truncating long text for {state.dialogue_id}")
                state.raw_text = state.raw_text[:1500] + "...[内容已截断]"
            state.current_stage = PipelineStage.RESOLUTION
            resolved_text, resolution_map = await self._resolve_references(state.raw_text)
            state.resolved_text = resolved_text
            state.resolution_map = resolution_map
            state.current_stage = PipelineStage.DEHYDRATION
            dehydrate_prompt = '提取客观事实，拆分为极简的单句，最多保留5句核心。严格返回 JSON: {"sentences": ["事实1", "事实2"]}'
            res = await self._call_llm(dehydrate_prompt, state.resolved_text)
            state.core_sentences = res.get("sentences", [])
            if not state.core_sentences:
                state.current_stage = PipelineStage.COMPLETED
                return state
            state.current_stage = PipelineStage.TRACKING
            dp_prompt = "分析状态转变。若无转变 has_shift=false。"
            state.decision_point = await self._call_llm(dp_prompt, "\n".join(state.core_sentences), DecisionPoint)
            state.current_stage = PipelineStage.EXTRACTION
            sys_prompt = (
    "Extract a knowledge graph from the text using ONLY the following ontology types.\n\n"
    "NODE TYPES (use exactly these IDs):\n"
    "  entity - A named concept, person, tool, paper, file, model, or object.\n"
    "  memory - An episodic long-term memory item.\n"
    "  topic - A thematic grouping.\n"
    "  claim - A conclusion, judgment, or distilled statement.\n"
    "  evidence - A source, result, citation, log, or verification artifact.\n"
    "  artifact - A generated file, answer, video, document, or code change.\n\n"
    "EDGE TYPES (use exactly these IDs):\n"
    "  mentions - memory -> entity (a memory mentions an entity)\n"
    "  topic - memory -> topic (a memory belongs to a topic)\n"
    "  related - general associative link between any nodes\n"
    "  produces - any node -> artifact\n"
    "  uses - any node uses a skill, tool, or evidence\n"
    "  verified_by - claim -> evidence\n"
    "  supports - memory -> claim (memory supports a claim)\n"
    "  contradicts - memory -> claim (memory contradicts a claim)\n"
    "  part_of - memory -> memory (part-whole relationship)\n"
    "  causes - memory -> memory (causal relationship)\n\n"
    "RULES:\n"
    "1. Node IDs must use the format kind:label (e.g., entity:Python, memory:user_asked_about_X).\n"
    "2. Use ONLY the node types and edge types listed above.\n"
    "3. Maximum 10 edges.\n"
    "4. Each node must have a descriptive label property.\n"
    "Return JSON with nodes and edges arrays."
)
            entities_data = await self._call_llm('提取最多5个核心实体，返回 JSON: {"entities": ["A", "B"]}', state.resolved_text)
            linked_entities = await self._link_entities(entities_data.get("entities", []))
            context_knowledge = await self._retrieve_context(linked_entities, state.resolved_text, state.owner_id)
            user_prompt = f"Text: {state.resolved_text}\nCore: {state.core_sentences}\n{context_knowledge}\nLinked: {linked_entities}"
            state.extracted_graph = await self._call_llm(sys_prompt, user_prompt, GraphExtraction)
            if state.extracted_graph:
                state.entities = [node.id for node in state.extracted_graph.nodes]
            state.topics = await self._extract_topics(state.core_sentences)
            state.memory_card = await self._construct_memory_card(state)
            pg_ok = await self._write_to_pg(state)
            if pg_ok:
                await asyncio.gather(
                    self._drain_memory_outbox(limit=10),
                    self._write_to_markdown_ledger(state)
                )
            else:
                await asyncio.gather(
                    self._write_to_neo4j(state),
                    self._write_to_markdown_ledger(state)
                )
            state.current_stage = PipelineStage.COMPLETED
        except Exception as e:
            state.error_msg = str(e)
            state.current_stage = PipelineStage.FAILED
            logger.error(f"Pipeline processing failed for {state.dialogue_id}: {e}", exc_info=True)
        return state

    def _build_graph_outbox_payload(self, state: PipelineState) -> Dict[str, Any]:
        graph = state.extracted_graph
        card = state.memory_card or {}
        nodes = []
        edges = []
        if graph:
            nodes = [
                {
                    "id": n.id,
                    "label": n.label,
                    "props": n.properties
                }
                for n in graph.nodes
            ]
            edges = [
                {
                    "src": e.source,
                    "tgt": e.target,
                    "type": e.type,
                    "props": e.properties
                }
                for e in graph.edges
            ]
        return {
            "event_type": "neo4j_memory_graph_upsert",
            "dialogue_id": state.dialogue_id,
            "message_id": state.message_id,
            "owner_id": state.owner_id,
            "scope": state.scope,
            "nodes": nodes,
            "edges": edges,
            "core_sentences": list(state.core_sentences),
            "entities": list(state.entities),
            "memory_card": {
                "keywords": card.get("keywords", []),
                "tags": card.get("tags", []),
                "context": card.get("context", "")
            }
        }

    async def _write_graph_payload_to_neo4j(self, payload: Dict[str, Any]):
        """Write graph payload to Neo4j using ontology-aligned methods.

        Node IDs use canonical kind:sha1_12 format. Nodes get proper
        Neo4j labels from the ontology. Edges use ontology-defined
        relation types in lowercase.
        """
        if not self.neo4j_driver:
            raise RuntimeError("Neo4j unavailable")
        try:
            from memory_ontology import ontology_node_id

            def _parse_node_id(raw_id):
                if ':' in str(raw_id):
                    parts = str(raw_id).split(':', 1)
                    kind = parts[0].strip().lower()
                    label = parts[1].strip()
                    valid = {
                        'entity', 'memory', 'topic', 'claim', 'evidence',
                        'artifact', 'skill', 'tool', 'paper', 'author',
                        'venue', 'project', 'session', 'decision', 'alternative',
                        'rag_entity', 'document', 'community', 'memory_card',
                        'owner', 'scope', 'hyperedge', 'literature_review',
                        'option',
                    }
                    return (kind if kind in valid else 'entity', label)
                return ('entity', str(raw_id))

            neo_labels = {
                "entity": "Entity", "memory": "Memory", "topic": "Topic",
                "claim": "Claim", "evidence": "Evidence", "artifact": "Artifact",
                "skill": "Skill", "tool": "Tool", "paper": "Paper",
                "author": "Author", "venue": "Venue", "project": "Project",
                "session": "Session", "decision": "Decision",
                "alternative": "Alternative", "rag_entity": "RAGEntity",
                "document": "Document", "community": "Community",
                "memory_card": "MemoryCard", "owner": "Owner",
                "scope": "Scope", "hyperedge": "HyperEdge",
                "literature_review": "LiteratureReview",
                "option": "Option",
            }

            type_map = {
                "mentions": "mentions", "mention": "mentions",
                "topic": "topic", "related": "related",
                "produces": "produces", "produce": "produces",
                "uses": "uses", "use": "uses",
                "verified_by": "verified_by", "verifies": "verified_by",
                "supports": "supports", "support": "supports",
                "contradicts": "contradicts", "contradict": "contradicts",
                "causes": "causes", "cause": "causes",
                "part_of": "part_of", "partof": "part_of",
                "similar_to": "similar_to", "similar": "similar_to",
                "elaborates": "elaborates", "elaborate": "elaborates",
                "precedes": "precedes", "precede": "precedes",
                "belongs_to": "belongs_to", "belongsto": "belongs_to",
            }

            async with self.neo4j_driver.session() as session:
                # Write nodes
                for n in payload.get("nodes", []):
                    raw_id = n.get("id", "")
                    if not raw_id:
                        continue
                    kind, label = _parse_node_id(raw_id)
                    node_id = ontology_node_id(kind, label)
                    props = dict(n.get("props", {}))
                    props.setdefault("label", label)
                    props.setdefault("kind", kind)
                    props.setdefault("source", "dehydrator")
                    props.setdefault("dialogue_id", payload.get("dialogue_id", ""))
                    props.setdefault("owner_id", payload.get("owner_id", ""))
                    neo_label = neo_labels.get(kind, "OntologyNode")
                    try:
                        await session.run(
                            f"MERGE (n:{neo_label}:OntologyNode {{id: $id}}) "
                            f"ON CREATE SET n = $props ON MATCH SET n += $props",
                            id=node_id, props=props,
                        )
                    except Exception as e:
                        logger.warning(f"Node merge failed for {node_id}: {e}")

                # Write edges
                for e in payload.get("edges", []):
                    src_raw = e.get("src", "")
                    tgt_raw = e.get("tgt", "")
                    if not src_raw or not tgt_raw:
                        continue
                    src_kind, src_label = _parse_node_id(src_raw)
                    tgt_kind, tgt_label = _parse_node_id(tgt_raw)
                    src_id = ontology_node_id(src_kind, src_label)
                    tgt_id = ontology_node_id(tgt_kind, tgt_label)
                    raw_type = str(e.get("type", "related")).strip().lower()
                    rel_type = type_map.get(raw_type, "related")
                    props = dict(e.get("props", {}))
                    props.setdefault("dialogue_id", payload.get("dialogue_id", ""))
                    props.setdefault("owner_id", payload.get("owner_id", ""))
                    try:
                        await session.run(
                            f"MATCH (a:OntologyNode {{id: $src_id}}) "
                            f"MATCH (b:OntologyNode {{id: $tgt_id}}) "
                            f"MERGE (a)-[r:{rel_type}]->(b) "
                            f"ON CREATE SET r = $props ON MATCH SET r += $props",
                            src_id=src_id, tgt_id=tgt_id, props=props,
                        )
                    except Exception as e:
                        logger.warning(f"Edge merge failed {src_id}->{tgt_id}: {e}")

                # Memory cards → ontology memory nodes
                card = payload.get("memory_card") or {}
                for i, sentence in enumerate(payload.get("core_sentences") or []):
                    memory_id = ontology_node_id(
                        "memory", f"{payload.get('dialogue_id', '')}_{i}"
                    )
                    try:
                        await session.run(
                            """
                            MERGE (m:Memory:OntologyNode {id: $id})
                            SET m.kind = 'memory',
                                m.label = $text,
                                m.text = $text,
                                m.dialogue_id = $dialogue_id,
                                m.owner_id = $owner_id,
                                m.scope = $scope,
                                m.keywords = $keywords,
                                m.tags = $tags,
                                m.context = $context,
                                m.source = 'dehydrator'
                            """,
                            id=memory_id, text=sentence,
                            dialogue_id=payload.get("dialogue_id"),
                            owner_id=payload.get("owner_id"),
                            scope=payload.get("scope"),
                            keywords=card.get("keywords", []),
                            tags=card.get("tags", []),
                            context=card.get("context", ""),
                        )
                        for entity_raw in list(payload.get("entities") or [])[:8]:
                            e_kind, e_label = _parse_node_id(str(entity_raw))
                            entity_id = ontology_node_id(e_kind, e_label)
                            try:
                                await session.run(
                                    """
                                    MATCH (m:OntologyNode {id: $memory_id})
                                    MERGE (e:Entity:OntologyNode {id: $entity_id})
                                    ON CREATE SET e.kind = 'entity', e.label = $entity_label, e.source = 'dehydrator'
                                    MERGE (m)-[r:mentions]->(e)
                                    ON CREATE SET r.dialogue_id = $dialogue_id
                                    ON MATCH SET r.dialogue_id = $dialogue_id
                                    """,
                                    memory_id=memory_id, entity_id=entity_id,
                                    entity_label=e_label,
                                    dialogue_id=payload.get("dialogue_id"),
                                )
                            except Exception as ex:
                                logger.debug(f"Mentions edge failed {memory_id}->{entity_id}: {ex}")
                    except Exception as e:
                        logger.warning(f"Memory node merge failed for {memory_id}: {e}")
        except Exception as e:
            logger.error(f"Neo4j write failed: {e}", exc_info=True)
            raise


    async def _write_to_neo4j(self, state: PipelineState):
        if not self.neo4j_driver or (not state.extracted_graph and not state.core_sentences):
            return
        payload = self._build_graph_outbox_payload(state)
        await self._write_graph_payload_to_neo4j(payload)

    async def _drain_memory_outbox(self, limit: int = 10):
        if not self.pg_pool or not self.neo4j_driver:
            return
        try:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, payload, attempts
                    FROM memory_outbox
                    WHERE event_type = 'neo4j_memory_graph_upsert'
                      AND status IN ('pending', 'failed')
                      AND attempts < $1
                    ORDER BY created_at
                    LIMIT $2
                    """,
                    self.memory_outbox_max_attempts,
                    limit
                )
            for row in rows:
                payload = row["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                try:
                    await self._write_graph_payload_to_neo4j(payload)
                    async with self.pg_pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE memory_outbox
                            SET status = 'done', last_error = NULL, updated_at = NOW()
                            WHERE id = $1
                            """,
                            row["id"]
                        )
                except Exception as e:
                    async with self.pg_pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE memory_outbox
                            SET status = 'failed',
                                attempts = attempts + 1,
                                last_error = $2,
                                updated_at = NOW()
                            WHERE id = $1
                            """,
                            row["id"],
                            str(e)[:1000]
                        )
        except Exception as e:
            logger.error(f"Memory outbox drain failed: {e}", exc_info=True)

    async def _check_apoc_procedure(self, session) -> bool:
        try:
            result = await session.run("CALL dbms.procedures() YIELD name WHERE name = 'apoc.merge.relationship' RETURN count(*) > 0 AS exists")
            record = await result.single()
            return record["exists"] if record else False
        except Exception:
            return False

    async def _write_to_pg(self, state: PipelineState):
        if not self.pg_pool or not state.core_sentences or not self.emb_model:
            return False
        try:
            rich_texts = [self._memory_embedding_text(sentence, state) for sentence in state.core_sentences]
            embeddings = (await asyncio.to_thread(self.emb_model.encode, rich_texts)).tolist()
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return False
        try:
            async with self.pg_pool.acquire() as conn:
                await register_vector(conn)
                if self._should_generate_memory_links(state):
                    neighbor_sets = [
                        await self._find_neighbor_memories(conn, embeddings[i], state.owner_id, state.message_id)
                        for i in range(len(state.core_sentences))
                    ]
                else:
                    neighbor_sets = [[] for _ in state.core_sentences]
            analysis_tasks = [
                self._analyze_memory_links(state, f"{state.message_id}_{i}", sentence, neighbor_sets[i])
                for i, sentence in enumerate(state.core_sentences)
            ]
            analysis_results = await asyncio.gather(*analysis_tasks, return_exceptions=True)
            prepared_records = []
            all_links = []
            all_evolutions = []
            card = state.memory_card or {}
            decision_data = None
            if state.decision_point:
                decision_data = state.decision_point.model_dump() if hasattr(state.decision_point, "model_dump") else state.decision_point.dict()
            for i, sentence in enumerate(state.core_sentences):
                eid = f"{state.message_id}_{i}"
                result = analysis_results[i]
                if isinstance(result, Exception):
                    logger.warning(f"A-MEM link analysis task failed for {eid}: {result}")
                    links, evolutions = [], []
                else:
                    links, evolutions = result
                state.memory_links[eid] = links
                state.evolution_suggestions.extend(evolutions)
                metadata = {
                    "type": "amem_note",
                    "dialogue_id": state.dialogue_id,
                    "message_id": state.message_id,
                    "fact_index": i,
                    "keywords": card.get("keywords", []),
                    "tags": card.get("tags", []),
                    "context": card.get("context", ""),
                    "entities": state.entities,
                    "topics": state.topics,
                    "decision_point": decision_data,
                    "resolution_map": state.resolution_map,
                    "links": links,
                    "amem": {
                        "note_construction": True,
                        "link_generation": bool(links),
                        "memory_evolution": "suggestion_log"
                    }
                }
                prepared_records.append((eid, sentence, embeddings[i], state.owner_id, state.scope, state.dialogue_id, json.dumps(metadata, ensure_ascii=False)))
                all_links.extend(links)
                all_evolutions.extend(evolutions)
            async with self.pg_pool.acquire() as conn:
                await register_vector(conn)
                async with conn.transaction():
                    prefix = escape_like_pattern(state.message_id)
                    await conn.execute("DELETE FROM memory_links WHERE source_id LIKE $1 ESCAPE '\\' OR target_id LIKE $1 ESCAPE '\\'", f"{prefix}%")
                    await conn.execute("DELETE FROM memory_evolution_log WHERE source_id LIKE $1 ESCAPE '\\' OR target_id LIKE $1 ESCAPE '\\'", f"{prefix}%")
                    await conn.execute("DELETE FROM episodic_memory WHERE id LIKE $1 ESCAPE '\\'", f"{prefix}%")
                    await conn.executemany(
                        """
                        INSERT INTO episodic_memory (id, text, embedding, owner_id, scope, session_id, metadata)
                        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                        """,
                        prepared_records
                    )
                    if all_links:
                        await conn.executemany(
                            """
                            INSERT INTO memory_links (source_id, target_id, relation, confidence, reason, metadata)
                            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                            ON CONFLICT (source_id, target_id, relation) DO UPDATE SET
                                confidence = EXCLUDED.confidence,
                                reason = EXCLUDED.reason,
                                metadata = EXCLUDED.metadata,
                                created_at = NOW()
                            """,
                            [
                                (
                                    link["source_id"],
                                    link["target_id"],
                                    link["relation"],
                                    link["confidence"],
                                    link.get("reason", ""),
                                    json.dumps({"dialogue_id": state.dialogue_id, "message_id": state.message_id}, ensure_ascii=False)
                                )
                                for link in all_links
                            ]
                        )
                    if all_evolutions:
                        await conn.executemany(
                            """
                            INSERT INTO memory_evolution_log (id, source_id, target_id, action, reason, suggested_metadata)
                            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                            ON CONFLICT (id) DO UPDATE SET
                                reason = EXCLUDED.reason,
                                suggested_metadata = EXCLUDED.suggested_metadata,
                                created_at = NOW()
                            """,
                            [
                                (
                                    item["id"],
                                    item["source_id"],
                                    item.get("target_id"),
                                    item.get("action"),
                                    item.get("reason", ""),
                                    json.dumps(item.get("suggested_metadata", {}), ensure_ascii=False)
                                )
                                for item in all_evolutions
                            ]
                        )
                    await conn.execute("DELETE FROM topic_index WHERE session_id = $1", state.dialogue_id)
                    for i, sentence in enumerate(state.core_sentences):
                        eid = f"{state.message_id}_{i}"
                        for entity in state.entities:
                            await conn.execute(
                                "INSERT INTO topic_index (entity, topic, episodic_id, text, session_id) VALUES ($1, $2, $3, $4, $5)",
                                entity, None, eid, sentence, state.dialogue_id
                            )
                        for topic in state.topics:
                            await conn.execute(
                                "INSERT INTO topic_index (entity, topic, episodic_id, text, session_id) VALUES ($1, $2, $3, $4, $5)",
                                None, topic, eid, sentence, state.dialogue_id
                            )
                    graph_payload = self._build_graph_outbox_payload(state)
                    await conn.execute(
                        """
                        INSERT INTO memory_outbox (id, event_type, dialogue_id, message_id, payload, status, attempts, last_error, updated_at)
                        VALUES ($1, 'neo4j_memory_graph_upsert', $2, $3, $4::jsonb, 'pending', 0, NULL, NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            payload = EXCLUDED.payload,
                            status = 'pending',
                            attempts = 0,
                            last_error = NULL,
                            updated_at = NOW()
                        """,
                        f"neo4j:{state.message_id}",
                        state.dialogue_id,
                        state.message_id,
                        json.dumps(graph_payload, ensure_ascii=False)
                    )
            return True
        except Exception as e:
            logger.error(f"PostgreSQL write failed: {e}", exc_info=True)
            return False

    async def _worker_loop(self):
        current_task = None
        try:
            while True:
                try:
                    state = None
                    if self.use_redis:
                        result = await self.redis_client.blpop(self.task_queue_key, timeout=self.redis_blpop_timeout)
                        if result:
                            _, raw_state = result
                            state = PipelineState.parse_raw(raw_state) if hasattr(PipelineState, "parse_raw") else PipelineState.model_validate_json(raw_state)
                    else:
                        state = await self.task_queue.get()
                        current_task = state
                    if state is None:
                        continue
                    processed = await self._process_state(state)
                    await self.result_queue.put(processed)
                    if self.use_redis:
                        dumped_json = processed.json() if hasattr(processed, "json") else processed.model_dump_json()
                        await self.redis_client.rpush("knowledge_pipeline:results", dumped_json)
                    if not self.use_redis:
                        self.task_queue.task_done()
                        current_task = None
                except asyncio.CancelledError:
                    if current_task is not None and not self.use_redis:
                        logger.warning(f"Worker cancelled while processing {current_task.dialogue_id}, requeueing task.")
                        await self.task_queue.put(current_task)
                        current_task = None
                    raise
                except RedisTimeoutError:
                    logger.debug("Redis task queue poll timed out; continuing.")
                    await asyncio.sleep(0.1)
                except RedisConnectionError as e:
                    logger.warning(f"Redis task queue connection issue: {e}; retrying.")
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Worker loop error: {e}", exc_info=True)
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Worker loop exiting due to cancellation.")
            raise

    def start_workers(self):
        self._workers = [asyncio.create_task(self._worker_loop()) for _ in range(self.concurrency)]

    async def process(self, dialog_id: str, text: str, owner_id: str = "SYSTEM", scope: str = "shared"):
        async with self._buffer_lock:
            if dialog_id not in self._message_buffers:
                self._message_buffers[dialog_id] = []
            self._message_buffers[dialog_id].append((text, owner_id, scope))
            if len(self._message_buffers[dialog_id]) >= self.batch_size:
                await self._flush_buffer(dialog_id)
            else:
                if dialog_id not in self._flush_tasks or self._flush_tasks[dialog_id].done():
                    self._flush_tasks[dialog_id] = asyncio.create_task(self._delayed_flush(dialog_id))

    async def _delayed_flush(self, dialog_id: str):
        await asyncio.sleep(self.flush_timeout)
        await self._flush_buffer(dialog_id)

    async def _flush_buffer(self, dialog_id: str):
        async with self._buffer_lock:
            if dialog_id not in self._message_buffers or not self._message_buffers[dialog_id]:
                return
            messages = self._message_buffers[dialog_id]
            self._message_buffers[dialog_id] = []
            if dialog_id in self._flush_tasks:
                self._flush_tasks[dialog_id].cancel()
                del self._flush_tasks[dialog_id]
        combined_text = "\n".join([msg[0] for msg in messages])
        last_owner_id = messages[-1][1]
        last_scope = messages[-1][2]
        logger.info(f"Flushing batch of {len(messages)} messages for {dialog_id}")
        state = PipelineState(dialogue_id=dialog_id, raw_text=combined_text, owner_id=last_owner_id, scope=last_scope)
        if self.use_redis:
            dumped_json = state.json() if hasattr(state, "json") else state.model_dump_json()
            await self.redis_client.rpush(self.task_queue_key, dumped_json)
        else:
            await self.task_queue.put(state)

    async def _flush_all_buffers(self):
        async with self._buffer_lock:
            for dialog_id in list(self._message_buffers.keys()):
                await self._flush_buffer(dialog_id)

async def main():
    try:
        config = load_config("model.toml")
    except Exception as e:
        logger.error(f"Config error: {e}")
        return
    try:
        from memory import MemoryDatabases
        db = MemoryDatabases(config)
        await db.connect_pg()
    except ImportError:
        db = None
    mock_dialogues = [
        {"id": "doctor_A_patient_X", "owner": "Doctor_A", "text": "患者X有高血压病史，建议服用降压药。"},
        {"id": "doctor_B_patient_X", "owner": "Doctor_B", "text": "患者X虽然血压高，但心率过快，建议先控制心率再用药。"},
    ]
    async with KnowledgePipeline(config, concurrency=1, batch_size=5, flush_timeout=5) as pipeline:
        pipeline.start_workers()
        for d in mock_dialogues:
            await pipeline.process(d['id'], d['text'], owner_id=d['owner'], scope="shared")
        await asyncio.sleep(1)
        await pipeline._flush_all_buffers()
        for _ in range(len(mock_dialogues)):
            try:
                state = await asyncio.wait_for(pipeline.result_queue.get(), timeout=30)
                print(f"\n=== {state.dialogue_id} ===")
                print(f"Pipeline Stage: {state.current_stage.value}")
                if state.error_msg:
                    print(f"Error: {state.error_msg}")
                else:
                    for i, sentence in enumerate(state.core_sentences, 1):
                        print(f"  {i}. {sentence}")
                    if state.extracted_graph:
                        print(f"\n[Knowledge Graph] ({len(state.extracted_graph.nodes)} nodes, {len(state.extracted_graph.edges)} edges)")
                print("=" * 50)
            except asyncio.TimeoutError:
                print("Timeout waiting for result.")
    if db:
        await db.close()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
