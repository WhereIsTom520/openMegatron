"""API layer: FastAPI endpoints, IM gateway, and channel adapters.
Extracted from agent.py for better modularity."""

from __future__ import annotations

import os
import json
import asyncio
import logging
import socket
import time
import uuid
import hashlib
import platform
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from urllib.parse import urlparse

try:
    import psutil
except ImportError:
    psutil = None

try:
    from fastapi import FastAPI, Request, BackgroundTasks, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

from pysrc.memory_ontology import default_memory_ontology
from pysrc.memory_ontology import ontology_node_id

import logging
logger = logging.getLogger(__name__)

from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent

from pysrc.evolution import EvolutionStore

try:
    from pysrc.integrations.feishu_bot import FeishuBotAdapter, FeishuConfig
except ImportError:
    FeishuBotAdapter = None
    FeishuConfig = None

try:
    from pysrc.memory import MemoryRecallEngine
except ImportError:
    MemoryRecallEngine = None


class ChannelAdapter:
    name = "base"

    def parse_payload(self, payload: dict) -> dict:
        return {
            "user_id": payload.get("user_id", "default_user"),
            "text": payload.get("text", ""),
            "raw": payload
        }

    def session_id_for(self, message: dict) -> str:
        user_id = str(message.get("user_id") or "default_user")
        return f"{self.name}_{user_id}"

    async def handle_inbound(self, agent: "YuanGeAgent", message: dict) -> str:
        session_id = self.session_id_for(message)
        return await agent.chat(session_id, message.get("text", ""), domain="auto")

    async def deliver(self, message: dict, answer: str):
        logger.info(f"[{self.name} To {message.get('user_id')}]: {answer}")


class WebhookChannelAdapter(ChannelAdapter):
    name = "im_user"

    def parse_payload(self, payload: dict) -> dict:
        return {
            "user_id": payload.get("user_id", "default_im_user"),
            "text": payload.get("text", ""),
            "raw": payload
        }


class IMGateway:
    def __init__(self, config: dict, agent_factory):
        self.gateway_cfg = config.get("gateway", {})
        self.agent_factory = agent_factory
        self.agent = None
        self.adapters = {
            "webhook": WebhookChannelAdapter(),
            "feishu": FeishuBotAdapter(FeishuConfig.from_config(config), logger),
        }
        self.app = FastAPI(title="Agent IM Gateway", lifespan=self.lifespan_handler)
        origins = self.gateway_cfg.get("allowed_origins", ["*"])
        self.app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
        self.app.post("/webhook")(self.handle_webhook)
        self.app.post("/integrations/feishu/events")(self.handle_feishu_events)

    @asynccontextmanager
    async def lifespan_handler(self, app: FastAPI):
        self.agent = await self.agent_factory()
        logger.info("Gateway and Agent Initialized.")
        yield
        if self.agent:
            await self.agent.close()
            logger.info("Gateway and Agent Shutdown safely.")

    async def handle_webhook(self, request: Request, background_tasks: BackgroundTasks):
        payload = await request.json()
        adapter = self.adapters["webhook"]
        message = adapter.parse_payload(payload)
        if not message.get("text"):
            return {"status": "ignored"}
        background_tasks.add_task(self.process_and_reply, adapter, message)
        return {"status": "processing"}

    async def handle_feishu_events(self, request: Request, background_tasks: BackgroundTasks):
        adapter = self.adapters["feishu"]
        return await adapter.handle_callback(request, self.agent, background_tasks)

    async def process_and_reply(self, adapter: ChannelAdapter, message: dict):
        try:
            answer = await adapter.handle_inbound(self.agent, message)
            await adapter.deliver(message, answer)
        except Exception as e:
            logger.error(f"Gateway adapter error: {e}")

    def run(self, host=None, port=None):
        host = host or self.gateway_cfg.get("host", "127.0.0.1")
        port = int(port or self.gateway_cfg.get("port", 8080))
        logger.info(f"Starting Gateway on http://{host}:{port}")
        uvicorn.run(self.app, host=host, port=port)

