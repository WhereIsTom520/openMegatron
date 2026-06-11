import os
import json
import hashlib
import asyncio
import logging
import urllib.parse
from typing import Any, Dict, List, Optional
import redis.asyncio as redis
from pathlib import Path
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

class AgentContextManager:
    def __init__(self, config: dict):
        redis_cfg = config.get("redis", {})
        r_host = redis_cfg.get("host", "localhost")
        r_port = redis_cfg.get("port", 6379)
        r_pass = redis_cfg.get("password")
        r_db = redis_cfg.get("db", 0)
        if r_pass:
            r_pass_encoded = urllib.parse.quote_plus(str(r_pass))
            redis_url = f"redis://:{r_pass_encoded}@{r_host}:{r_port}/{r_db}"
        else:
            redis_url = f"redis://{r_host}:{r_port}/{r_db}"
        self.redis = redis.from_url(redis_url, decode_responses=True)

        runtime_cfg = config.get("runtime", {})
        self.ttl_seconds = max(runtime_cfg.get("memory_ttl_days", 30), 1) * 86400

    async def close(self):
        try:
            await self.redis.aclose()
        except Exception as e:
            logger.error(f"Redis close error: {e}", exc_info=True)
            raise

    async def add_history(self, session_id: str, role: str, content: str, max_len: int = 300):
        key = f"agent_history:{session_id}"
        await self.redis.rpush(key, json.dumps({"role": role, "content": content}))
        await self.redis.ltrim(key, -max_len, -1)
        await self.redis.expire(key, self.ttl_seconds)

    async def get_history(self, session_id: str) -> List[dict]:
        items = await self.redis.lrange(f"agent_history:{session_id}", 0, -1)
        return [json.loads(item) for item in items]

    async def clear_history(self, session_id: str):
        await self.redis.delete(f"agent_history:{session_id}")

    async def get_core_memory(self, session_id: str) -> dict:
        data = await self.redis.get(f"core_memory:{session_id}")
        return json.loads(data) if data else {}

    async def update_core_memory(self, session_id: str, updates: dict):
        current = await self.get_core_memory(session_id)
        current.update(updates)
        await self.redis.set(f"core_memory:{session_id}", json.dumps(current, ensure_ascii=False))

    async def push_notification(self, session_id: str, message: str):
        await self.redis.rpush(f"notifications:{session_id}", message)
        await self.redis.expire(f"notifications:{session_id}", self.ttl_seconds)

    async def pop_notifications(self, session_id: str) -> List[str]:
        items = await self.redis.lrange(f"notifications:{session_id}", 0, -1)
        await self.redis.delete(f"notifications:{session_id}")
        return items

    async def record_failure(self, session_id: str, task_type: str, task_key: str, error: str):
        key = f"failure:{session_id}:{task_type}"
        record = {"task_key": task_key, "error": error, "timestamp": time.time()}
        await self.redis.lpush(key, json.dumps(record))
        await self.redis.ltrim(key, 0, 9)
        await self.redis.expire(key, self.ttl_seconds)

    async def record_skill_success(self, task_summary: str, skill_name: str):
        key = "skill_task_mapping"
        mapping_raw = await self.redis.get(key)
        data = json.loads(mapping_raw) if mapping_raw else {}
        task_hash = hashlib.sha256(task_summary.encode()).hexdigest()[:16]
        entry = data.get(task_hash, {"success_count": 0})
        data[task_hash] = {
            "skill": skill_name,
            "task_sample": task_summary[:200],
            "timestamp": time.time(),
            "success_count": entry.get("success_count", 0) + 1
        }
        await self.redis.set(key, json.dumps(data), ex=self.ttl_seconds)

    async def find_matching_skill(self, task_summary: str) -> Optional[str]:
        key = "skill_task_mapping"
        mapping_raw = await self.redis.get(key)
        if not mapping_raw:
            return None
        data = json.loads(mapping_raw)
        task_hash = hashlib.sha256(task_summary.encode()).hexdigest()[:16]
        if task_hash in data:
            return data[task_hash]["skill"]
        for h, info in data.items():
            if info.get("task_sample") and task_summary.startswith(info["task_sample"][:50]):
                return info["skill"]
        return None

    def _tokenize_pattern_text(self, text: str) -> List[str]:
        return re.findall(r'[a-zA-Z0-9_\-\.\/]+|[\u4e00-\u9fff]{1,4}', (text or '').lower())

    async def record_task_pattern(self, pattern: dict):
        if not isinstance(pattern, dict):
            return
        goal_type = str(pattern.get("goal_type") or "general_task").strip() or "general_task"
        chain = pattern.get("successful_chain") or pattern.get("chain") or []
        if not isinstance(chain, list):
            chain = [str(chain)]
        key = "task_patterns"
        base = json.dumps({"goal_type": goal_type, "chain": chain}, ensure_ascii=False, sort_keys=True)
        pattern_id = hashlib.sha256(base.encode()).hexdigest()[:16]
        existing_raw = await self.redis.hget(key, pattern_id)
        now = time.time()
        existing = json.loads(existing_raw) if existing_raw else {}
        examples = list(dict.fromkeys(existing.get("example_user_goals", []) + pattern.get("example_user_goals", [])))[:8]
        merged = {
            "id": pattern_id,
            "goal_type": goal_type,
            "preconditions": pattern.get("preconditions") or existing.get("preconditions", []),
            "successful_chain": chain or existing.get("successful_chain", []),
            "notes": pattern.get("notes") or existing.get("notes", ""),
            "success_count": int(existing.get("success_count", 0)) + int(pattern.get("success_count", 0)),
            "failure_count": int(existing.get("failure_count", 0)) + int(pattern.get("failure_count", 0)),
            "example_user_goals": examples,
            "last_seen": now
        }
        await self.redis.hset(key, pattern_id, json.dumps(merged, ensure_ascii=False))
        await self.redis.expire(key, self.ttl_seconds * 3)

    async def record_tool_reward_stats(self, tool_calls: List[dict], task_reward: dict):
        if not tool_calls:
            return
        key = "tool_reward_stats"
        reward_value = float(task_reward.get("reward", 0.0))
        now = time.time()
        for call in tool_calls:
            tool_name = call.get("skill") or call.get("tool") or "unknown_tool"
            parsed = call.get("parsed_output") if isinstance(call.get("parsed_output"), dict) else {}
            status = str(parsed.get("status") or "").lower()
            success = status not in {"error", "denied"} and not parsed.get("error")
            duration_ms = float(call.get("duration_ms") or 0.0)
            try:
                existing_raw = await self.redis.hget(key, tool_name)
                existing = json.loads(existing_raw) if existing_raw else {}
            except Exception:
                existing = {}
            calls = int(existing.get("calls", 0)) + 1
            successes = int(existing.get("successes", 0)) + (1 if success else 0)
            failures = int(existing.get("failures", 0)) + (0 if success else 1)
            total_duration_ms = float(existing.get("total_duration_ms", 0.0)) + duration_ms
            total_reward = float(existing.get("total_reward", 0.0)) + reward_value
            updated = {
                "tool": tool_name,
                "calls": calls,
                "successes": successes,
                "failures": failures,
                "success_rate": successes / calls if calls else 0.0,
                "avg_duration_ms": total_duration_ms / calls if calls else 0.0,
                "avg_reward": total_reward / calls if calls else 0.0,
                "total_duration_ms": total_duration_ms,
                "total_reward": total_reward,
                "last_status": status or ("success" if success else "unknown"),
                "last_seen": now
            }
            await self.redis.hset(key, tool_name, json.dumps(updated, ensure_ascii=False))
        await self.redis.expire(key, self.ttl_seconds * 3)

    async def get_tool_reward_stats(self, limit: int = 20) -> List[dict]:
        try:
            raw = await self.redis.hgetall("tool_reward_stats")
        except Exception:
            return []
        stats = []
        for value in raw.values():
            try:
                stats.append(json.loads(value))
            except Exception:
                continue
        stats.sort(key=lambda item: (item.get("avg_reward", 0), item.get("success_rate", 0)), reverse=True)
        return stats[:limit]

    async def record_script_skill_candidate(self, candidate: dict):
        if not isinstance(candidate, dict):
            return
        script_hash = str(candidate.get("script_hash") or "").strip()
        if not script_hash:
            return
        session_id = str(candidate.get("session_id") or "default")
        key = "script_skill_candidates"
        latest_key = f"script_skill_latest:{session_id}"
        await self.redis.hset(key, script_hash, json.dumps(candidate, ensure_ascii=False))
        await self.redis.expire(key, self.ttl_seconds * 3)
        await self.redis.set(latest_key, script_hash, ex=self.ttl_seconds * 3)

    async def get_script_skill_candidate(self, script_hash: str = None, session_id: str = "default") -> Optional[dict]:
        key = "script_skill_candidates"
        resolved_hash = (script_hash or "").strip()
        if not resolved_hash:
            resolved_hash = await self.redis.get(f"script_skill_latest:{session_id or 'default'}")
        if not resolved_hash:
            return None
        raw = await self.redis.hget(key, resolved_hash)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        data.setdefault("script_hash", resolved_hash)
        return data

    async def get_task_patterns(self) -> List[dict]:
        raw = await self.redis.hgetall("task_patterns")
        return [json.loads(v) for v in raw.values()]

    async def find_matching_task_patterns(self, query: str, limit: int = 3) -> List[dict]:
        patterns = await self.get_task_patterns()
        if not patterns:
            return []
        stop_tokens = {
            "2024", "2025", "2026", "论文", "文献", "研究", "科研", "综述", "帮我", "一下",
            "以来", "给出", "链接", "paper", "papers", "research", "review", "search", "fetch"
        }
        query_tokens = {tok for tok in self._tokenize_pattern_text(query) if tok not in stop_tokens}
        if not query_tokens:
            return []
        query_text = str(query or "").lower()
        marker_groups = [
            ("人机", "human"),
            ("协同", "collaboration"),
            ("智能体", "agent"),
            ("记忆", "memory"),
            ("信息管理", "information management"),
            ("信息系统", "information systems"),
            ("信管", "mis"),
        ]
        scored = []
        for pattern in patterns:
            text = " ".join([
                str(pattern.get("goal_type", "")),
                str(pattern.get("notes", "")),
                " ".join([str(x) for x in pattern.get("preconditions", [])]),
                " ".join([str(x) for x in pattern.get("successful_chain", [])]),
                " ".join([str(x) for x in pattern.get("example_user_goals", [])])
            ])
            pattern_text = text.lower()
            marker_mismatch = False
            for zh_marker, en_marker in marker_groups:
                query_has_marker = zh_marker in query_text or en_marker in query_text
                pattern_has_marker = zh_marker in pattern_text or en_marker in pattern_text
                if query_has_marker and not pattern_has_marker:
                    marker_mismatch = True
                    break
            if marker_mismatch:
                continue
            pattern_tokens = {tok for tok in self._tokenize_pattern_text(text) if tok not in stop_tokens}
            overlap = len(query_tokens & pattern_tokens)
            overlap_ratio = overlap / max(1, min(len(query_tokens), len(pattern_tokens)))
            if overlap >= 2 and overlap_ratio >= 0.25:
                score = overlap + min(int(pattern.get("success_count", 0)), 5) * 0.25 - min(int(pattern.get("failure_count", 0)), 5) * 0.15
                scored.append((score, pattern))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [pattern for _, pattern in scored[:limit]]

