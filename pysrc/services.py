"""Service facades and health-check infrastructure — thin wrappers over YuanGeAgent.

All stateful context management lives in agent.AgentContextManager (single source of truth).
This module only exposes typed service facades for use by API handlers and tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import redis.asyncio as redis

if TYPE_CHECKING:
    from agent import YuanGeAgent

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """Process-wide singleton service registry."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._services = {}
        return cls._instance

    def register(self, service_cls, instance=None):
        service = instance if instance is not None else service_cls()
        self._services[service_cls] = service
        return service

    def get(self, service_cls):
        if service_cls not in self._services:
            self.register(service_cls)
        return self._services[service_cls]


# ═══════════════════════════════════════════════════════════
#  Service Facades
# ═══════════════════════════════════════════════════════════

class MemoryService:
    def __init__(self, agent: YuanGeAgent):
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
    def __init__(self, agent: YuanGeAgent):
        self._agent = agent

    async def run_code(self, filename: str, code: str, session_id: str) -> dict:
        return await self._agent.execute_write_and_run(filename, "", code, session_id)

    async def run_command(self, command: str) -> dict:
        return await self._agent.execute_system_cmd(command)

    def get_cpu_time_limit(self) -> int:
        return getattr(self._agent.runtime, 'cpu_time_limit_sec', 3600)


class ContextService:
    def __init__(self, agent: YuanGeAgent):
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
    def __init__(self, agent: YuanGeAgent):
        self._agent = agent

    def list_skill_names(self) -> List[str]:
        return list(self._agent.loaded_skills.keys())

    async def reload(self):
        await self._agent._load_skills()

    async def install_from_github(self, repo_url: str, skill_name: str, session_id: str = None):
        from skill import InstallGithubSkillTool
        return await InstallGithubSkillTool(self._agent).execute(repo_url, skill_name, session_id)

    async def search_market(self, keyword: str):
        from skill import SearchSkillMarketTool
        return await SearchSkillMarketTool(self._agent).execute(keyword)

    def resolve_skill(self, skill_name: str):
        from skill import RunSkillScriptTool
        return RunSkillScriptTool(self._agent)._resolve_skill(skill_name)

    def find_entry_script(self, skill_name: str, skill_info: dict):
        from skill import RunSkillScriptTool
        return RunSkillScriptTool(self._agent)._find_entry_script(skill_name, skill_info)

    async def save_skill(self, skill_name: str, description: str, code: str, parameters: dict = None):
        from skill import SaveAsSkillTool
        return await SaveAsSkillTool(self._agent).execute(skill_name, description, code, parameters)

    async def promote_script_candidate(self, script_hash: str = None, skill_name: str = None,
                                       description: str = None, force: bool = False,
                                       session_id: str = "default"):
        return await self._agent.promote_script_candidate(script_hash, session_id, skill_name, description, force)

    async def backup_skill(self, skill_name: str):
        from skill import BackupSkillTool
        return await BackupSkillTool(self._agent).execute(skill_name)

    async def restore_skill(self, skill_name: str, backup_path: str = None):
        from skill import RestoreSkillTool
        return await RestoreSkillTool(self._agent).execute(skill_name, backup_path)

    async def git_reset_skill(self, skill_name: str):
        from skill import GitResetSkillTool
        return await GitResetSkillTool(self._agent).execute(skill_name)


class SubagentService:
    def __init__(self, agent: YuanGeAgent):
        self._agent = agent

    async def spawn(self, subtasks: List[str], session_id: str = "default") -> List[str]:
        return await self._agent.spawn_subagents(subtasks, parent_session_id=session_id)


class ToolRegistryService:
    def __init__(self, agent: YuanGeAgent):
        self._agent = agent

    async def register(self, tool_name: str, desc: str, schema_str: str, code: str) -> dict:
        return await self._agent.execute_register_tool(tool_name, desc, schema_str, code)


class SchedulerService:
    def __init__(self, agent: YuanGeAgent):
        self._agent = agent

    async def manage_task(self, action: str, task_prompt: str = None, cron_expr: str = None,
                          job_id: str = None, channel: str = "notification",
                          session_id: str = None) -> dict:
        from skill import ScheduleTaskTool
        return await ScheduleTaskTool(self._agent).execute(
            action, task_prompt, cron_expr, job_id, channel, session_id)


class ConfirmationService:
    def __init__(self, agent: YuanGeAgent):
        self._agent = agent

    async def request_confirmation(self, session_id: str, prompt: str,
                                   script_hash: str = None, code_preview: str = None) -> bool:
        return await self._agent._request_user_confirmation(prompt, script_hash, code_preview, session_id)


class ClinicalService:
    def __init__(self, agent: YuanGeAgent):
        self._agent = agent

    async def update_rule(self, rule: str) -> dict:
        return await self._agent.execute_update_clinical_rule(rule)


# ═══════════════════════════════════════════════════════════
#  Backward-compatibility re-export
# ═══════════════════════════════════════════════════════════
# AgentContextManager was previously duplicated here.  It now lives solely in
# agent.py.  Keep a lazy reference so that existing imports from services
# (if any) continue to work until callers are updated.

def __getattr__(name: str):
    if name == "AgentContextManager":
        from agent import AgentContextManager as _ACM
        return _ACM
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ═══════════════════════════════════════════════════════════
#  Service Health & Reconnection
# ═══════════════════════════════════════════════════════════

class ServiceHealthChecker:
    """Periodically check backend services and attempt reconnection."""

    def __init__(self, config: dict):
        self.config = config
        self.last_check: dict[str, dict] = {}
        self._redis_client: redis.Redis | None = None

    async def check_redis(self) -> dict:
        """Ping Redis and return status."""
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
    ) -> redis.Redis | None:
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
                logger.warning(
                    f"Redis reconnect attempt {attempt}/{max_retries} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
        logger.error(f"Redis reconnection failed after {max_retries} attempts")
        return None