class AgentAPI:
    def __init__(self, config: dict, agent_factory):
        self.config = config
        self.agent_factory = agent_factory
        self.agent = None
        self.active_ws_connections = set()
        self.started_at = time.time()
        self.agent_startup_error = None
        self.chat_lock = asyncio.Lock()
        self.evolution_store = EvolutionStore(BASE_DIR.parent)
        self.app = FastAPI(title="Agent HTTP API", lifespan=self.lifespan)
        self.app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
        self.app.post("/chat")(self.chat_endpoint)
        self.app.post("/agent/delegate")(self.remote_delegate_endpoint)
        self.app.get("/runtime_status")(self.runtime_status_endpoint)
        self.app.get("/model_options")(self.model_options_endpoint)
        self.app.get("/validate_link")(self.validate_link_endpoint)
        self.app.get("/evolution/policy")(self.evolution_policy_endpoint)
        self.app.get("/evolution/proposals")(self.list_evolution_proposals_endpoint)
        self.app.post("/evolution/proposals")(self.create_evolution_proposal_endpoint)
        self.app.post("/evolution/apply")(self.apply_evolution_proposal_endpoint)
        self.app.post("/evolution/reject")(self.reject_evolution_proposal_endpoint)
        self.app.post("/evolution/rollback")(self.rollback_evolution_proposal_endpoint)
        self.app.get("/memory/records")(self.list_memory_records_endpoint)
        self.app.post("/memory/update")(self.update_memory_record_endpoint)
        self.app.post("/memory/delete")(self.delete_memory_record_endpoint)
        self.app.post("/memory/clear")(self.clear_memory_endpoint)
        self.app.post("/conversations/clear")(self.clear_conversations_endpoint)
        self.app.get("/check_confirmation")(self.check_confirmation_endpoint)
        self.app.post("/submit_confirmation")(self.submit_confirmation_endpoint)
        self.app.get("/memory/ontology")(self.get_memory_ontology_endpoint)
        self.app.get("/memory/hypergraph")(self.get_memory_hypergraph_endpoint)
        self.app.add_api_websocket_route("/ws/agent-events", self.agent_events_ws)
        self.app.get("/skills/list")(self.list_skills_endpoint)
        self.app.get("/skills/read")(self.read_skill_file_endpoint)
        self.app.post("/skills/write")(self.write_skill_file_endpoint)
        self.app.post("/skills/rollback")(self.rollback_skill_file_endpoint)

    async def agent_events_ws(self, websocket: WebSocket):
        await websocket.accept()
        self.active_ws_connections.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            self.active_ws_connections.discard(websocket)

    async def broadcast_agent_event(self, event_type: str, data: dict):
        if not self.active_ws_connections:
            return
        event_id = str(uuid.uuid4())
        payload = {
            "schema": "megatron.telemetry.v1",
            "event_id": event_id,
            "type": event_type,
            "phase": data.get("phase") if isinstance(data, dict) else None,
            "timestamp": time.time(),
            "session_id": data.get("session_id") if isinstance(data, dict) else None,
            "parent_event_id": data.get("parent_event_id") if isinstance(data, dict) else None,
            "data": data
        }
        message = json.dumps(payload, ensure_ascii=False)
        for ws in list(self.active_ws_connections):
            try:
                await ws.send_text(message)
            except Exception:
                self.active_ws_connections.discard(ws)

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        try:
            self.agent = await self.agent_factory()
            self.agent.broadcast_event = self.broadcast_agent_event
            self.agent_startup_error = None
        except Exception as exc:
            self.agent = None
            self.agent_startup_error = f"{exc.__class__.__name__}: {exc}"
            logger.error(f"Agent startup failed; HTTP status API remains available: {exc}", exc_info=True)
        yield
        if self.agent:
            await self.agent.close()

    async def runtime_status_endpoint(self, request: Request):
        started = time.perf_counter()
        request_host = request.url.hostname or "localhost"
        request_port = request.url.port
        status = await asyncio.to_thread(self._collect_runtime_status, request_host, request_port)
        status["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
        return status

    async def model_options_endpoint(self):
        providers = []
        for provider_id, cfg in (self.config.get("llm_providers", {}) or {}).items():
            models = cfg.get("models") or [cfg.get("model", "gpt-4o-mini")]
            providers.append({
                "id": provider_id,
                "label": cfg.get("label") or provider_id,
                "baseUrl": cfg.get("base_url"),
                "configured": bool(cfg.get("api_key")),
                "models": [{"id": str(model), "name": str(model)} for model in models if model],
            })
        return {
            "active_provider": getattr(self.agent, "llm_provider", None) or self.config.get("llm_provider", "openai"),
            "active_model": getattr(self.agent, "model", None) or self.config.get("llm", {}).get("model", "gpt-4o-mini"),
            "providers": providers,
        }

    def _collect_runtime_status(self, request_host: str, request_port: Optional[int]) -> dict:
        now = time.time()
        system = self._collect_system_metrics()
        process = self._collect_process_metrics()
        services = self._collect_service_status(request_host, request_port)
        skills = self._collect_skill_counts()
        return {
            "ok": True,
            "timestamp": now,
            "backend": {
                "status": "online",
                "agent_status": "ready" if self.agent else "degraded",
                "startup_error": self.agent_startup_error,
                "pid": os.getpid(),
                "uptime_sec": max(0, round(now - self.started_at)),
                "host": request_host,
                "port": request_port,
                "python": platform.python_version(),
            },
            "system": system,
            "process": process,
            "services": services,
            "skills": skills,
        }

    def _collect_system_metrics(self) -> dict:
        logical_cores = os.cpu_count() or 0
        metrics = {
            "platform": platform.platform(),
            "logical_cores": logical_cores,
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "memory_used_mb": 0.0,
            "memory_total_mb": 0.0,
            "disk_percent": 0.0,
            "disk_free_gb": 0.0,
            "psutil_available": psutil is not None,
        }
        if psutil is None:
            return metrics
        try:
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(str(BASE_DIR.parent))
            metrics.update({
                "cpu_percent": self._round_percent(psutil.cpu_percent(interval=None)),
                "memory_percent": self._round_percent(memory.percent),
                "memory_used_mb": round(memory.used / 1024 / 1024, 1),
                "memory_total_mb": round(memory.total / 1024 / 1024, 1),
                "disk_percent": self._round_percent(disk.percent),
                "disk_free_gb": round(disk.free / 1024 / 1024 / 1024, 2),
            })
        except Exception as exc:
            metrics["error"] = str(exc)
        return metrics

    def _collect_process_metrics(self) -> dict:
        metrics = {
            "pid": os.getpid(),
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "threads": 0,
        }
        if psutil is None:
            return metrics
        try:
            proc = psutil.Process(os.getpid())
            metrics.update({
                "cpu_percent": self._round_percent(proc.cpu_percent(interval=None)),
                "memory_mb": round(proc.memory_info().rss / 1024 / 1024, 1),
                "threads": proc.num_threads(),
            })
        except Exception as exc:
            metrics["error"] = str(exc)
        return metrics

    def _collect_service_status(self, request_host: str, request_port: Optional[int]) -> List[dict]:
        services = [{
            "name": "api",
            "label": "API",
            "host": request_host,
            "port": request_port,
            "status": "online",
            "latency_ms": 0,
        }]
        redis_cfg = self.config.get("redis", {}) or {}
        services.append(self._probe_tcp_service(
            "redis",
            "Redis",
            redis_cfg.get("host", "localhost"),
            redis_cfg.get("port", 6379),
        ))
        postgres_cfg = (
            self.config.get("postgres")
            or self.config.get("postgresql")
            or self.config.get("pgvector")
            or {}
        )
        services.append(self._probe_tcp_service(
            "postgres",
            "Postgres",
            postgres_cfg.get("host", "localhost"),
            postgres_cfg.get("port", 5432),
        ))
        neo4j_host, neo4j_port = self._neo4j_host_port()
        services.append(self._probe_tcp_service("neo4j", "Neo4j", neo4j_host, neo4j_port))
        return services

    def _collect_skill_counts(self) -> dict:
        categories = {}
        loaded = 0
        if self.agent and getattr(self.agent, "loaded_skills", None):
            loaded = len(self.agent.loaded_skills)
            for info in self.agent.loaded_skills.values():
                category = str(info.get("category") or "general").strip() or "general"
                categories[category] = categories.get(category, 0) + 1
            return {"total": loaded, "loaded": loaded, "categories": categories}

        skill_root = BASE_DIR / "skills"
        skill_dirs = set()
        if skill_root.exists():
            for candidate in skill_root.rglob("*"):
                if not candidate.is_file() or candidate.name.lower() != "skill.md":
                    continue
                try:
                    rel = candidate.parent.relative_to(skill_root)
                except ValueError:
                    continue
                if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
                    continue
                skill_dirs.add(candidate.parent)
        for skill_dir in skill_dirs:
            try:
                rel = skill_dir.relative_to(skill_root)
                category = rel.parts[0] if len(rel.parts) > 1 else "general"
            except ValueError:
                category = "general"
            categories[category] = categories.get(category, 0) + 1
        return {"total": len(skill_dirs), "loaded": loaded, "categories": categories}

    def _probe_tcp_service(self, name: str, label: str, host: str, port: Any, timeout: float = 0.08) -> dict:
        safe_host = str(host or "localhost")
        connect_host = "127.0.0.1" if safe_host.lower() == "localhost" else safe_host
        safe_port = self._safe_int(port)
        started = time.perf_counter()
        service = {
            "name": name,
            "label": label,
            "host": safe_host,
            "port": safe_port,
            "status": "unknown",
            "latency_ms": None,
        }
        if not safe_port:
            service["reason"] = "missing_port"
            return service
        try:
            with socket.create_connection((connect_host, safe_port), timeout=timeout):
                pass
            service["status"] = "online"
            service["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
        except OSError as exc:
            service["status"] = "offline"
            service["latency_ms"] = max(0, round((time.perf_counter() - started) * 1000))
            service["reason"] = exc.__class__.__name__
        return service

    def _neo4j_host_port(self) -> tuple[str, int]:
        neo4j_cfg = self.config.get("neo4j", {}) or {}
        uri = str(neo4j_cfg.get("uri") or "")
        parsed = urllib.parse.urlparse(uri) if uri else None
        host = (parsed.hostname if parsed else None) or neo4j_cfg.get("host", "localhost")
        port = (
            parsed.port if parsed and parsed.port
            else self._safe_int(neo4j_cfg.get("bolt_port"), 7687)
        )
        return str(host or "localhost"), port

    def _round_percent(self, value: Any) -> float:
        try:
            numeric = float(value)
        except Exception:
            return 0.0
        return round(max(0.0, min(100.0, numeric)), 1)

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    async def validate_link_endpoint(self, url: str):
        if aiohttp is None:
            return {"ok": False, "status": 0, "reason": "aiohttp_unavailable"}
        url = (url or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return {"ok": False, "status": 0, "reason": "unsupported_url"}
        if not await self._is_public_http_target(parsed):
            return {"ok": False, "status": 0, "reason": "blocked_private_target"}
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                try:
                    async with session.head(url, allow_redirects=True) as response:
                        if response.status == 405:
                            raise aiohttp.ClientResponseError(response.request_info, response.history, status=response.status)
                        return {
                            "ok": self._link_status_is_available(response.status),
                            "status": response.status,
                            "final_url": str(response.url),
                        }
                except aiohttp.ClientResponseError:
                    raise
                except Exception:
                    async with session.get(url, allow_redirects=True) as response:
                        return {
                            "ok": self._link_status_is_available(response.status),
                            "status": response.status,
                            "final_url": str(response.url),
                        }
        except Exception as exc:
            logger.warning(f"Link validation failed for {url}: {exc}")
            return {"ok": False, "status": 0, "reason": "request_failed"}

    def _link_status_is_available(self, status: int) -> bool:
        return 200 <= status < 400 or status in (401, 403)

    async def _is_public_http_target(self, parsed_url) -> bool:
        hostname = parsed_url.hostname
        if not hostname:
            return False
        try:
            addresses = await asyncio.to_thread(socket.getaddrinfo, hostname, parsed_url.port or (443 if parsed_url.scheme == "https" else 80), type=socket.SOCK_STREAM)
            for address in addresses:
                ip = ipaddress.ip_address(address[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
                    return False
            return True
        except Exception as exc:
            logger.warning(f"Could not resolve link target {hostname}: {exc}")
            return False

    async def evolution_policy_endpoint(self):
        return {"status": "success", "policy": self.evolution_store.policy()}

    async def list_evolution_proposals_endpoint(self, status: str = "", include_content: bool = True):
        try:
            proposals = self.evolution_store.list_proposals(status=status, include_content=include_content)
            return {"status": "success", "proposals": proposals, "total": len(proposals)}
        except Exception as exc:
            logger.warning(f"Evolution proposal listing failed: {exc}")
            return {"status": "error", "message": str(exc), "proposals": [], "total": 0}

    async def create_evolution_proposal_endpoint(self, request: Request):
        data = await self._safe_request_json(request)
        try:
            proposal = self.evolution_store.create_proposal(
                title=str(data.get("title") or ""),
                summary=str(data.get("summary") or ""),
                kind=str(data.get("kind") or "project"),
                files=data.get("files") if isinstance(data.get("files"), list) else [],
                author=str(data.get("author") or "user"),
                notes=data.get("notes") if isinstance(data.get("notes"), list) else [],
            )
            return {"status": "success", "proposal": proposal}
        except EvolutionError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            logger.warning(f"Evolution proposal creation failed: {exc}", exc_info=True)
            return {"status": "error", "message": f"Failed to create proposal: {exc}"}

    async def apply_evolution_proposal_endpoint(self, request: Request):
        data = await self._safe_request_json(request)
        return self._evolution_action_response(
            lambda: self.evolution_store.apply_proposal(str(data.get("id") or ""), reviewer=str(data.get("reviewer") or "user"))
        )

    async def reject_evolution_proposal_endpoint(self, request: Request):
        data = await self._safe_request_json(request)
        return self._evolution_action_response(
            lambda: self.evolution_store.reject_proposal(
                str(data.get("id") or ""),
                reviewer=str(data.get("reviewer") or "user"),
                reason=str(data.get("reason") or ""),
            )
        )

    async def rollback_evolution_proposal_endpoint(self, request: Request):
        data = await self._safe_request_json(request)
        return self._evolution_action_response(
            lambda: self.evolution_store.rollback_proposal(str(data.get("id") or ""), reviewer=str(data.get("reviewer") or "user"))
        )

    def _evolution_action_response(self, action):
        try:
            proposal = action()
            return {"status": "success", "proposal": proposal}
        except EvolutionError as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            logger.warning(f"Evolution action failed: {exc}", exc_info=True)
            return {"status": "error", "message": f"Evolution action failed: {exc}"}

    async def list_memory_records_endpoint(self, query: str = "", limit: int = 80):
        db = self._memory_db()
        if not db or not getattr(db, "pg_pool", None):
            return {"records": [], "total": 0, "status": "degraded", "message": self.agent_startup_error or "memory database unavailable"}

        safe_limit = max(1, min(int(limit or 80), 200))
        query = (query or "").strip()
        async with db.pg_pool.acquire() as conn:
            if query:
                pattern = f"%{query}%"
                rows = await conn.fetch(
                    """
                    SELECT id, text, owner_id, scope, session_id, metadata, created_at
                    FROM episodic_memory
                    WHERE id ILIKE $1
                       OR text ILIKE $1
                       OR COALESCE(owner_id, '') ILIKE $1
                       OR COALESCE(scope, '') ILIKE $1
                       OR COALESCE(session_id, '') ILIKE $1
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT $2
                    """,
                    pattern,
                    safe_limit,
                )
                total = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM episodic_memory
                    WHERE id ILIKE $1
                       OR text ILIKE $1
                       OR COALESCE(owner_id, '') ILIKE $1
                       OR COALESCE(scope, '') ILIKE $1
                       OR COALESCE(session_id, '') ILIKE $1
                    """,
                    pattern,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, text, owner_id, scope, session_id, metadata, created_at
                    FROM episodic_memory
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT $1
                    """,
                    safe_limit,
                )
                total = await conn.fetchval("SELECT COUNT(*) FROM episodic_memory")
        return {"records": [self._memory_record_to_json(row) for row in rows], "total": int(total or 0)}

    async def update_memory_record_endpoint(self, request: Request):
        db = self._memory_db()
        if not db or not getattr(db, "pg_pool", None):
            return {"status": "error", "message": self.agent_startup_error or "memory database unavailable"}
        data = await self._safe_request_json(request)
        memory_id = str(data.get("id") or "").strip()
        text = str(data.get("text") or "").strip()
        if not memory_id:
            return {"status": "error", "message": "Missing memory id."}
        if not text:
            return {"status": "error", "message": "Memory text cannot be empty."}

        async with db.pg_pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, owner_id, scope, session_id, metadata FROM episodic_memory WHERE id = $1",
                memory_id,
            )
            if not existing:
                return {"status": "error", "message": "Memory record not found."}
            await conn.execute("DELETE FROM topic_index WHERE episodic_id = $1", memory_id)

        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = self._json_safe_value(existing["metadata"])
        await db.service.add_memory(
            session_id=str(data.get("session_id") or existing["session_id"] or "default"),
            text=text,
            owner_id=str(data.get("owner_id") or existing["owner_id"] or "shared"),
            scope=str(data.get("scope") or existing["scope"] or "shared"),
            metadata=metadata if isinstance(metadata, dict) else {},
            memory_id=memory_id,
        )
        record = await self._fetch_memory_record(memory_id)
        return {"status": "success", "record": record}

    async def delete_memory_record_endpoint(self, request: Request):
        db = self._memory_db()
        if not db or not getattr(db, "pg_pool", None):
            return {"status": "error", "message": self.agent_startup_error or "memory database unavailable"}
        data = await self._safe_request_json(request)
        memory_id = str(data.get("id") or "").strip()
        if not memory_id:
            return {"status": "error", "message": "Missing memory id."}
        deleted = await self._delete_memory_record(memory_id)
        return {"status": "success", "deleted": deleted, "id": memory_id}

    async def clear_memory_endpoint(self):
        db = self._memory_db()
        if not db:
            return {"status": "error", "message": self.agent_startup_error or "memory database unavailable"}
        deleted = {"episodic_memory": 0, "topic_index": 0, "memory_links": 0, "memory_evolution_log": 0, "redis_core": 0, "neo4j": 0}
        if getattr(db, "pg_pool", None):
            async with db.pg_pool.acquire() as conn:
                async with conn.transaction():
                    deleted["memory_links"] = self._command_count(await conn.execute("DELETE FROM memory_links"))
                    deleted["memory_evolution_log"] = self._command_count(await conn.execute("DELETE FROM memory_evolution_log"))
                    deleted["topic_index"] = self._command_count(await conn.execute("DELETE FROM topic_index"))
                    deleted["episodic_memory"] = self._command_count(await conn.execute("DELETE FROM episodic_memory"))
        if self.agent and getattr(self.agent, "ctx", None):
            deleted["redis_core"] = await self._delete_redis_patterns(self.agent.ctx.redis, ["core_memory:*"])
        if getattr(db, "neo4j", None):
            try:
                async with db.neo4j.session() as session:
                    result = await session.run("MATCH (n) DETACH DELETE n")
                    summary = await result.consume()
                    deleted["neo4j"] = int(getattr(summary.counters, "nodes_deleted", 0) or 0)
            except Exception as exc:
                logger.warning(f"Neo4j memory clear skipped: {exc}")
        return {"status": "success", "deleted": deleted}

    async def clear_conversations_endpoint(self, request: Request):
        if not self.agent or not getattr(self.agent, "ctx", None):
            return {"status": "error", "message": self.agent_startup_error or "agent context unavailable"}
        data = await self._safe_request_json(request)
        session_id = str(data.get("session_id") or "").strip()
        patterns = self._conversation_redis_patterns(session_id or None)
        deleted = await self._delete_redis_patterns(self.agent.ctx.redis, patterns)
        return {"status": "success", "deleted": deleted, "session_id": session_id or None}



    async def get_memory_ontology_endpoint(self):
        """Return the ontology definition (node_types + relation_types)."""
        from pysrc.memory_ontology import default_memory_ontology
        return default_memory_ontology()

    async def get_memory_hypergraph_endpoint(self, kind: str = "", limit: int = 200):
        """Return hypergraph data as nodes + edges for frontend visualization."""
        db = self._memory_db()
        if not db or not db.service:
            return {"nodes": [], "edges": [], "hyperedges": [], "status": "degraded", "message": "memory database unavailable"}
        try:
            if kind:
                nodes = await db.service.query_ontology_nodes(kind=kind, limit=limit)
                return {"nodes": nodes, "edges": [], "hyperedges": []}
            return await db.service.query_hypergraph(limit_nodes=limit, limit_edges=min(limit, 100))
        except Exception as e:
            logger.warning(f"Hypergraph query failed: {e}")
            return {"nodes": [], "edges": [], "hyperedges": [], "status": "error", "message": str(e)}

    def _memory_db(self):
        if not self.agent:
            return None
        engine = getattr(self.agent, "memory_engine", None)
        return getattr(engine, "db", None)


    # ---- Skill editor endpoints ----

    async def list_skills_endpoint(self):
        """List all skills in pysrc/skills with their SKILL.md and Python scripts."""
        skills_root = BASE_DIR / "skills"
        result = []
        try:
            if skills_root.is_dir():
                for md_path in sorted(skills_root.rglob("SKILL.md")):
                    rel_dir = md_path.parent.relative_to(skills_root)
                    # Build a friendly name from the directory path
                    name = str(rel_dir).replace("\\", "/")
                    files = []
                    # Collect SKILL.md and all .py files in the skill directory
                    for item in sorted(md_path.parent.rglob("*")):
                        if item.is_file() and item.suffix in (".md", ".py"):
                            rel = str(item.relative_to(skills_root)).replace("\\", "/")
                            files.append({"name": item.name, "path": rel})
                    result.append({"name": name, "path": str(rel_dir).replace("\\", "/"), "files": files})
        except Exception as exc:
            logger.warning(f"Failed to list skills: {exc}")
            return {"status": "error", "message": str(exc), "skills": []}
        return {"status": "success", "skills": result, "total": len(result)}

    async def read_skill_file_endpoint(self, path: str = ""):
        """Read the content of a skill file given its relative path under pysrc/skills."""
        if not path:
            return {"status": "error", "message": "Missing path parameter"}
        # Security: normalize and ensure it stays under pysrc/skills
        skills_root = BASE_DIR / "skills"
        try:
            target = (skills_root / path).resolve()
            if not str(target).startswith(str(skills_root.resolve())):
                return {"status": "error", "message": "Path traversal denied"}
            if not target.is_file():
                return {"status": "error", "message": "File not found"}
            content = target.read_text(encoding="utf-8", errors="replace")
            return {"status": "success", "path": path, "content": content}
        except Exception as exc:
            logger.warning(f"Failed to read skill file {path}: {exc}")
            return {"status": "error", "message": str(exc)}

    async def write_skill_file_endpoint(self, request: Request):
        """Write content to a skill file, with automatic rollback snapshot."""
        data = await self._safe_request_json(request)
        rel_path = str(data.get("path") or "").strip()
        content = str(data.get("content") or "")
        if not rel_path:
            return {"status": "error", "message": "Missing path"}
        skills_root = BASE_DIR / "skills"
        project_root = BASE_DIR.parent
        rollback_file = project_root / "skill_rollbacks.json"
        try:
            target = (skills_root / rel_path).resolve()
            if not str(target).startswith(str(skills_root.resolve())):
                return {"status": "error", "message": "Path traversal denied"}
            # Ensure parent directory exists
            target.parent.mkdir(parents=True, exist_ok=True)
            # Create rollback snapshot before writing
            snapshot = {
                "id": str(uuid.uuid4()),
                "skill_name": data.get("skill_name", rel_path),
                "path": rel_path,
                "timestamp": time.time(),
                "original_content": "",
            }
            if target.is_file():
                snapshot["original_content"] = target.read_text(encoding="utf-8", errors="replace")
            # Write the new content
            target.write_text(content, encoding="utf-8")
            # Record in rollbacks JSON
            rollbacks = []
            if rollback_file.is_file():
                try:
                    rollbacks = json.loads(rollback_file.read_text(encoding="utf-8"))
                except Exception:
                    rollbacks = []
            rollbacks.insert(0, snapshot)
            # Cap at 50 entries
            rollbacks = rollbacks[:50]
            rollback_file.write_text(json.dumps(rollbacks, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"status": "success", "snapshot": snapshot["id"], "message": "File written and rollback snapshot created"}
        except Exception as exc:
            logger.warning(f"Failed to write skill file {rel_path}: {exc}")
            return {"status": "error", "message": str(exc)}

    async def rollback_skill_file_endpoint(self, request: Request):
        """Restore a skill file from a rollback snapshot."""
        data = await self._safe_request_json(request)
        snapshot_id = str(data.get("snapshot_id") or "").strip()
        project_root = BASE_DIR.parent
        skills_root = BASE_DIR / "skills"
        rollback_file = project_root / "skill_rollbacks.json"
        if not snapshot_id:
            # Return list of available rollbacks
            try:
                if rollback_file.is_file():
                    rollbacks = json.loads(rollback_file.read_text(encoding="utf-8"))
                    return {"status": "success", "rollbacks": rollbacks, "total": len(rollbacks)}
                return {"status": "success", "rollbacks": [], "total": 0}
            except Exception as exc:
                return {"status": "error", "message": str(exc), "rollbacks": []}
        # Restore from a specific snapshot
        try:
            if not rollback_file.is_file():
                return {"status": "error", "message": "No rollback file found"}
            rollbacks = json.loads(rollback_file.read_text(encoding="utf-8"))
            target_snapshot = None
            remaining = []
            for snap in rollbacks:
                if snap.get("id") == snapshot_id and not target_snapshot:
                    target_snapshot = snap
                else:
                    remaining.append(snap)
            if not target_snapshot:
                return {"status": "error", "message": f"Snapshot {snapshot_id} not found"}
            rel_path = target_snapshot.get("path", "")
            if not rel_path:
                return {"status": "error", "message": "Snapshot has no path"}
            target = (skills_root / rel_path).resolve()
            if not str(target).startswith(str(skills_root.resolve())):
                return {"status": "error", "message": "Path traversal denied"}
            old_content = target_snapshot.get("original_content", "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(old_content, encoding="utf-8")
            # Update rollback file (remove the restored snapshot)
            rollback_file.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"status": "success", "message": f"Rolled back {rel_path} from snapshot {snapshot_id}"}
        except Exception as exc:
            logger.warning(f"Failed to rollback skill file: {exc}")
            return {"status": "error", "message": str(exc)}
    async def _safe_request_json(self, request: Request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def _fetch_memory_record(self, memory_id: str) -> Optional[dict]:
        db = self._memory_db()
        if not db or not getattr(db, "pg_pool", None):
            return None
        async with db.pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, text, owner_id, scope, session_id, metadata, created_at FROM episodic_memory WHERE id = $1",
                memory_id,
            )
        return self._memory_record_to_json(row) if row else None

    async def _delete_memory_record(self, memory_id: str) -> int:
        db = self._memory_db()
        deleted = 0
        async with db.pg_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM memory_links WHERE source_id = $1 OR target_id = $1", memory_id)
                await conn.execute("DELETE FROM memory_evolution_log WHERE source_id = $1 OR target_id = $1", memory_id)
                await conn.execute("DELETE FROM topic_index WHERE episodic_id = $1", memory_id)
                deleted = self._command_count(await conn.execute("DELETE FROM episodic_memory WHERE id = $1", memory_id))
        if getattr(db, "neo4j", None):
            try:
                async with db.neo4j.session() as session:
                    await session.run(
                        "MATCH (n) WHERE n.id = $id OR n.memory_id = $id OR n.episodic_id = $id DETACH DELETE n",
                        id=memory_id,
                    )
            except Exception as exc:
                logger.warning(f"Neo4j memory delete skipped for {memory_id}: {exc}")
        return deleted

    def _memory_record_to_json(self, row) -> dict:
        return {
            "id": str(row["id"]),
            "text": row["text"] or "",
            "owner_id": row["owner_id"] or "",
            "scope": row["scope"] or "",
            "session_id": row["session_id"] or "",
            "metadata": self._json_safe_value(row["metadata"]),
            "created_at": self._json_safe_value(row["created_at"]),
        }

    def _json_safe_value(self, value):
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool, list, dict)):
            return value
        if hasattr(value, "isoformat"):
            return value.isoformat()
        try:
            return json.loads(value)
        except Exception:
            return str(value)

    def _command_count(self, command: str) -> int:
        try:
            return int(str(command).split()[-1])
        except Exception:
            return 0

    def _conversation_redis_patterns(self, session_id: Optional[str]) -> List[str]:
        if session_id:
            return [
                f"agent_history:{session_id}",
                f"core_memory:{session_id}",
                f"notifications:{session_id}",
                f"active_chat_turn:{session_id}",
                f"confirm_req:{session_id}:*",
                f"failure:{session_id}:*",
                f"chat:shared:{session_id}",
                f"chat:private:*:{session_id}",
            ]
        return [
            "agent_history:*",
            "core_memory:*",
            "notifications:*",
            "active_chat_turn:*",
            "confirm_req:*",
            "failure:*",
            "chat:shared:*",
            "chat:private:*",
        ]

    async def _delete_redis_patterns(self, redis_client, patterns: List[str]) -> int:
        deleted = 0
        for pattern in patterns:
            batch = []
            async for key in redis_client.scan_iter(match=pattern, count=200):
                batch.append(key)
                if len(batch) >= 200:
                    deleted += int(await redis_client.delete(*batch))
                    batch = []
            if batch:
                deleted += int(await redis_client.delete(*batch))
        return deleted

    async def chat_endpoint(self, request: Request):
        data = await request.json()
        if not self.agent:
            return {
                "answer": f"Backend is running, but the agent is not ready: {self.agent_startup_error or 'startup failed'}",
                "executed_tools": [],
                "status": "degraded",
            }
        session_id = str(data.get("session_id") or "default")
        room_id = str(data.get("room_id") or "").strip()
        user_id = str(data.get("user_id") or "shared").strip() or "shared"
        user_name = str(data.get("user_name") or user_id).strip() or user_id
        if room_id and (not session_id or session_id == "default"):
            safe_room = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in room_id)[:120]
            session_id = f"room_{safe_room or hashlib.sha256(room_id.encode('utf-8')).hexdigest()[:16]}"
        message = str(data.get("message") or "")
        agent_message = message
        if user_name or user_id != "shared":
            agent_message = f"[Group chat speaker: {user_name} | user_id: {user_id}]\n{message}"
        domain = data.get("domain", "auto")
        provider = data.get("provider")
        model = data.get("model")
        lang = data.get("lang", "en")
        if provider:
            provider_id = str(provider).strip().lower()
            provider_cfg = (self.config.get("llm_providers", {}) or {}).get(provider_id)
            if not provider_cfg or not provider_cfg.get("api_key"):
                provider_label = (provider_cfg or {}).get("label") or provider_id
                if lang == "zh":
                    missing_key_message = (
                        f"{provider_label} 尚未配置 API Key。请在 pysrc/model.toml 中添加该厂商的 api_key。"
                        "或设置 MEGATRON_FORCE_LLM_SETUP=1 后重新运行 start.bat。"
                    )
                else:
                    missing_key_message = (
                        f"Provider '{provider_id}' is not configured. Please add its API key in pysrc/model.toml "
                        "or rerun start.bat with MEGATRON_FORCE_LLM_SETUP=1."
                    )
                return {
                    "answer": missing_key_message,
                    "executed_tools": [],
                    "status": "missing_provider_key",
                }
        async with self.chat_lock:
            if provider or model:
                self.agent.configure_llm(provider, model)
            answer = await self.agent.chat(session_id, agent_message, domain)
        return {"answer": answer, "executed_tools": []}

    async def remote_delegate_endpoint(self, request: Request):
        if not self.agent:
            return {"status": "error", "message": f"Agent is not ready: {self.agent_startup_error or 'startup failed'}"}
        data = await request.json()
        signature = request.headers.get("X-Agent-Signature", "")
        if not self.agent.verify_swarm_signature(data, signature):
            return {"status": "error", "message": "Invalid or missing swarm signature."}
        scopes = data.get("scopes") or []
        ok_scope, missing = self.agent._scopes_cover(self.agent.swarm_allowed_remote_scopes, scopes)
        if not ok_scope:
            return {"status": "error", "message": f"Remote task scopes denied: {missing}"}
        if "chat:delegate" not in scopes and "*" not in scopes:
            return {"status": "error", "message": "Remote delegation requires chat:delegate scope."}
        source_node = str(data.get("source_node") or "remote")
        session_id = f"remote_{source_node}_{data.get('session_id', 'default')}"
        task_prompt = str(data.get("task_prompt") or "").strip()
        if not task_prompt:
            return {"status": "error", "message": "Missing task_prompt."}
        answer = await self.agent.chat(session_id, task_prompt, domain="auto")
        return {"status": "success", "node_id": self.agent.swarm_node_id, "answer": answer}

    async def check_confirmation_endpoint(self, session_id: str = "default"):
        if not self.agent:
            return {"pending": False, "status": "degraded", "message": self.agent_startup_error}
        pattern = f"confirm_req:{session_id}:*"
        keys = await self.agent.ctx.redis.keys(pattern)
        active_turn = await self.agent._get_active_chat_turn(session_id)
        if keys:
            for key in keys:
                data_str = await self.agent.ctx.redis.get(key)
                if data_str:
                    data = json.loads(data_str)
                    if data.get("status") == "pending":
                        request_turn = data.get("turn_id")
                        if request_turn and request_turn != active_turn:
                            await self.agent._mark_confirmation_denied(key, data, "stale_confirmation_request")
                            continue
                        return {
                            "pending": True,
                            "request_id": data.get("request_id"),
                            "prompt": data["prompt"],
                            "code_preview": data["code_preview"]
                        }
        return {"pending": False}

    async def submit_confirmation_endpoint(self, request: Request):
        if not self.agent:
            return {"status": "error", "message": f"Agent is not ready: {self.agent_startup_error or 'startup failed'}"}
        req_data = await request.json()
        session_id = req_data.get("session_id", "default")
        request_id = req_data.get("request_id")
        action = req_data.get("action")
        if not request_id:
            return {"status": "error", "message": "Missing request_id"}
        key = f"confirm_req:{session_id}:{request_id}"
        data_str = await self.agent.ctx.redis.get(key)
        if not data_str:
            return {"status": "error", "message": "Confirmation request not found or expired"}
        data = json.loads(data_str)
        if data.get("status") != "pending":
            return {"status": "error", "message": "Request already processed"}
        active_turn = await self.agent._get_active_chat_turn(session_id)
        request_turn = data.get("turn_id")
        if action == "approve" and request_turn and request_turn != active_turn:
            await self.agent._mark_confirmation_denied(key, data, "stale_confirmation_request")
            await self.broadcast_agent_event("hitl_response", {
                "schema": "megatron.hitl.v1",
                "session_id": session_id,
                "request_id": request_id,
                "status": "denied",
                "reason": "stale_confirmation_request"
            })
            return {"status": "error", "message": "Confirmation request is stale. Please retry the current task."}
        data["status"] = "approved" if action == "approve" else "denied"
        await self.agent.ctx.redis.set(key, json.dumps(data, ensure_ascii=False), ex=60)
        await self.broadcast_agent_event("hitl_response", {
            "schema": "megatron.hitl.v1",
            "session_id": session_id,
            "request_id": request_id,
            "status": data["status"]
        })
        return {"status": "success"}

    def run(self, host="0.0.0.0", port=8000):
        logger.info(f"Starting HTTP API on http://{host}:{port}")
        uvicorn.run(self.app, host=host, port=port)