class MemoryService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    async def search(self, query: str, session_id: str) -> dict:
        return await self._agent.execute_memory_search(query, session_id)

    async def memorize(self, fact: str) -> dict:
        return await self._agent.execute_memorize_fact(fact)

    async def amend(self, target_fact: str) -> dict:
        return await self._agent.execute_amend_memory(target_fact)

    async def update_core(self, session_id: str, updates: dict) -> dict:
        return await self._agent.execute_update_core(session_id, updates)

class RuntimeService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    async def run_code(self, filename: str, code: str, session_id: str) -> dict:
        return await self._agent.execute_write_and_run(filename, "", code, session_id)

    async def run_command(self, command: str) -> dict:
        return await self._agent.execute_system_cmd(command)

    def get_cpu_time_limit(self) -> int:
        return getattr(self._agent.runtime, 'cpu_time_limit_sec', 3600)

class ContextService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    async def add_history(self, session_id: str, role: str, content: str):
        await self._agent.ctx.add_history(session_id, role, content)

    async def get_history(self, session_id: str) -> List[dict]:
        return await self._agent.ctx.get_history(session_id)

    async def get_core_memory(self, session_id: str) -> dict:
        return await self._agent.ctx.get_core_memory(session_id)

    async def update_core_memory(self, session_id: str, updates: dict):
        await self._agent.ctx.update_core_memory(session_id, updates)

class SkillsService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    def list_skill_names(self) -> List[str]:
        return list(self._agent.loaded_skills.keys())

    async def reload(self):
        await self._agent._load_skills()

    async def install_from_github(self, repo_url: str, skill_name: str, session_id: str = None):
        return await InstallGithubSkillTool(self._agent).execute(repo_url, skill_name, session_id)

    async def search_market(self, keyword: str):
        return await SearchSkillMarketTool(self._agent).execute(keyword)

    def resolve_skill(self, skill_name: str):
        return RunSkillScriptTool(self._agent)._resolve_skill(skill_name)

    def find_entry_script(self, skill_name: str, skill_info: dict):
        return RunSkillScriptTool(self._agent)._find_entry_script(skill_name, skill_info)

    async def save_skill(self, skill_name: str, description: str, code: str, parameters: dict = None):
        return await SaveAsSkillTool(self._agent).execute(skill_name, description, code, parameters)

    async def promote_script_candidate(self, script_hash: str = None, skill_name: str = None, description: str = None, force: bool = False, session_id: str = "default"):
        return await self._agent.promote_script_candidate(script_hash, session_id, skill_name, description, force)

    async def backup_skill(self, skill_name: str):
        return await BackupSkillTool(self._agent).execute(skill_name)

    async def restore_skill(self, skill_name: str, backup_path: str = None):
        return await RestoreSkillTool(self._agent).execute(skill_name, backup_path)

    async def git_reset_skill(self, skill_name: str):
        return await GitResetSkillTool(self._agent).execute(skill_name)

class SubagentService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    async def spawn(self, subtasks: List[str], session_id: str = "default") -> List[str]:
        return await self._agent.spawn_subagents(subtasks, parent_session_id=session_id)

class ToolRegistryService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    async def register(self, tool_name: str, desc: str, schema_str: str, code: str) -> dict:
        return await self._agent.execute_register_tool(tool_name, desc, schema_str, code)

class SchedulerService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    async def manage_task(self, action: str, task_prompt: str = None, cron_expr: str = None,
                          job_id: str = None, channel: str = "notification", session_id: str = None) -> dict:
        return await ScheduleTaskTool(self._agent).execute(action, task_prompt, cron_expr, job_id, channel, session_id)

class ConfirmationService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    async def request_confirmation(self, session_id: str, prompt: str, script_hash: str = None, code_preview: str = None) -> bool:
        return await self._agent._request_user_confirmation(prompt, script_hash, code_preview, session_id)

class ClinicalService:
    def __init__(self, agent: 'YuanGeAgent'):
        self._agent = agent

    async def update_rule(self, rule: str) -> dict:
        return await self._agent.execute_update_clinical_rule(rule)


# ── Service Health & Reconnection ─────────────────────

class ServiceHealthChecker:
    """Periodically check backend services and attempt reconnection."""

    def __init__(self, config: dict):
        self.config = config
        self.last_check: dict[str, dict] = {}
        self._redis_client: 'redis.Redis | None' = None

    async def check_redis(self) -> dict:
        """Ping Redis and return status."""
        start = asyncio.get_event_loop().time() if hasattr(asyncio.get_event_loop(), 'time') else __import__('time').time
        import time as _time
        t0 = _time.monotonic()
        try:
            redis_cfg = self.config.get("redis", {})
            r_host = redis_cfg.get("host", "localhost")
            r_port = redis_cfg.get("port", 6379)
            r_pass = redis_cfg.get("password")
            r_db = redis_cfg.get("db", 0)
            if r_pass:
                r_pass_encoded = urllib.parse.quote_plus(str(r_pass))
                redis_url = f"redis://:{r_pass_encoded}@{r_host}:{r_port}/{r_db}"
            else:
                redis_url = f"redis://{r_host}:{r_port}/{r_db}"
            client = redis.from_url(redis_url, decode_responses=True)
            try:
                await client.ping()
                latency = int((_time.monotonic() - t0) * 1000)
                return {"status": "online", "latency_ms": latency}
            finally:
                await client.aclose()
        except Exception as e:
            latency = int((_time.monotonic() - t0) * 1000)
            return {"status": "offline", "latency_ms": latency, "reason": str(e)[:200]}

    async def check_postgres(self) -> dict:
        """Ping PostgreSQL and return status."""
        import time as _time
        t0 = _time.monotonic()
        try:
            import asyncpg
            pg_cfg = (
                self.config.get("postgres")
                or self.config.get("postgresql")
                or self.config.get("pgvector")
                or {}
            )
            conn = await asyncpg.connect(
                host=pg_cfg.get("host", "localhost"),
                port=pg_cfg.get("port", 5432),
                user=pg_cfg.get("user", "root"),
                password=pg_cfg.get("password", "root"),
                database=pg_cfg.get("database", "root"),
                timeout=3,
            )
            try:
                await conn.execute("SELECT 1")
                latency = int((_time.monotonic() - t0) * 1000)
                return {"status": "online", "latency_ms": latency}
            finally:
                await conn.close()
        except ImportError:
            latency = int((_time.monotonic() - t0) * 1000)
            return {"status": "unknown", "latency_ms": latency, "reason": "asyncpg not installed"}
        except Exception as e:
            latency = int((_time.monotonic() - t0) * 1000)
            return {"status": "offline", "latency_ms": latency, "reason": str(e)[:200]}

    async def check_neo4j(self) -> dict:
        """Ping Neo4j and return status."""
        import time as _time
        t0 = _time.monotonic()
        try:
            from neo4j import GraphDatabase
            neo4j_cfg = self.config.get("neo4j", {})
            uri = neo4j_cfg.get("uri", "bolt://localhost:7687")
            user = neo4j_cfg.get("user", "neo4j")
            password = neo4j_cfg.get("password", "root")
            driver = GraphDatabase.driver(uri, auth=(user, password))
            try:
                with driver.session(database="neo4j") as session:
                    session.run("RETURN 1")
                latency = int((_time.monotonic() - t0) * 1000)
                return {"status": "online", "latency_ms": latency}
            finally:
                driver.close()
        except ImportError:
            latency = int((_time.monotonic() - t0) * 1000)
            return {"status": "unknown", "latency_ms": latency, "reason": "neo4j driver not installed"}
        except Exception as e:
            latency = int((_time.monotonic() - t0) * 1000)
            return {"status": "offline", "latency_ms": latency, "reason": str(e)[:200]}

    async def check_all(self) -> dict[str, dict]:
        """Run all service checks concurrently."""
        results = await asyncio.gather(
            self.check_redis(),
            self.check_postgres(),
            self.check_neo4j(),
            return_exceptions=True,
        )
        names = ["redis", "postgres", "neo4j"]
        report: dict[str, dict] = {}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                report[name] = {"status": "offline", "latency_ms": None, "reason": str(result)[:200]}
            else:
                report[name] = result
            self.last_check[name] = report[name]
        return report

    def summarize(self) -> tuple[bool, list[dict]]:
        """Return (all_healthy, service_list) from last check."""
        services: list[dict] = []
        all_healthy = True
        labels = {"redis": "Redis", "postgres": "Postgres", "neo4j": "Neo4j"}
        for name, info in self.last_check.items():
            svc = {
                "name": name,
                "label": labels.get(name, name),
                "status": info.get("status", "unknown"),
                "latency_ms": info.get("latency_ms"),
                "reason": info.get("reason"),
            }
            services.append(svc)
            if svc["status"] != "online":
                all_healthy = False
        return all_healthy, services

    async def attempt_reconnect_redis(
        self, max_retries: int = 5, backoff_base: float = 1.0
    ) -> 'redis.Redis | None':
        """Attempt to reconnect to Redis with exponential backoff."""
        import time as _time
        redis_cfg = self.config.get("redis", {})
        for attempt in range(1, max_retries + 1):
            try:
                r_host = redis_cfg.get("host", "localhost")
                r_port = redis_cfg.get("port", 6379)
                r_pass = redis_cfg.get("password")
                r_db = redis_cfg.get("db", 0)
                if r_pass:
                    r_pass_encoded = urllib.parse.quote_plus(str(r_pass))
                    redis_url = f"redis://:{r_pass_encoded}@{r_host}:{r_port}/{r_db}"
                else:
                    redis_url = f"redis://{r_host}:{r_port}/{r_db}"
                client = redis.from_url(
                    redis_url, decode_responses=True,
                    retry_on_timeout=True, health_check_interval=30,
                )
                await client.ping()
                logger.info(f"Redis reconnected (attempt {attempt}/{max_retries})")
                self._redis_client = client
                return client
            except Exception as e:
                delay = backoff_base * (2 ** (attempt - 1))
                logger.warning(f"Redis reconnect attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
        logger.error(f"Redis reconnection failed after {max_retries} attempts")
        return None


