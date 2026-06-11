import os
import ast
import copy
import json
import hashlib
import hmac
import asyncio
import logging
import shlex
import aiofiles
import time
import re
import platform
import socket
import sys
import argparse
import uuid
import urllib.parse
import ipaddress
import numpy as np
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional
from pathlib import Path
from openai import AsyncOpenAI
import redis.asyncio as redis
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.redis import RedisJobStore
from decision_tracker import DecisionTracker
from model_tier import (
    detect_model_tier, get_tier_config, MODEL_TIER_CONFIG,
    adapt_system_rules, adapt_execution_contract, adapt_tool_schema,
    suggest_subtasks, TierConfig,
)

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import psutil
except ImportError:
    psutil = None

from memory import MemoryRecallEngine
from dialogue_dehydrator import KnowledgePipeline
from runtime_engine import PythonRuntime, ToolManager, ExtensionTool
from confirmation_state import ConfirmationStateTracker
from conversation_flow import ConversationFlow
from text_tool_calls import TextToolCallParser
from evolution import EvolutionError, EvolutionStore

from skill import (
    SearchMemoryTool, MemorizeFactTool, AmendMemoryTool, UpdateCoreMemoryTool,
    UpdateClinicalRuleTool, ExecuteSystemCommandTool, WriteAndExecuteScriptTool,
    RegisterNewToolTool, DelegateToSubagentsTool, InstallGithubSkillTool, SearchSkillMarketTool,
    DelegateToRemoteAgentTool,
    RunSkillScriptTool, ReloadSkillsTool, SaveAsSkillTool, ScheduleTaskTool,
    PromoteScriptToSkillTool, BackupSkillTool, RestoreSkillTool, GitResetSkillTool,
    ProposeEvolutionTool, OpenAPIBridge, MCPServerBridge, SmartSkillAdapterTool
)

try:
    import tomllib
except ImportError:
    import tomli as tomllib

import yaml

try:
    from fastapi import FastAPI, Request, BackgroundTasks, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = BASE_DIR / "workspace"
LOG_DIR = BASE_DIR.parent / "log"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "agent.log"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    logger.addHandler(console_handler)
    try:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(file_handler)
    except OSError as e:
        logger.warning(f"File logging disabled: {e}")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

AUTO_CONFIRM = os.environ.get("AUTO_CONFIRM", "0").lower() in ("1", "true", "yes")

DEFAULT_LLM_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o4-mini"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "qwen": {
        "label": "通义千问 / Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long"],
    },
    "moonshot": {
        "label": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    },
    "zhipu": {
        "label": "智谱 / Zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "models": ["glm-4-flash", "glm-4-plus", "glm-4-air"],
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "model": "MiniMax-Text-01",
        "models": ["MiniMax-Text-01", "MiniMax-M1"],
    },
    "stepfun": {
        "label": "阶跃星辰 / Stepfun",
        "base_url": "https://api.stepfun.com/v1",
        "model": "step-2-mini",
        "models": ["step-2-mini", "step-2-16k", "step-1-8k"],
    },
    "siliconflow": {
        "label": "硅基流动 / SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1"],
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "models": ["openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash-001"],
    },
}

def load_config(config_path: str = "model.toml") -> dict:
    base_dir = Path(__file__).resolve().parent
    config_file = base_dir / config_path
    if not config_file.exists():
        raise FileNotFoundError(f"Config not found: {config_file}")
    with open(config_file, "rb") as f:
        config_data = tomllib.load(f)
    llm_block = config_data.get("llm", {})
    active_provider = llm_block.get("active_provider", "openai")
    legacy_api_key = llm_block.get("api_key", "")
    legacy_base_url = llm_block.get("base_url")
    legacy_model = llm_block.get("model")
    legacy_extra_params = llm_block.get("extra_params", {})
    provider_configs = {}
    for provider_name, defaults in DEFAULT_LLM_PROVIDERS.items():
        file_config = llm_block.get(provider_name, {})
        if not isinstance(file_config, dict):
            file_config = {}
        env_prefix = f"MEGATRON_{provider_name.upper()}"
        provider_configs[provider_name] = {
            "label": file_config.get("label") or defaults.get("label") or provider_name,
            "api_key": (
                os.environ.get(f"{env_prefix}_API_KEY")
                or (os.environ.get("OPENAI_API_KEY") if provider_name == active_provider else "")
                or file_config.get("api_key", "")
                or (legacy_api_key if provider_name == active_provider else "")
            ),
            "base_url": (
                os.environ.get(f"{env_prefix}_BASE_URL")
                or (os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") if provider_name == active_provider else "")
                or file_config.get("base_url")
                or (legacy_base_url if provider_name == active_provider else "")
                or defaults.get("base_url")
            ),
            "model": (
                os.environ.get(f"{env_prefix}_MODEL")
                or (os.environ.get("OPENAI_MODEL") if provider_name == active_provider else "")
                or file_config.get("model")
                or (legacy_model if provider_name == active_provider else "")
                or defaults.get("model")
            ),
            "models": file_config.get("models") or defaults.get("models", []),
            "extra_params": file_config.get("extra_params", legacy_extra_params if provider_name == active_provider else {}),
        }
    for provider_name, file_config in llm_block.items():
        if provider_name == "active_provider" or provider_name in provider_configs or not isinstance(file_config, dict):
            continue
        env_prefix = f"MEGATRON_{provider_name.upper()}"
        provider_configs[provider_name] = {
            "label": file_config.get("label") or provider_name,
            "api_key": os.environ.get(f"{env_prefix}_API_KEY") or file_config.get("api_key", ""),
            "base_url": os.environ.get(f"{env_prefix}_BASE_URL") or file_config.get("base_url"),
            "model": os.environ.get(f"{env_prefix}_MODEL") or file_config.get("model", "gpt-4o-mini"),
            "models": file_config.get("models") or [file_config.get("model", "gpt-4o-mini")],
            "extra_params": file_config.get("extra_params", {}),
        }
    if active_provider not in provider_configs:
        active_provider = "openai"
    provider_config = provider_configs.get(active_provider, provider_configs["openai"])
    api_key = provider_config.get("api_key", "")
    base_url = provider_config.get("base_url")
    model = provider_config.get("model", "gpt-4o-mini")
    postgres_config = (
        config_data.get("postgres")
        or config_data.get("postgresql")
        or config_data.get("pgvector")
        or {}
    )
    return {
        "llm": {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "extra_params": provider_config.get("extra_params", {})
        },
        "llm_provider": active_provider,
        "llm_providers": provider_configs,
        "redis": config_data.get("redis", {}),
        "postgres": postgres_config,
        "postgresql": config_data.get("postgresql", postgres_config),
        "neo4j": config_data.get("neo4j", {}),
        "embedding": config_data.get("embedding", {}),
        "rerank": config_data.get("rerank", {}),
        "experts": config_data.get("experts", {}),
        "session": config_data.get("session", {}),
        "runtime": config_data.get("runtime", {}),
        "permissions": config_data.get("permissions", {}),
        "swarm": config_data.get("swarm", {}),
        "secrets": config_data.get("secrets", {}),
        "filesystem": config_data.get("filesystem", {}),
        "gateway": config_data.get("gateway", {}),
        "integrations": config_data.get("integrations", {})
    }

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

    async def add_history(self, session_id: str, role: str, content: str, max_len: int = 20):
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

class YuanGeAgent:
    def __init__(self, config: dict):
        self.config = config
        self.ctx = AgentContextManager(config)
        self.memory_engine = MemoryRecallEngine(config)
        self.dehydrator = KnowledgePipeline(config, concurrency=1)
        self.workspace_dir = str(WORKSPACE_DIR)
        os.makedirs(self.workspace_dir, exist_ok=True)
        self.tools_dir = os.path.join(self.workspace_dir, "tools")
        os.makedirs(self.tools_dir, exist_ok=True)
        self.runtime = PythonRuntime(self.workspace_dir, config)
        self.tool_manager = ToolManager()
        self.evolution_store = EvolutionStore(BASE_DIR.parent)
        self.skill_docs = {}
        self.loaded_skills = {}
        self.skill_embeddings = {}
        self.mcp_bridges = []
        self.broadcast_event = None
        self._ledger_observer = None
        self._ledger_sync_engine = None
        self._background_thinker_task = None
        self._last_activity_at = time.time()

        self.memory_service = MemoryService(self)
        self.runtime_service = RuntimeService(self)
        self.context_service = ContextService(self)
        self.skills_service = SkillsService(self)
        self.subagent_service = SubagentService(self)
        self.tool_registry_service = ToolRegistryService(self)
        self.scheduler_service = SchedulerService(self)
        self.confirmation_service = ConfirmationService(self)
        self.clinical_service = ClinicalService(self)

        redis_cfg = config.get("redis", {})
        redis_password = redis_cfg.get("password")
        jobstores = {
            'default': RedisJobStore(
                host=redis_cfg.get("host", "localhost"),
                port=redis_cfg.get("port", 6379),
                password=redis_password if redis_password else None,
                db=redis_cfg.get("db", 0)
            )
        }
        self.scheduler = AsyncIOScheduler(jobstores=jobstores)

        runtime_cfg = config.get("runtime", {})

        def runtime_int(key: str, default: int) -> int:
            try:
                return int(runtime_cfg.get(key, default))
            except Exception:
                return default

        def runtime_bool(key: str, default: bool = False) -> bool:
            value = runtime_cfg.get(key, default)
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes", "on")
            return bool(value)

        def runtime_mode(key: str, default: str = "auto") -> str:
            value = runtime_cfg.get(key, default)
            if isinstance(value, bool):
                return "on" if value else "off"
            value = str(value or default).strip().lower()
            if value in ("1", "true", "yes", "on", "always"):
                return "on"
            if value in ("0", "false", "no", "off", "never", "disabled"):
                return "off"
            return value or default

        self.max_agent_steps = runtime_int("max_agent_steps", 30)
        # Token-based context budget (not message count). Default 64K tokens.
        # Supports "64K", "128K", "32K", or raw integer token counts.
        raw_budget = runtime_cfg.get("context_window_budget", "64K")
        if isinstance(raw_budget, str) and raw_budget.upper().endswith("K"):
            self.context_token_budget = int(raw_budget.upper().replace("K", "")) * 1024
        else:
            self.context_token_budget = int(raw_budget) if raw_budget else 65536
        # Legacy compat: context_window_size still used by some paths
        self.context_window_size = max(runtime_int("context_window_size", 16), 32)

        for tool_cls in [
            SearchMemoryTool, MemorizeFactTool, AmendMemoryTool, UpdateCoreMemoryTool,
            UpdateClinicalRuleTool, ExecuteSystemCommandTool, WriteAndExecuteScriptTool,
            RegisterNewToolTool, DelegateToSubagentsTool, DelegateToRemoteAgentTool,
            InstallGithubSkillTool, SearchSkillMarketTool,
            RunSkillScriptTool, ReloadSkillsTool, SaveAsSkillTool, ScheduleTaskTool,
            PromoteScriptToSkillTool, ProposeEvolutionTool, BackupSkillTool, RestoreSkillTool, GitResetSkillTool
        ]:
            self.tool_manager.register(tool_cls(self))

        self._load_extension_tools()
        self.skills_dir = BASE_DIR / "skills"
        self.skills_dir.mkdir(exist_ok=True)
        experts_cfg = config.get("experts", {})
        self.domain_experts = {}
        self.domain_meta = {}
        for domain_name, domain_cfg in experts_cfg.items():
            self.domain_experts[domain_name] = domain_cfg.get("experts", [])
            self.domain_meta[domain_name] = {
                "description": domain_cfg.get("domain_description", ""),
                "keywords": domain_cfg.get("domain_keywords", []),
                "system_prompt": domain_cfg.get("system_prompt", "")
            }
        if "general" not in self.domain_experts:
            self.domain_experts["general"] = [{"id": "Analyst_AI", "role": "Objective Analyst"}]
            self.domain_meta["general"] = {"description": "", "keywords": [], "system_prompt": ""}
        self.llm_provider = config.get("llm_provider", "openai")
        self.model = "gpt-4o-mini"
        self.extra_params = {}
        self.client = None
        self.configure_llm(self.llm_provider, config.get("llm", {}).get("model"))

        # ── Model tier detection ──
        self.model_tier = detect_model_tier(self.model)
        self.tier_config: TierConfig = get_tier_config(self.model)
        # Apply tier-based limits (can be overridden by explicit runtime config)
        if not runtime_cfg.get("context_window_budget"):
            self.context_token_budget = self.tier_config.context_token_budget
        if not runtime_cfg.get("max_agent_steps"):
            self.max_agent_steps = self.tier_config.max_agent_steps
        self.max_prompt_skills = min(
            getattr(self, 'max_prompt_skills', 8),
            self.tier_config.max_tools_in_prompt,
        )
        logger.info(
            "Model tier: %s (%s) — %dK ctx, %d tools, %d repair attempts, %s rules",
            self.model_tier, self.tier_config.label,
            self.tier_config.context_token_budget // 1024,
            self.tier_config.max_tools_in_prompt,
            self.tier_config.max_repair_attempts,
            self.tier_config.system_rules_mode,
        )
        self.rules_file = BASE_DIR / "CLINICAL_RULES.md"
        self._conflict_detector_task = None
        self.fs_config = config.get("filesystem", {})
        self.allowed_paths = self.fs_config.get("allowed_paths", [])
        self.allow_mkdir = self.fs_config.get("allow_mkdir", False)
        self.allowed_cmds = set(runtime_cfg.get("allowed_cmds", ['ping', 'echo', 'date', 'dir', 'ls', 'yt-dlp', 'wget', 'curl']))
        self.auto_skill_promotion = runtime_bool("auto_skill_promotion", False)
        self.enable_memory_hot_reload = runtime_bool("enable_memory_hot_reload", False)
        self.dependency_audit_enabled = runtime_bool("dependency_audit_enabled", True)
        self.dependency_audit_ttl_seconds = runtime_int("dependency_audit_ttl_seconds", 86400)
        self.dependency_audit_fail_closed = str(runtime_cfg.get("dependency_audit_failure_policy", "confirm")).lower() == "block"
        self.enable_background_thinker = runtime_bool("enable_background_thinker", False)
        self.background_thinker_interval_sec = max(60, runtime_int("background_thinker_interval_sec", 1800))
        self.background_thinker_idle_sec = max(60, runtime_int("background_thinker_idle_sec", 900))
        self.skill_budget_mode = runtime_mode("skill_budget_mode", "auto")
        self.top_k_skills = max(1, runtime_int("top_k_skills", 3))
        self.max_prompt_skills = max(self.top_k_skills, runtime_int("max_prompt_skills", 8))
        if "expert_debate_mode" in runtime_cfg:
            self.expert_debate_mode = runtime_mode("expert_debate_mode", "auto")
        else:
            self.expert_debate_mode = runtime_mode("enable_expert_debate", "auto")
        self.enable_expert_debate = self.expert_debate_mode != "off"
        self.expert_debate_min_chars = max(20, runtime_int("expert_debate_min_chars", 180))
        self.max_expert_opinions = max(0, runtime_int("max_expert_opinions", 2))
        self.max_subagents = max(1, runtime_int("max_subagents", 4))
        self.subagent_concurrency = max(1, min(self.max_subagents, runtime_int("subagent_concurrency", self.max_subagents)))
        self.permissions_cfg = config.get("permissions", {})
        self.skill_scope_overrides = self.permissions_cfg.get("skills", {})
        self.runtime_default_scopes = self.permissions_cfg.get("runtime_default_scopes", ["*"])
        self.subagent_default_scopes = self.permissions_cfg.get("subagent_default_scopes", ["*"])
        self.swarm_cfg = config.get("swarm", {})
        self.swarm_node_id = self.swarm_cfg.get("node_id") or socket.gethostname()
        peers = self.swarm_cfg.get("peers", [])
        self.swarm_peers = {str(peer.get("id")): peer for peer in peers if isinstance(peer, dict) and peer.get("id") and peer.get("base_url")}
        self.swarm_shared_secret = str(self.swarm_cfg.get("shared_secret") or config.get("secrets", {}).get("SWARM_SHARED_SECRET") or "")
        self.swarm_allowed_remote_scopes = self.swarm_cfg.get("allowed_remote_scopes", ["chat:delegate"])
        self.swarm_timeout_sec = runtime_int("swarm_timeout_sec", 120)
        self._console_locks: Dict[str, asyncio.Lock] = {}
        self._static_system_prompt_cache: Dict[str, str] = {}
        self._chat_turn_ttl_seconds = max(600, runtime_int("chat_turn_ttl_seconds", 600))
        self.confirmation_state = ConfirmationStateTracker(self.ctx.redis, self._chat_turn_ttl_seconds, logger)
        self.conversation_flow = ConversationFlow()
        self.text_tool_call_parser = TextToolCallParser()

    def configure_llm(self, provider: str = None, model: str = None):
        provider_id = str(provider or self.llm_provider or self.config.get("llm_provider", "openai")).strip().lower()
        providers = self.config.get("llm_providers", {}) or {}
        provider_cfg = providers.get(provider_id)
        if not provider_cfg:
            provider_id = self.config.get("llm_provider", "openai")
            provider_cfg = providers.get(provider_id) or self.config.get("llm", {})
        selected_model = str(model or provider_cfg.get("model") or self.model or "gpt-4o-mini").strip()
        api_key = str(provider_cfg.get("api_key") or "").strip()
        base_url = provider_cfg.get("base_url")
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = str(base_url)
        self.client = AsyncOpenAI(**client_kwargs)
        self.model = selected_model
        self.extra_params = provider_cfg.get("extra_params", {}) or {}
        self.llm_provider = provider_id

    def _persist_allowed_cmds(self, new_cmd: str):
        config_path = "model.toml"
        if not os.path.exists(config_path):
            return
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                content = f.read()
            cmd_list_str = '", "'.join(self.allowed_cmds)
            new_line = f'allowed_cmds = ["{cmd_list_str}"]'
            if "allowed_cmds" in content:
                content = re.sub(r'allowed_cmds\s*=\s*\[.*?\]', new_line, content, flags=re.DOTALL)
            else:
                if "[runtime]" in content:
                    content = content.replace('[runtime]', f'[runtime]\n{new_line}')
                else:
                    content += f'\n[runtime]\n{new_line}\n'
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass

    def _load_extension_tools(self):
        if not os.path.exists(self.tools_dir):
            return
        for item in os.listdir(self.tools_dir):
            tool_dir = os.path.join(self.tools_dir, item)
            if not os.path.isdir(tool_dir) or item == "venvs":
                continue
            versions_dir = os.path.join(tool_dir, "versions")
            if not os.path.exists(versions_dir):
                continue
            version_dirs = sorted([d for d in os.listdir(versions_dir) if os.path.isdir(os.path.join(versions_dir, d))], reverse=True)
            if not version_dirs:
                continue
            version_path = os.path.join(versions_dir, version_dirs[0])
            json_path = os.path.join(version_path, f"{item}.json")
            py_path = os.path.join(version_path, f"{item}.py")
            if not (os.path.exists(json_path) and os.path.exists(py_path)):
                continue
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    func_data = json.load(f).get("function", {})
                if func_data.get("name"):
                    ext_tool = ExtensionTool(func_data["name"], func_data.get("description", ""), func_data.get("parameters", {}), py_path, self.runtime)
                    self.tool_manager.register(ext_tool)
            except Exception:
                pass

    @staticmethod
    @staticmethod
    def _convert_parameters_to_schema(parameters: Any) -> dict:
        """Build a rich JSON Schema from parameter definitions.

        Supports: type, description, enum, pattern, examples, default, minimum,
        maximum, minLength, maxLength, items (for arrays), properties (for nested objects),
        and required marking.
        """
        if isinstance(parameters, dict):
            # Already a full JSON Schema
            if "type" in parameters and parameters["type"] == "object" and "properties" in parameters:
                return parameters
            properties = {}
            required = []
            for key, val in parameters.items():
                if isinstance(val, dict) and "type" in val:
                    prop = {"type": val["type"]}
                    if "description" in val:
                        prop["description"] = str(val["description"])
                    if "enum" in val and isinstance(val["enum"], list):
                        prop["enum"] = val["enum"]
                    if "pattern" in val:
                        prop["pattern"] = str(val["pattern"])
                    if "examples" in val and isinstance(val["examples"], list):
                        prop["examples"] = val["examples"]
                    if "default" in val:
                        prop["default"] = val["default"]
                    if "minimum" in val:
                        prop["minimum"] = val["minimum"]
                    if "maximum" in val:
                        prop["maximum"] = val["maximum"]
                    if "minLength" in val:
                        prop["minLength"] = val["minLength"]
                    if "maxLength" in val:
                        prop["maxLength"] = val["maxLength"]
                    if val["type"] == "array" and "items" in val:
                        prop["items"] = val["items"]
                    if val["type"] == "object" and "properties" in val:
                        prop["properties"] = YuanGeAgent._convert_parameters_to_schema(val["properties"])["properties"]
                    if val.get("required"):
                        required.append(key)
                    properties[key] = prop
                elif isinstance(val, str):
                    # Shorthand: "param_name": "type" → auto-generate
                    properties[key] = {"type": val, "description": key}
            if properties:
                schema: dict = {"type": "object", "properties": properties}
                if required:
                    schema["required"] = required
                return schema
        return {"type": "object", "properties": {}}

    def _iter_skill_dirs(self) -> List[Path]:
        skill_dirs = []
        if not self.skills_dir.exists():
            return skill_dirs
        for skill_md in self.skills_dir.rglob("SKILL.md"):
            skill_dir = skill_md.parent
            try:
                rel = skill_dir.relative_to(self.skills_dir)
            except ValueError:
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
                continue
            skill_dirs.append(skill_dir)
        return sorted(skill_dirs, key=lambda p: str(p.relative_to(self.skills_dir)).lower())

    def _infer_skill_category_from_path(self, skill_dir: Path, front_matter: dict) -> str:
        category = str(front_matter.get("category") or "").strip().lower()
        if category:
            return category
        try:
            rel = skill_dir.relative_to(self.skills_dir)
            if len(rel.parts) > 1:
                return rel.parts[0].lower()
        except ValueError:
            pass
        return "general"

    async def _load_skills(self):
        if not self.skills_dir.exists():
            return
        self.loaded_skills = {}
        self.skill_docs = {}
        for skill_dir in self._iter_skill_dirs():
            skill_md_path = skill_dir / "SKILL.md"
            try:
                async with aiofiles.open(skill_md_path, 'r', encoding='utf-8') as f:
                    md_content = await f.read()
            except Exception:
                continue

            md_content = md_content.lstrip("\ufeff")
            front_matter = {}
            body_text = md_content
            match = re.match(r'^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)', md_content, re.DOTALL | re.MULTILINE)
            if not match:
                logger.error(f"SKILL.md in {skill_dir} is missing YAML front matter. Skipping.")
                continue
            try:
                front_matter = yaml.safe_load(match.group(1)) or {}
                body_text = md_content[match.end():]
            except yaml.YAMLError as e:
                logger.error(f"YAML parse error in {skill_md_path}: {e}")
                continue

            skill_name = front_matter.get("name") or skill_dir.name
            description = front_matter.get("description", "").strip()
            parameters = front_matter.get("parameters", {})
            if isinstance(parameters, str):
                try:
                    parameters = json.loads(parameters)
                except Exception:
                    parameters = {}
            entry_function = front_matter.get("entry_function", "main")
            keywords = front_matter.get("keywords", [])
            capabilities = front_matter.get("capabilities", [])
            consumes = front_matter.get("consumes", {})
            produces = front_matter.get("produces", {})
            side_effects = front_matter.get("side_effects", [])
            risk = front_matter.get("risk", "")
            category = self._infer_skill_category_from_path(skill_dir, front_matter)
            category_keywords = front_matter.get("category_keywords", [])

            scripts_dir = skill_dir / "scripts"
            candidates = []
            if scripts_dir.exists():
                candidates.extend(scripts_dir.glob("*.py"))
            candidates.extend(skill_dir.glob("*.py"))
            entry_py = next((c for c in candidates if c.is_file()), None)
            if not entry_py:
                logger.warning(f"Skill {skill_name} has no Python entry script. Skipping.")
                continue

            schema = self._convert_parameters_to_schema(parameters)

            self.loaded_skills[skill_name] = {
                "description": description,
                "parameters": parameters,
                "dir": skill_dir,
                "body": None,
                "keywords": keywords,
                "capabilities": capabilities,
                "consumes": consumes,
                "produces": produces,
                "side_effects": side_effects,
                "risk": risk,
                "category": category,
                "category_keywords": category_keywords
            }
            self.skill_docs[skill_name] = body_text[:2000]

            skill_rel_path = str(skill_dir.relative_to(self.skills_dir)).replace("\\", "/")
            adapter = SmartSkillAdapterTool(
                skill_folder_name=skill_rel_path,
                function_name=entry_function,
                description=description,
                schema=schema,
                agent=self
            )
            self.tool_manager.register(adapter)

        if hasattr(self, 'memory_engine') and self.memory_engine.emb_model:
            for sname, info in self.loaded_skills.items():
                try:
                    param_text = json.dumps(info.get("parameters", {}), ensure_ascii=False)
                    keyword_text = " ".join([str(x) for x in info.get("keywords", []) or []])
                    doc_text = self.skill_docs.get(sname, "")[:2000]
                    manifest_text = json.dumps({
                        "capabilities": info.get("capabilities", []),
                        "consumes": info.get("consumes", {}),
                        "produces": info.get("produces", {}),
                        "side_effects": info.get("side_effects", []),
                        "risk": info.get("risk", "")
                    }, ensure_ascii=False)
                    embed_text = (
                        f"Skill: {sname}\n"
                        f"Category: {info.get('category', 'general')}\n"
                        f"Description: {info.get('description', '')}\n"
                        f"Parameters: {param_text}\n"
                        f"Keywords: {keyword_text}\n"
                        f"Manifest: {manifest_text}\n"
                        f"Docs: {doc_text}"
                    )
                    emb_vector = await asyncio.to_thread(self.memory_engine.emb_model.encode, [embed_text])
                    self.skill_embeddings[sname] = np.array(emb_vector[0])
                except Exception:
                    pass

    async def initialize(self):
        await self.memory_engine.initialize()
        await self.dehydrator.__aenter__()
        self.dehydrator.start_workers()
        self._conflict_detector_task = asyncio.create_task(self._conflict_detection_loop())
        await self._load_skills()
        if not self.scheduler.running:
            self.scheduler.start()
        openapi_cfg = self.config.get("openapi", {})
        for api_name, api_info in openapi_cfg.items():
            try:
                with open(api_info["spec_path"], 'r') as f:
                    spec = json.load(f)
                bridge = OpenAPIBridge(self.tool_manager, api_info["base_url"], spec)
                bridge.register_all()
            except Exception:
                pass
        mcp_cfg = self.config.get("mcp_servers", {})
        for server_name, server_info in mcp_cfg.items():
            try:
                bridge = MCPServerBridge(
                    self.tool_manager,
                    server_command=server_info["command"],
                    server_args=server_info["args"]
                )
                await bridge.connect_and_register()
                self.mcp_bridges.append(bridge)
            except Exception:
                pass
        if self.enable_memory_hot_reload:
            await self._start_memory_ledger_watcher()
        if self.enable_background_thinker and not self._background_thinker_task:
            self._background_thinker_task = asyncio.create_task(self._background_thinker_loop())

    async def _background_thinker_loop(self):
        while True:
            try:
                await asyncio.sleep(self.background_thinker_interval_sec)
                if time.time() - self._last_activity_at < self.background_thinker_idle_sec:
                    continue
                await self._run_background_thought()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Background thinker iteration failed: {e}")

    async def _run_background_thought(self):
        try:
            patterns = await self.ctx.get_task_patterns()
            reward_stats = await self.ctx.get_tool_reward_stats(limit=12)
            recent_patterns = sorted(patterns, key=lambda item: item.get("last_seen", 0), reverse=True)[:8]
            prompt = (
                "Inspect these recent autonomous-agent workflow memories. "
                "Return one JSON object with keys: kind, summary, suggested_next_action, urgency, open_question. "
                "Do not propose external network access or file modifications unless clearly necessary. "
                "Prefer low-cost maintenance such as identifying stale memories, missing skills, repeated failures, or low-reward tools.\n"
                f"Patterns: {json.dumps(recent_patterns, ensure_ascii=False, default=str)}\n"
                f"Tool Reward Stats: {json.dumps(reward_stats, ensure_ascii=False, default=str)}"
            )
            res = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are the agent's low-priority background reflection process. Return JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                **self.extra_params
            )
            thought = json.loads(res.choices[0].message.content)
            thought["created_at"] = time.time()
            await self.ctx.redis.lpush("background_thoughts", json.dumps(thought, ensure_ascii=False))
            await self.ctx.redis.ltrim("background_thoughts", 0, 99)
            await self.ctx.redis.expire("background_thoughts", self.ctx.ttl_seconds * 3)
            if thought.get("open_question") or str(thought.get("urgency", "")).lower() in {"medium", "high"}:
                await self.ctx.redis.lpush("background_open_questions", json.dumps({
                    "question": thought.get("open_question") or thought.get("suggested_next_action") or thought.get("summary"),
                    "urgency": thought.get("urgency", "low"),
                    "kind": thought.get("kind", "background_reflection"),
                    "created_at": thought["created_at"]
                }, ensure_ascii=False))
                await self.ctx.redis.ltrim("background_open_questions", 0, 49)
                await self.ctx.redis.expire("background_open_questions", self.ctx.ttl_seconds * 3)
            await self.dehydrator.process("system_background", "Background Thought:\n" + json.dumps(thought, ensure_ascii=False))
            if self.broadcast_event:
                await self.broadcast_event("background_thought", {"summary": str(thought.get("summary", ""))[:300], "kind": thought.get("kind")})
        except Exception as e:
            logger.warning(f"Background thought failed: {e}")

    async def _start_memory_ledger_watcher(self):
        if self._ledger_observer:
            return
        try:
            from ledger_watcher import LedgerSync, LedgerHandler
            from watchdog.observers import Observer

            ledger_path = Path(getattr(self.dehydrator, "ledger_path", "MEMORY_LEDGER.md"))
            if not ledger_path.is_absolute():
                ledger_path = BASE_DIR / ledger_path
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.touch(exist_ok=True)
            self.dehydrator.ledger_path = str(ledger_path)

            sync_engine = LedgerSync(self.config)
            await sync_engine.connect()
            loop = asyncio.get_running_loop()
            handler = LedgerHandler(loop, sync_engine, str(ledger_path))
            observer = Observer()
            observer.schedule(handler, path=str(ledger_path.parent), recursive=False)
            observer.start()
            self._ledger_sync_engine = sync_engine
            self._ledger_observer = observer
            logger.info(f"Memory ledger hot-reload enabled: {ledger_path}")
            if self.broadcast_event:
                await self.broadcast_event("memory_hot_reload_started", {"ledger_path": str(ledger_path)})
        except Exception as e:
            logger.warning(f"Memory ledger hot-reload disabled: {e}")

    async def close(self):
        if self._background_thinker_task and not self._background_thinker_task.done():
            self._background_thinker_task.cancel()
            try:
                await self._background_thinker_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self._conflict_detector_task and not self._conflict_detector_task.done():
            self._conflict_detector_task.cancel()
            try:
                await self._conflict_detector_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self.scheduler.running:
            self.scheduler.shutdown()
        for bridge in self.mcp_bridges:
            try:
                await bridge.close()
            except Exception:
                pass
        if self._ledger_observer:
            try:
                self._ledger_observer.stop()
                await asyncio.to_thread(self._ledger_observer.join, 5)
            except Exception:
                pass
            self._ledger_observer = None
        if self._ledger_sync_engine:
            try:
                await self._ledger_sync_engine.close()
            except Exception:
                pass
            self._ledger_sync_engine = None
        await self.ctx.close()
        await self.memory_engine.close()
        await self.client.close()
        if hasattr(self.dehydrator, 'stop_workers'):
            await self.dehydrator.stop_workers()
        else:
            await asyncio.sleep(0.5)
        await self.dehydrator.__aexit__(None, None, None)

    async def scheduled_task_executor(self, session_id: str, prompt: str, channel: str = "notification"):
        if self.broadcast_event:
            await self.broadcast_event("scheduled_task_triggered", {"session_id": session_id, "prompt": prompt, "channel": channel})
        if channel == "notification":
            await self.ctx.push_notification(session_id, f"定时任务提醒：{prompt}")
        elif channel == "websocket" and self.broadcast_event:
            await self.broadcast_event("scheduled_task", {"session_id": session_id, "prompt": prompt})
        else:
            await self.chat(session_id, f"【定时任务】{prompt}")

    async def _request_user_confirmation(self, prompt: str, script_hash: str = None,
                                         code_preview: str = None, session_id: str = "default") -> bool:
        if AUTO_CONFIRM:
            return True
        if script_hash and await self.ctx.redis.sismember("trusted_scripts", script_hash):
            return True
        if os.environ.get("AGENT_NO_CONSOLE_CONFIRM") == "1" or not sys.stdin.isatty():
            return await self._request_web_only(session_id, prompt, code_preview)
        if session_id not in self._console_locks:
            self._console_locks[session_id] = asyncio.Lock()
        async with self._console_locks[session_id]:
            try:
                full_prompt = f"\n[Security] {prompt.strip()}\nAllow execution? (y/n/a/p to preview code): "
                while True:
                    ans = (await asyncio.to_thread(input, full_prompt)).strip().lower()
                    if ans == 'p' and code_preview is not None:
                        print(f"\n{code_preview}\n")
                        continue
                    if ans in ('a', 'always') and script_hash:
                        await self.ctx.redis.sadd("trusted_scripts", script_hash)
                        return True
                    if ans == 'y':
                        return True
                    if ans == 'n':
                        return False
            except:
                return False

    async def _request_web_only(self, session_id: str, prompt: str, code_preview: str) -> bool:
        pending = await self.confirmation_state.create_pending_request(session_id, prompt, code_preview, ttl_seconds=120)
        key = pending["key"]
        confirm_data = pending["data"]
        request_id = confirm_data["request_id"]
        await self.ctx.redis.publish(f"confirm_channel:{session_id}", json.dumps({"action": "new_request", "request_id": request_id}))
        if self.broadcast_event:
            await self.broadcast_event("hitl_request", {
                "schema": "megatron.hitl.v1",
                "request_id": request_id,
                "session_id": session_id,
                "prompt": prompt.strip(),
                "has_code_preview": bool(code_preview),
                "status": "pending"
            })
        deadline = time.time() + 60
        while time.time() < deadline:
            data = await self.confirmation_state.get_request(key)
            if data:
                if await self.confirmation_state.request_is_stale(session_id, data.get("turn_id")):
                    await self.confirmation_state.mark_denied(key, data, "superseded_by_new_chat_turn")
                    return False
                if data["status"] == "approved":
                    return True
                elif data["status"] == "denied":
                    return False
            await asyncio.sleep(0.5)
        await self.ctx.redis.delete(key)
        return False

    def _active_turn_key(self, session_id: str) -> str:
        return self.confirmation_state.active_turn_key(session_id)

    async def _get_active_chat_turn(self, session_id: str) -> Optional[str]:
        return await self.confirmation_state.get_active_turn(session_id)

    async def _set_active_chat_turn(self, session_id: str) -> str:
        return await self.confirmation_state.start_turn(session_id)

    async def _mark_confirmation_denied(self, key: str, data: dict, reason: str):
        await self.confirmation_state.mark_denied(key, data, reason)

    async def _cancel_pending_confirmations(self, session_id: str, reason: str) -> int:
        return await self.confirmation_state.cancel_pending(session_id, reason)

    def _scope_allowed(self, allowed_scopes: List[str], required_scope: str) -> bool:
        if "*" in allowed_scopes:
            return True
        if required_scope in allowed_scopes:
            return True
        namespace = required_scope.split(":", 1)[0]
        return f"{namespace}:*" in allowed_scopes

    def _scopes_cover(self, allowed_scopes: List[str], required_scopes: List[str]) -> tuple[bool, List[str]]:
        allowed = [str(scope) for scope in (allowed_scopes or [])]
        required = [str(scope) for scope in (required_scopes or []) if str(scope).strip()]
        missing = [scope for scope in required if not self._scope_allowed(allowed, scope)]
        return not missing, missing

    def _infer_skill_required_scopes(self, skill_name: str, skill_info: dict) -> List[str]:
        explicit = skill_info.get("required_scopes") or skill_info.get("scopes")
        if explicit:
            return explicit if isinstance(explicit, list) else [str(explicit)]
        text = self._skill_search_text(skill_name, skill_info)
        scopes = ["skill:execute"]
        if any(marker in text for marker in ["url", "http", "download", "api", "搜索", "检索", "下载", "抓取"]):
            scopes.append("network:read")
        if any(marker in text for marker in ["write", "save", "export", "download", "保存", "写入", "导出"]):
            scopes.append("fs:write")
        if any(marker in text for marker in ["delete", "remove", "reset", "删除", "恢复", "重置"]):
            scopes.append("fs:mutate")
        return sorted(set(scopes))

    def check_skill_scopes(self, skill_name: str, skill_info: dict, session_id: str = "default") -> tuple[bool, str, List[str]]:
        required = self._infer_skill_required_scopes(skill_name, skill_info)
        allowed = (
            self.skill_scope_overrides.get(skill_name)
            or self.permissions_cfg.get("session_scopes", {}).get(session_id or "default")
            or self.permissions_cfg.get("default_skill_scopes")
            or ["*"]
        )
        ok_scope, missing = self._scopes_cover(allowed, required)
        if not ok_scope:
            return False, f"Permission scope denied for skill '{skill_name}'. Missing scopes: {missing}", required
        return True, "", required

    def check_runtime_scopes(self, required_scopes: List[str], session_id: str = "default", source: str = "runtime") -> tuple[bool, str]:
        allowed = (
            self.permissions_cfg.get("session_scopes", {}).get(session_id or "default")
            or self.runtime_default_scopes
            or ["*"]
        )
        ok_scope, missing = self._scopes_cover(allowed, required_scopes)
        if not ok_scope:
            return False, f"Permission scope denied for {source}. Missing scopes: {missing}"
        return True, ""

    async def execute_memory_search(self, query: str, session_id: str) -> dict:
        try:
            emb_model = getattr(self.memory_engine, 'emb_model', None)
            q_vec = (await asyncio.to_thread(emb_model.encode, [query]))[0].tolist() if emb_model else [0.0]*self.memory_engine.embed_dim
            try:
                ent_res = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "system", "content": 'JSON: {"entities": []}'},
                              {"role": "user", "content": query}],
                    response_format={"type": "json_object"}, **self.extra_params)
                entities = json.loads(ent_res.choices[0].message.content).get("entities", [])
            except:
                entities = []
            episodic, semantic = await asyncio.gather(
                self.memory_engine.db.get_episodic_decay(q_vec, owner_id="shared"),
                self.memory_engine.db.get_semantic_graph(entities))
            all_candidates = list(set(episodic + semantic))
            reranker = getattr(self.memory_engine, 'reranker', None)
            if all_candidates and reranker:
                try:
                    scores = await asyncio.to_thread(reranker.predict, [[query, doc] for doc in all_candidates])
                    facts = [doc for doc, score in sorted(zip(all_candidates, scores), key=lambda x: x[1], reverse=True)[:self.memory_engine.top_k]]
                except:
                    facts = all_candidates[:self.memory_engine.top_k]
            else:
                facts = all_candidates[:self.memory_engine.top_k]
            return {"facts": facts}
        except Exception:
            return {"facts": []}

    async def execute_memorize_fact(self, fact: str) -> dict:
        try:
            emb_model = getattr(self.memory_engine, 'emb_model', None)
            vector = (await asyncio.to_thread(emb_model.encode, [fact]))[0].tolist() if emb_model else [0.0]*self.memory_engine.embed_dim
            async with self.memory_engine.db.pg_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO episodic_memory (text, embedding, owner_id, created_at) VALUES ($1, $2, $3, NOW())",
                    fact, vector, "shared")
            return {"status": "success", "message": f"Fact recorded: {fact}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def execute_amend_memory(self, target_fact: str) -> dict:
        deleted_pg, deleted_neo4j = 0, 0
        if getattr(self.memory_engine.db, 'pg_pool', None):
            try:
                emb_model = getattr(self.memory_engine, 'emb_model', None)
                emb_res = (await asyncio.to_thread(emb_model.encode, [target_fact])) if emb_model else [[0.0]*self.memory_engine.embed_dim]
                async with self.memory_engine.db.pg_pool.acquire() as conn:
                    record = await conn.fetchrow(
                        'SELECT id, text, (1.0 - (embedding <=> $1)) AS sim FROM episodic_memory ORDER BY embedding <=> $1 LIMIT 1;',
                        emb_res[0].tolist())
                    if record and record['sim'] > 0.70:
                        await conn.execute('DELETE FROM episodic_memory WHERE id = $1', record['id'])
                        deleted_pg += 1
            except:
                pass
        if getattr(self.memory_engine.db, 'neo4j', None):
            try:
                prompt = f'Extract two core entities (subject and object). Return JSON: {{"source": "entity1", "target": "entity2"}}. Fact: "{target_fact}"'
                ent_res = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}, **self.extra_params)
                rel_data = json.loads(ent_res.choices[0].message.content)
                if rel_data.get("source") and rel_data.get("target"):
                    async with self.memory_engine.db.neo4j.session() as session:
                        res = await session.run(
                            "MATCH (a)-[r]-(b) WHERE (a.id CONTAINS $src OR toString(a.name) CONTAINS $src) AND (b.id CONTAINS $tgt OR toString(b.name) CONTAINS $tgt) DELETE r RETURN count(r) as deleted_count",
                            src=rel_data["source"], tgt=rel_data["target"])
                        summary = await res.single()
                        if summary:
                            deleted_neo4j += summary['deleted_count']
            except:
                pass
        return {"status": "success", "message": f"Erased. Cleaned {deleted_pg} vectors, {deleted_neo4j} graph relations."}

    async def execute_update_core(self, session_id: str, updates_str: dict) -> dict:
        try:
            await self.ctx.update_core_memory(session_id, updates_str if isinstance(updates_str, dict) else json.loads(updates_str))
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def execute_update_clinical_rule(self, rule: str) -> dict:
        try:
            with open(self.rules_file, "a", encoding="utf-8") as f:
                f.write(f"- {rule}\n")
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_clinical_rules(self) -> str:
        if os.path.exists(self.rules_file):
            with open(self.rules_file, "r", encoding="utf-8") as f:
                return f.read().strip() or "No active rules."
        return "No active rules."

    @staticmethod
    def _sanitize_script(code: str) -> str:
        allowed_ranges = [
            (0x20, 0x7E), (0x4E00, 0x9FFF), (0x3000, 0x303F), (0xFF00, 0xFFEF),
            (0x0A, 0x0A), (0x0D, 0x0D), (0x09, 0x09),
        ]
        result = []
        for ch in code:
            cp = ord(ch)
            if any(start <= cp <= end for start, end in allowed_ranges):
                result.append(ch)
            else:
                result.append(' ')
        return ''.join(result)

    @staticmethod
    def _normalize_generated_skill_name(name: str, fallback: str = "generated_skill") -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", name or "").strip("_").lower()
        if not normalized:
            normalized = fallback
        if normalized and normalized[0].isdigit():
            normalized = f"skill_{normalized}"
        return normalized[:80]

    @staticmethod
    def _strip_code_fence(code: str) -> str:
        text = (code or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:python)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def _fallback_promoted_skill_code(self, code: str) -> str:
        source_literal = repr(code)
        return (
            "import json\n"
            "import sys\n\n"
            f"ORIGINAL_CODE = {source_literal}\n\n"
            "def _load_params():\n"
            "    if len(sys.argv) < 2 or not sys.argv[1].strip():\n"
            "        return {}\n"
            "    return json.loads(sys.argv[1])\n\n"
            "def main():\n"
            "    params = _load_params()\n"
            "    namespace = {\n"
            "        '__name__': '__main__',\n"
            "        '__file__': 'promoted_original.py',\n"
            "        'params': params,\n"
            "        'skill_params': params,\n"
            "    }\n"
            "    exec(compile(ORIGINAL_CODE, 'promoted_original.py', 'exec'), namespace)\n\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )

    def _fallback_promoted_skill_definition(self, candidate: dict, preferred_name: str = None, preferred_description: str = None) -> dict:
        filename = candidate.get("filename") or "script"
        script_hash = str(candidate.get("script_hash") or "")[:8]
        base_name = preferred_name or Path(filename).stem or f"generated_skill_{script_hash}"
        return {
            "skill_name": self._normalize_generated_skill_name(base_name, f"generated_skill_{script_hash or 'script'}"),
            "description": preferred_description or candidate.get("description") or f"Reusable skill promoted from successful script {filename}.",
            "category": "code",
            "keywords": ["generated", "script", "code"],
            "capabilities": ["execute", "transformer"],
            "consumes": {},
            "produces": {"stdout": {"type": "string"}},
            "side_effects": ["Runs promoted Python code in the skill runner."],
            "risk": "medium: generated from a previously approved script; review before using for sensitive data.",
            "parameters": {"type": "object", "properties": {}, "required": []},
            "code": self._fallback_promoted_skill_code(candidate.get("code") or "")
        }

    async def _infer_promoted_skill_definition(self, candidate: dict, preferred_name: str = None, preferred_description: str = None) -> dict:
        fallback = self._fallback_promoted_skill_definition(candidate, preferred_name, preferred_description)
        code = candidate.get("code") or ""
        output_preview = candidate.get("output_preview") or ""
        prompt = (
            "Convert this successful one-off Python script into one reusable Codex skill. "
            "Return one JSON object only. No markdown. "
            "Schema: {"
            "\"skill_name\": snake_case string, "
            "\"description\": string, "
            "\"category\": string, "
            "\"keywords\": string[], "
            "\"capabilities\": string[], "
            "\"consumes\": object, "
            "\"produces\": object, "
            "\"side_effects\": string[], "
            "\"risk\": string, "
            "\"parameters\": JSON schema object with type=object/properties/required, "
            "\"code\": complete Python script"
            "}. "
            "The code must read one JSON object from sys.argv[1], validate defaults locally, and print useful output. "
            "Keep network, filesystem, and shell behavior no broader than the original script. "
            "If the script cannot be cleanly parameterized, preserve behavior and accept an empty object.\n\n"
            f"Preferred name: {preferred_name or ''}\n"
            f"Preferred description: {preferred_description or ''}\n"
            f"Original filename: {candidate.get('filename')}\n"
            f"Original description: {candidate.get('description')}\n"
            f"Successful output preview:\n{output_preview[:2000]}\n\n"
            f"Original code:\n{code[:12000]}"
        )
        try:
            res = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You turn successful automation scripts into durable, well-scoped Codex skills. Return JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                **self.extra_params
            )
            definition = json.loads(res.choices[0].message.content)
            if not isinstance(definition, dict):
                return fallback
            merged = fallback.copy()
            merged.update({k: v for k, v in definition.items() if v not in (None, "", [], {})})
            merged["skill_name"] = self._normalize_generated_skill_name(merged.get("skill_name"), fallback["skill_name"])
            merged["description"] = str(merged.get("description") or fallback["description"]).strip()
            merged["parameters"] = self._convert_parameters_to_schema(merged.get("parameters") or fallback["parameters"])
            merged["code"] = self._strip_code_fence(str(merged.get("code") or fallback["code"]))
            try:
                compile(merged["code"], f"{merged['skill_name']}.py", "exec")
            except Exception:
                merged["code"] = fallback["code"]
            return merged
        except Exception as e:
            logger.warning(f"Promoted skill inference fell back to raw wrapper: {e}")
            return fallback

    async def _record_script_skill_candidate(
        self,
        session_id: str,
        filename: str,
        description: str,
        code: str,
        script_hash: str,
        result: dict
    ):
        candidate = {
            "script_hash": script_hash,
            "session_id": session_id or "default",
            "filename": filename,
            "description": description,
            "code": code,
            "output_preview": str(result.get("output") or result.get("message") or "")[:4000],
            "download_paths": result.get("download_paths", []),
            "created_at": time.time()
        }
        try:
            await self.ctx.record_script_skill_candidate(candidate)
            result["promotion_candidate"] = {
                "script_hash": script_hash,
                "message": "Script succeeded and was recorded as a skill candidate. After confirming it is useful, call promote_script_to_skill with this hash."
            }
            if self.broadcast_event:
                await self.broadcast_event("skill_candidate", {
                    "session_id": session_id,
                    "script_hash": script_hash,
                    "filename": filename,
                    "description": description[:200]
                })
        except Exception as e:
            logger.warning(f"Failed to record script skill candidate: {e}")

    async def _auto_promote_script_candidate(self, script_hash: str, session_id: str):
        prompt = (
            "The just-executed script finished successfully. Promote it into a reusable skill now? "
            "Choose yes only if this script is a generally useful capability, not a one-off artifact."
        )
        try:
            confirmed = await self._request_user_confirmation(
                prompt=prompt,
                script_hash=f"promote_{script_hash}",
                code_preview=None,
                session_id=session_id or "default"
            )
            if confirmed:
                await self.promote_script_candidate(script_hash=script_hash, session_id=session_id)
        except Exception as e:
            logger.warning(f"Auto skill promotion failed: {e}")

    async def promote_script_candidate(
        self,
        script_hash: str = None,
        session_id: str = "default",
        preferred_name: str = None,
        preferred_description: str = None,
        force: bool = False
    ) -> dict:
        candidate = await self.ctx.get_script_skill_candidate(script_hash, session_id or "default")
        if not candidate:
            return {"status": "error", "message": "No script skill candidate found for this session/hash.", "completed": True}
        definition = await self._infer_promoted_skill_definition(candidate, preferred_name, preferred_description)
        skill_name = self._normalize_generated_skill_name(definition.get("skill_name"), "generated_skill")
        skill_root = self.skills_dir / "generated"
        skill_dir = skill_root / skill_name
        if skill_dir.exists() and not force:
            suffix = str(candidate.get("script_hash") or hashlib.sha256((candidate.get("code") or "").encode()).hexdigest())[:8]
            skill_name = self._normalize_generated_skill_name(f"{skill_name}_{suffix}", "generated_skill")
            skill_dir = skill_root / skill_name
        scripts_dir = skill_dir / "scripts"
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            scripts_dir.mkdir(exist_ok=True)
            parameters = self._convert_parameters_to_schema(definition.get("parameters") or {})
            metadata = {
                "name": skill_name,
                "description": definition.get("description") or f"Generated skill {skill_name}.",
                "category": definition.get("category") or "code",
                "keywords": definition.get("keywords") or ["generated", "script"],
                "capabilities": definition.get("capabilities") or ["execute"],
                "consumes": definition.get("consumes") or {},
                "produces": definition.get("produces") or {},
                "side_effects": definition.get("side_effects") or ["Runs promoted Python code."],
                "risk": definition.get("risk") or "medium",
                "parameters": parameters,
                "entry_function": "main"
            }
            front_matter_lines = ["---"]
            for key, value in metadata.items():
                front_matter_lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
            front_matter_lines.append("---")
            skill_md = (
                "\n".join(front_matter_lines)
                + f"\n\n# {skill_name}\n\n"
                + f"{metadata['description']}\n\n"
                + "Generated from a successful write_and_execute_script run. Review before broad reuse.\n"
            )
            code = self._strip_code_fence(definition.get("code") or "")
            compile(code, f"{skill_name}.py", "exec")
            async with aiofiles.open(skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
                await f.write(skill_md)
            async with aiofiles.open(scripts_dir / "main.py", "w", encoding="utf-8") as f:
                await f.write(code)
            await self._load_skills()
            return {
                "status": "success",
                "message": f"Promoted script candidate into skill '{skill_name}'.",
                "skill_name": skill_name,
                "skill_dir": str(skill_dir),
                "completed": True
            }
        except Exception as e:
            return {"status": "error", "message": f"Skill promotion failed: {e}", "completed": True}

    @staticmethod
    def _normalize_dependency_spec(dep: str) -> str:
        dep = str(dep or "").strip()
        dep = re.split(r"\s*;\s*", dep, maxsplit=1)[0].strip()
        dep = re.split(r"\s*(?:==|>=|<=|~=|!=|>|<|\[)", dep, maxsplit=1)[0].strip()
        return dep.lower().replace("_", "-")

    def _heuristic_dependency_risk(self, deps: List[str]) -> Optional[str]:
        known_packages = {
            "numpy", "pandas", "requests", "openai", "pillow", "opencv-python", "pyyaml",
            "beautifulsoup4", "scikit-learn", "scikit-image", "scipy", "matplotlib",
            "seaborn", "torch", "sentence-transformers", "fastapi", "uvicorn", "redis",
            "asyncpg", "neo4j", "pgvector", "pypdf", "pymupdf", "python-docx",
            "python-pptx", "openpyxl", "lxml", "aiohttp", "aiofiles", "watchdog",
            "python-dotenv", "yt-dlp", "networkx", "cryptography"
        }
        suspicious_names = []
        for dep in deps:
            name = self._normalize_dependency_spec(dep)
            if not name:
                continue
            if re.search(r"(?:^|[-_])(os|sys|subprocess|socket|shutil)(?:$|[-_])", name):
                suspicious_names.append(dep)
                continue
            for known in known_packages:
                distance = self._levenshtein_distance(name, known, max_distance=2)
                if 0 < distance <= 2 and name not in known_packages:
                    suspicious_names.append(dep)
                    break
        if suspicious_names:
            return "Possible typosquatting or suspicious stdlib-like package names: " + ", ".join(sorted(set(suspicious_names)))
        return None

    @staticmethod
    def _levenshtein_distance(a: str, b: str, max_distance: int = 2) -> int:
        if abs(len(a) - len(b)) > max_distance:
            return max_distance + 1
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            row_min = i
            for j, cb in enumerate(b, 1):
                val = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
                cur.append(val)
                row_min = min(row_min, val)
            if row_min > max_distance:
                return max_distance + 1
            prev = cur
        return prev[-1]

    async def audit_dependency_safety(
        self,
        dependencies: List[str],
        session_id: str = "default",
        source: str = "runtime",
        context: str = ""
    ) -> tuple[bool, str]:
        clean_deps = sorted({str(dep).strip() for dep in dependencies if str(dep).strip()})
        if not clean_deps or not self.dependency_audit_enabled:
            return True, ""
        heuristic_reason = self._heuristic_dependency_risk(clean_deps)
        if heuristic_reason:
            if self.broadcast_event:
                await self.broadcast_event("dependency_audit", {"source": source, "risk": "high", "dependencies": clean_deps, "reason": heuristic_reason})
            return False, heuristic_reason
        cache_key = "dependency_audit:" + hashlib.sha256(json.dumps(clean_deps, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        try:
            cached = await self.ctx.redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                risk = str(data.get("risk", "low")).lower()
                reason = data.get("reason", "")
                if risk == "low" and not data.get("suggest_block"):
                    return True, reason
                if risk == "high" or data.get("suggest_block"):
                    return False, reason or "Dependency audit blocked this dependency set."
        except Exception:
            pass
        prompt = (
            "Audit this Python dependency set before an autonomous agent runs pip install. "
            "Return JSON only: {\"risk\":\"low|medium|high\", \"reason\":\"...\", \"suggest_block\": boolean}. "
            "Check typosquatting, suspicious stdlib-like names, known risky packages, install-time code risk, "
            "network/downloader packages, and whether the dependencies look broader than the task needs.\n\n"
            f"Source: {source}\n"
            f"Dependencies:\n" + "\n".join(f"- {dep}" for dep in clean_deps) + "\n\n"
            f"Context:\n{context[:3000]}"
        )
        try:
            res = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a conservative software supply-chain security reviewer. Return JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                **self.extra_params
            )
            audit = json.loads(res.choices[0].message.content)
        except Exception as e:
            reason = f"Dependency audit failed: {e}"
            if self.dependency_audit_fail_closed:
                return False, reason
            confirmed = await self._request_user_confirmation(
                prompt=reason + "\nAllow dependency installation anyway?",
                session_id=session_id
            )
            return confirmed, "User approved dependency install after audit failure." if confirmed else "User denied dependency install after audit failure."
        risk = str(audit.get("risk", "low")).lower()
        reason = str(audit.get("reason") or "")
        suggest_block = bool(audit.get("suggest_block", False))
        try:
            await self.ctx.redis.setex(cache_key, self.dependency_audit_ttl_seconds, json.dumps(audit, ensure_ascii=False))
        except Exception:
            pass
        if self.broadcast_event:
            await self.broadcast_event("dependency_audit", {"source": source, "risk": risk, "dependencies": clean_deps, "reason": reason[:300]})
        if risk == "high" or suggest_block:
            return False, reason or "Dependency audit marked this dependency set as high risk."
        if risk == "medium":
            confirmed = await self._request_user_confirmation(
                prompt=f"Dependency audit reported medium risk for {', '.join(clean_deps)}:\n{reason}\nAllow installation?",
                session_id=session_id
            )
            if not confirmed:
                return False, "User denied medium-risk dependency installation."
        return True, reason

    async def execute_write_and_run(self, filename: str, description: str, code: str, session_id: str) -> dict:
        code = self._sanitize_script(code)
        script_hash = hashlib.sha256(code.encode()).hexdigest()
        prompt = f"\nRequest to execute script:\n- File: {filename}\n- Function: {description}\nAllow execution? (y/n/a/p to preview code): "
        if not await self._request_user_confirmation(prompt, script_hash, code_preview=code, session_id=session_id):
            return {"status": "denied", "completed": True}
        deps = self.runtime._extract_deps(code)
        required_scopes = ["code:execute"]
        if deps:
            required_scopes.append("dependency:install")
        if re.search(r"\b(open|Path\(|write_text|write_bytes|mkdir|remove|unlink|rmtree|rename)\b", code):
            required_scopes.append("fs:write")
        scope_ok, scope_msg = self.check_runtime_scopes(required_scopes, session_id=session_id, source="write_and_execute_script")
        if not scope_ok:
            return {"status": "error", "message": scope_msg, "required_scopes": required_scopes, "completed": True}
        deps_safe, deps_reason = await self.audit_dependency_safety(
            deps,
            session_id=session_id,
            source="write_and_execute_script",
            context=f"Filename: {filename}\nDescription: {description}\nCode preview:\n{code[:3000]}"
        )
        if not deps_safe:
            return {
                "status": "error",
                "message": f"Dependency installation blocked: {deps_reason}",
                "dependencies": deps,
                "completed": True
            }
        res = await self.runtime.run_code(filename, code)
        if res.get("status") == "error":
            await self.ctx.record_failure(session_id, "script", filename, res.get("message", res.get("error", "")))
            return res
        output = res.get("output", "")
        download_paths = [line.split(":", 1)[1].strip() for line in output.splitlines() if line.startswith("DOWNLOAD_PATH:")]
        if download_paths:
            res["output"] = output + f"\n\nFiles saved to: {', '.join(download_paths)}"
            res["download_paths"] = download_paths
        await self._record_script_skill_candidate(session_id, filename, description, code, script_hash, res)
        if self.auto_skill_promotion:
            asyncio.create_task(self._auto_promote_script_candidate(script_hash, session_id))
        return res

    async def execute_system_cmd(self, command: str) -> dict:
        scope_ok, scope_msg = self.check_runtime_scopes(["system:command"], session_id="default", source="execute_system_command")
        if not scope_ok:
            return {"status": "error", "message": scope_msg, "completed": True}
        is_windows = platform.system() == "Windows"
        try:
            cmd_parts = shlex.split(command, posix=not is_windows)
        except Exception as e:
            return {"status": "error", "message": f"Malformed: {e}", "completed": False}
        if not cmd_parts:
            return {"status": "error", "message": "Empty command", "completed": False}
        base_cmd = cmd_parts[0]
        base_cmd_name = os.path.basename(base_cmd).lower()
        is_windows_builtin = False
        if is_windows and base_cmd_name in {'dir', 'copy', 'del', 'type', 'ren', 'move', 'echo', 'mkdir', 'rmdir', 'cd'}:
            cmd_parts = ['cmd.exe', '/c'] + cmd_parts
            base_cmd = cmd_parts[0]
            base_cmd_name = 'cmd.exe'
            is_windows_builtin = True
        if base_cmd_name in {'powershell', 'cmd', 'sh', 'bash', 'powershell.exe', 'cmd.exe'} and not is_windows_builtin:
            return {"status": "error", "message": f"Forbidden shell: {base_cmd}.", "completed": False}
        forbidden_pats = ['|', ';', '`', '$', '>', '<', '&&', '||']
        if base_cmd_name not in {'yt-dlp', 'yt-dlp.exe', 'wget', 'curl', 'wget.exe', 'curl.exe'} and not is_windows_builtin:
            forbidden_pats.append('&')
        if any(pat in command for pat in forbidden_pats):
            return {"status": "error", "message": "Forbidden pattern detected", "completed": False}
        if base_cmd_name not in {'yt-dlp.exe', 'yt-dlp', 'ffmpeg.exe', 'ffmpeg', 'cmd.exe', 'cmd'}:
            if base_cmd not in self.allowed_cmds:
                prompt = f"Unlisted command: '{base_cmd}'.\nExecute this time AND add to permanent whitelist? (y/n): "
                if await self._request_user_confirmation(prompt, session_id="default"):
                    self.allowed_cmds.add(base_cmd)
                    self._persist_allowed_cmds(base_cmd)
                else:
                    return {"status": "denied", "message": "Execution denied by user.", "completed": False}
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                cwd=str(self.workspace_dir), env=env)
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
            output_str = stdout_bytes.decode('utf-8', errors='replace').strip() if stdout_bytes else ""
            if proc.returncode == 0:
                return {"status": "success", "output": output_str, "completed": False}
            else:
                return {"status": "error", "message": f"Command failed (code {proc.returncode}):\n{output_str}", "completed": False}
        except FileNotFoundError:
            return {"status": "error", "message": f"Command not found: {base_cmd}", "completed": False}
        except asyncio.TimeoutError:
            return {"status": "error", "message": "Execution timed out.", "completed": False}
        except Exception as e:
            return {"status": "error", "message": str(e), "completed": False}

    async def execute_register_tool(self, tool_name: str, desc: str, schema_str: str, code: str) -> dict:
        if not tool_name.isidentifier():
            return {"status": "error", "message": "Invalid ID", "completed": True}
        try:
            new_schema = {"type": "function", "function": {"name": tool_name, "description": desc, "parameters": json.loads(schema_str)}}
            version_dir = os.path.join(self.tools_dir, tool_name, "versions", f"v_{int(time.time())}")
            os.makedirs(version_dir, exist_ok=True)
            async with aiofiles.open(os.path.join(version_dir, f"{tool_name}.json"), 'w', encoding='utf-8') as f:
                await f.write(json.dumps(new_schema, ensure_ascii=False, indent=4))
            async with aiofiles.open(os.path.join(version_dir, f"{tool_name}.py"), 'w', encoding='utf-8') as f:
                await f.write(code)
            self.tool_manager.register(ExtensionTool(tool_name, desc, new_schema["function"]["parameters"], os.path.join(version_dir, f"{tool_name}.py"), self.runtime))
            return {"status": "success", "completed": True}
        except Exception as e:
            return {"status": "error", "message": str(e), "completed": True}

    def _swarm_signature(self, payload: dict) -> str:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self.swarm_shared_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    def verify_swarm_signature(self, payload: dict, signature: str) -> bool:
        if not self.swarm_shared_secret:
            return False
        expected = self._swarm_signature(payload)
        return hmac.compare_digest(expected, signature or "")

    async def delegate_remote_agent(self, peer_id: str, task_prompt: str, session_id: str = "default", scopes: List[str] = None) -> dict:
        scope_ok, scope_msg = self.check_runtime_scopes(["agent:remote_delegate", "network:write"], session_id=session_id or "default", source="delegate_to_remote_agent")
        if not scope_ok:
            return {"status": "error", "message": scope_msg, "completed": True}
        peer = self.swarm_peers.get(str(peer_id))
        if not peer:
            return {"status": "error", "message": f"Remote peer '{peer_id}' is not configured.", "completed": True}
        requested_scopes = scopes or ["chat:delegate"]
        peer_allowed = peer.get("allowed_scopes") or self.swarm_allowed_remote_scopes
        ok_scope, missing = self._scopes_cover(peer_allowed, requested_scopes)
        if not ok_scope:
            return {"status": "error", "message": f"Peer '{peer_id}' does not allow requested scopes: {missing}", "completed": True}
        if not self.swarm_shared_secret:
            return {"status": "error", "message": "Swarm shared_secret is not configured; remote delegation is disabled.", "completed": True}
        payload = {
            "source_node": self.swarm_node_id,
            "session_id": session_id or "default",
            "task_prompt": task_prompt,
            "scopes": requested_scopes,
            "created_at": time.time()
        }
        headers = {
            "Content-Type": "application/json",
            "X-Agent-Node": self.swarm_node_id,
            "X-Agent-Signature": self._swarm_signature(payload)
        }
        url = str(peer["base_url"]).rstrip("/") + "/agent/delegate"
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=self.swarm_timeout_sec)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status >= 400:
                        return {"status": "error", "message": f"Remote peer returned HTTP {resp.status}", "response": data, "completed": True}
                    if self.broadcast_event:
                        await self.broadcast_event("remote_delegate_done", {"peer_id": peer_id, "status": data.get("status", "unknown")})
                    return {"status": "success", "peer_id": peer_id, "response": data, "completed": True}
        except ImportError as e:
            return {"status": "error", "message": f"Remote delegation requires aiohttp and dependencies: {e}", "completed": True}
        except Exception as e:
            return {"status": "error", "message": f"Remote delegation failed: {e}", "completed": True}

    async def spawn_subagents(self, tasks: List[str], parent_session_id: str = "default") -> List[str]:
        scope_ok, scope_msg = self.check_runtime_scopes(["agent:spawn"], session_id=parent_session_id or "default", source="delegate_to_subagents")
        if not scope_ok:
            return [{"status": "error", "message": scope_msg}]
        cache_ttl = 86400
        clean_tasks = [str(task).strip() for task in tasks if str(task).strip()]
        skipped_tasks = []
        if len(clean_tasks) > self.max_subagents:
            skipped_tasks = clean_tasks[self.max_subagents:]
            clean_tasks = clean_tasks[:self.max_subagents]
        semaphore = asyncio.Semaphore(self.subagent_concurrency)

        async def run_subtask_with_cache(task_prompt: str, idx: int):
            cache_key = f"subagent_cache:{hashlib.sha256(task_prompt.encode()).hexdigest()}"
            try:
                cached = await self.ctx.redis.get(cache_key)
                if cached:
                    logger.info(f"Subtask {idx} cache hit for: {task_prompt[:50]}...")
                    if self.broadcast_event:
                        await self.broadcast_event("subagent_cache_hit", {"index": idx, "task_preview": task_prompt[:120]})
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"Subtask {idx} cache read failed: {e}")

            sub_session = f"sub_{idx}_{hash(task_prompt) % 1000000}"
            workspace_suffix = hashlib.sha256(f"{idx}:{task_prompt}".encode()).hexdigest()[:10]
            sub_workspace = os.path.join(self.workspace_dir, "subagents", f"sub_{idx}_{workspace_suffix}")
            os.makedirs(sub_workspace, exist_ok=True)
            sub_config = copy.deepcopy(self.config)
            sub_runtime = dict(sub_config.get("runtime", {}))
            sub_runtime["workspace"] = sub_workspace
            sub_config["runtime"] = sub_runtime
            async with semaphore:
                if self.broadcast_event:
                    await self.broadcast_event("subagent_spawn", {"index": idx, "session_id": sub_session, "workspace": sub_workspace, "task_preview": task_prompt[:120]})
                sub_agent = YuanGeAgent(sub_config)
                await sub_agent.initialize()
                try:
                    result = await sub_agent.chat(sub_session, task_prompt)
                    await self._merge_subagent_memory(sub_agent, parent_session_id, sub_session)
                    try:
                        await self.ctx.redis.setex(cache_key, cache_ttl, json.dumps(result, ensure_ascii=False))
                    except Exception as e:
                        logger.warning(f"Subtask {idx} cache write failed: {e}")
                    if self.broadcast_event:
                        await self.broadcast_event("subagent_done", {"index": idx, "session_id": sub_session, "result_preview": str(result)[:200]})
                    return result
                finally:
                    await sub_agent.close()

        tasks_coro = [run_subtask_with_cache(task, i) for i, task in enumerate(clean_tasks)]
        results = await asyncio.gather(*tasks_coro)
        if skipped_tasks:
            results.append({
                "status": "skipped",
                "message": f"Skipped {len(skipped_tasks)} subtasks because max_subagents={self.max_subagents}.",
                "skipped_tasks": skipped_tasks
            })
        return results

    async def _merge_subagent_memory(self, sub_agent: 'YuanGeAgent', parent_session_id: str, sub_session: str):
        sub_memory = await sub_agent.ctx.get_core_memory(sub_session)
        if sub_memory:
            await self.ctx.update_core_memory(parent_session_id or "default", {f"subagent:{sub_session}": sub_memory})

    def _task_complexity_signals(self, user_input: str) -> dict:
        text = user_input or ""
        lowered = self._normalize_text(text)
        intent = self._detect_task_intent(text)
        category = self._detect_skill_category(text) or "general"
        score = 0
        markers = []

        def add(points: int, marker: str):
            nonlocal score
            score += points
            markers.append(marker)

        if len(text) >= 180:
            add(1, "long_query")
        if len(text) >= 600:
            add(1, "very_long_query")
        if "\n" in text:
            add(1, "multi_line")
        if "traceback" in lowered or "error" in lowered or "报错" in lowered or "exception" in lowered:
            add(2, "debug_or_error")
        if "```" in text or re.search(r"\b(class|def|async|import|select|from|function)\b", lowered):
            add(1, "code_or_structured_text")
        if intent.get("needs_input_completion"):
            add(1, "needs_input_completion")
        if intent.get("wants_discovery") and intent.get("wants_transformation"):
            add(1, "discover_then_transform")
        if category in {"research", "code"}:
            add(1, f"domain_{category}")
        complex_words = [
            "架构", "方案", "优化", "权衡", "安全", "评估", "综述", "研究现状",
            "全链路", "测试覆盖", "性能", "噪音", "长期", "记忆", "对标", "生产环境"
        ]
        if any(word in lowered for word in complex_words):
            add(1, "complex_domain_terms")
        simple_words = ["是什么", "怎么", "列出", "现在可以", "状态", "时间"]
        if len(text) < 80 and any(word in lowered for word in simple_words) and not intent.get("wants_action"):
            score = max(0, score - 1)
            markers.append("simple_question")
        return {"score": score, "markers": markers, "intent": intent, "category": category}

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Fast token estimator: ~4 chars/token for English, ~2 chars/token for CJK.
        Falls back to tiktoken if available for precise counts."""
        if not text:
            return 0
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            # Heuristic: count CJK chars separately (denser in tokens)
            cjk = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
            latin = len(text) - cjk
            return cjk // 2 + latin // 4

    @staticmethod
    def _msg_tokens(msg: dict) -> int:
        """Estimate token count for a single message dict."""
        content = str(msg.get("content", ""))
        # Add overhead for role and formatting (~4 tokens per message)
        return YuanGeAgent._estimate_tokens(content) + 4

    def _total_tokens(self, messages: list) -> int:
        """Sum estimated tokens across message list."""
        return sum(self._msg_tokens(m) for m in messages)

    async def _manage_context_window(self, session_id: str) -> List[dict]:
        """Token-based context management with intelligent pruning.

        Strategy:
          1. Always keep the system prompt + last 4 exchanges (user+assistant pairs).
          2. If total tokens exceed budget, progressively summarize older messages.
          3. Tool outputs (identified by 'tool' role or [Tool prefix) are kept longer
             than conversational filler.
          4. Never discard the most recent assistant response (it may contain
             in-progress tool calls).
        """
        history = await self.ctx.get_history(session_id)
        if not history:
            return history

        budget = getattr(self, 'context_token_budget', 65536)
        total = self._total_tokens(history)

        # If under budget, return as-is (but bump Redis cap to allow growth)
        if total < budget * 0.85 and len(history) < 200:
            return history

        # ── Pruning strategy ──
        # Reserve ~20% of budget for the response
        target = int(budget * 0.75)

        # Identify message types
        keep_tail = 6  # Always keep last 6 messages
        tail = history[-keep_tail:] if len(history) >= keep_tail else history
        head = history[:-keep_tail] if len(history) > keep_tail else []

        tail_tokens = self._total_tokens(tail)

        # If even the tail exceeds target, we must compress
        if tail_tokens > target:
            # Emergency: keep only last 3, summarize rest
            emergency_tail = history[-3:]
            emergency_head = history[:-3]
            summary_text = self._quick_summarize(emergency_head)
            result = [{"role": "system", "content": f"[Context summary — {len(emergency_head)} earlier messages compressed]\n{summary_text}"}]
            result.extend(emergency_tail)
            await self._replace_history(session_id, result)
            return result

        # Normal pruning: keep tail, compress head progressively
        remaining_budget = target - tail_tokens
        kept_head = []
        summarised = []

        # Walk head from newest to oldest, keeping as much as fits
        for msg in reversed(head):
            mt = self._msg_tokens(msg)
            # Tool outputs and assistant responses are high-value
            is_high_value = (
                msg.get("role") in ("assistant", "tool")
                or "[Tool" in str(msg.get("content", ""))[:100]
                or "```" in str(msg.get("content", ""))[:200]
            )
            if remaining_budget >= mt:
                kept_head.insert(0, msg)
                remaining_budget -= mt
            elif is_high_value and remaining_budget > 50:
                # Keep truncated version of high-value content
                content = str(msg.get("content", ""))
                trunc = content[:remaining_budget * 4] + "\n...[truncated]"
                kept_head.insert(0, {"role": msg.get("role", "user"), "content": trunc})
                remaining_budget = 0
            else:
                summarised.append(msg)

        # If we have summarised messages, create a compressed summary
        if summarised:
            summary_text = self._quick_summarize(summarised)
            summary_msg = {"role": "system", "content": f"[Compressed {len(summarised)} earlier messages]\n{summary_text}"}
            kept_head.insert(0, summary_msg)

        result = kept_head + tail
        await self._replace_history(session_id, result)
        return result

    def _quick_summarize(self, messages: list) -> str:
        """Fast extractive summary: pull user questions and key assistant decisions."""
        lines = []
        for m in messages[:20]:  # Sample at most 20
            role = str(m.get("role", ""))
            content = str(m.get("content", ""))
            if role == "user" and len(content) > 5:
                lines.append(f"- User asked: {content[:200]}")
            elif role == "assistant" and len(content) > 10:
                # Extract first meaningful sentence
                first_line = content.strip().split("\n")[0][:200]
                lines.append(f"- Assistant: {first_line}")
            elif role == "tool":
                lines.append(f"- Tool output: {content[:100]}")
        return "\n".join(lines[:30]) if lines else "(no summary available)"

    async def _replace_history(self, session_id: str, new_messages: list):
        """Atomically replace the entire history with new_messages."""
        await self.ctx.clear_history(session_id)
        for msg in new_messages:
            await self.ctx.add_history(session_id, msg.get("role", "user"), str(msg.get("content", "")), max_len=300)

    def _should_use_expert_debate(self, user_input: str, domain: str, experts: List[dict]) -> bool:
        if not experts or self.max_expert_opinions <= 0:
            return False
        mode = getattr(self, "expert_debate_mode", "auto")
        if mode == "off":
            return False
        if mode == "on":
            return True
        lowered = self._normalize_text(user_input)
        explicit_markers = [
            "专家", "辩论", "仲裁", "多视角", "评审", "review",
            "architecture", "安全", "科研方案", "研究方案", "复杂", "权衡"
        ]
        if any(marker in lowered for marker in explicit_markers):
            return True
        signals = self._task_complexity_signals(user_input)
        if domain != "general" and signals["score"] >= 2:
            return True
        if len(user_input) >= self.expert_debate_min_chars and signals["score"] >= 3:
            return True
        return signals["score"] >= 4

    async def _gather_opinions(self, session_id: str, user_input: str, domain: str, history: List[dict]) -> str:
        experts = self.domain_experts.get(domain, self.domain_experts.get("general", []))
        if not self._should_use_expert_debate(user_input, domain, experts):
            return "[]"
        experts = experts[:self.max_expert_opinions]
        async def ask_expert(expert):
            msgs = [{"role": "system", "content": f"Role: {expert['role']}\nTask: Analyze user query."}] + history + [{"role": "user", "content": user_input}]
            try:
                res = await self.client.chat.completions.create(model=self.model, messages=msgs, **self.extra_params)
                return {"owner_id": expert["id"], "answer": res.choices[0].message.content}
            except Exception as e:
                return {"owner_id": expert["id"], "answer": f"Error: {e}"}
        opinions = await asyncio.gather(*[ask_expert(e) for e in experts])
        return json.dumps(opinions, ensure_ascii=False)

    async def _conflict_detection_loop(self):
        pass

    def _normalize_text(self, text: str) -> str:
        return (text or "").strip().lower()

    def _tokenize_for_routing(self, text: str) -> set:
        normalized = self._normalize_text(text)
        tokens = set(re.findall(r'[a-zA-Z0-9_\-\.\/]+|[\u4e00-\u9fff]{1,4}', normalized))
        return {token for token in tokens if token.strip()}

    def _skill_search_text(self, name: str, info: dict) -> str:
        params = json.dumps(info.get("parameters", {}), ensure_ascii=False)
        keywords = " ".join([str(x) for x in info.get("keywords", []) or []])
        manifest = json.dumps({
            "capabilities": info.get("capabilities", []),
            "consumes": info.get("consumes", {}),
            "produces": info.get("produces", {}),
            "side_effects": info.get("side_effects", []),
            "risk": info.get("risk", "")
        }, ensure_ascii=False)
        doc = self.skill_docs.get(name, "")
        return f"{name}\n{info.get('description', '')}\n{params}\n{keywords}\n{manifest}\n{doc[:2400]}".lower()

    def _infer_skill_capabilities(self, name: str, info: dict) -> set:
        text = self._skill_search_text(name, info)
        caps = set()
        raw_caps = info.get("capabilities", []) or []
        if isinstance(raw_caps, str):
            raw_caps = [raw_caps]
        for cap in raw_caps:
            cap_norm = str(cap).strip().lower().replace("-", "_")
            if cap_norm in {"produce", "producer", "search", "lookup", "read", "list", "discover"}:
                caps.add("producer")
            if cap_norm in {"consume", "consumer", "download", "save", "write", "execute", "send", "create", "update", "delete"}:
                caps.add("consumer")
            if cap_norm in {"transform", "transformer", "convert", "extract", "analyze", "summarize", "transcribe"}:
                caps.add("transformer")
            caps.add(cap_norm)
        if info.get("produces"):
            caps.update({"producer", "resource_output", "input_provider"})
        if info.get("consumes") or info.get("parameters"):
            caps.update({"resource_input"})
        if info.get("consumes"):
            caps.update({"consumer", "input_consumer"})
        capability_markers = {
            "producer": [
                "search", "query", "lookup", "find", "list", "discover", "resolve", "inspect", "read",
                "检索", "搜索", "查找", "查询", "列出", "发现", "解析", "读取"
            ],
            "consumer": [
                "download", "save", "fetch", "obtain", "convert", "extract", "transcribe", "summarize",
                "create", "update", "delete", "send", "execute", "run",
                "下载", "保存", "抓取", "获取", "转换", "提取", "转写", "总结", "创建", "更新", "删除", "发送", "执行"
            ],
            "resource_input": [
                "url", "link", "uri", "id", "path", "file", "query", "keyword", "name",
                "链接", "网址", "路径", "文件", "编号", "关键词", "名称"
            ],
            "resource_output": [
                "return", "candidate", "result", "url", "link", "id", "path", "file", "list",
                "返回", "候选", "结果", "链接", "路径", "文件", "列表"
            ],
            "transformer": [
                "convert", "extract", "transcribe", "summarize", "parse", "analyze",
                "转换", "提取", "转写", "解析", "分析", "总结"
            ]
        }
        capability_markers["producer"].extend(["检索", "搜索", "查找", "查询", "列出", "发现", "解析", "读取"])
        capability_markers["consumer"].extend(["下载", "保存", "抓取", "获取", "转换", "提取", "转写", "总结", "创建", "更新", "删除", "发送", "执行"])
        capability_markers["resource_input"].extend(["链接", "网址", "路径", "文件", "编号", "关键词", "名称"])
        capability_markers["resource_output"].extend(["返回", "候选", "结果", "链接", "路径", "文件", "列表"])
        capability_markers["transformer"].extend(["转换", "提取", "转写", "解析", "分析", "总结", "综述", "归纳", "验证"])
        for cap, markers in capability_markers.items():
            if any(marker in text for marker in markers):
                caps.add(cap)
        if "producer" in caps and "resource_output" in caps:
            caps.add("input_provider")
        if "consumer" in caps and "resource_input" in caps:
            caps.add("input_consumer")
        return caps

    def _detect_task_intent(self, user_input: str) -> dict:
        text = user_input or ""
        lower = self._normalize_text(text)
        has_direct_resource = bool(re.search(
            r'https?://|www\.|[a-zA-Z]:[\\/]|/[^ \n\t]+|\b[A-Z]{1,10}[a-zA-Z0-9_-]{6,}\b|\b\d{8,}\b|10\.\d{4,9}/[-._;()/:A-Z0-9]+',
            text,
            re.I
        ))
        action_words = [
            "下载", "保存", "获取", "抓取", "下到", "存到", "导出", "搜索", "查找", "检索", "查询",
            "转换", "提取", "转写", "总结", "分析", "发送", "创建", "更新", "删除", "执行",
            "\u5199\u4ee3\u7801", "\u5b9e\u73b0", "\u5f00\u53d1", "\u4fee\u590d", "\u8c03\u8bd5", "\u91cd\u6784", "\u6d4b\u8bd5",
            "download", "save", "fetch", "get", "obtain", "export", "search", "find", "lookup", "query",
            "convert", "extract", "transcribe", "summarize", "analyze", "send", "create", "update", "delete", "run", "execute",
            "implement", "fix", "debug", "refactor", "patch", "build", "lint", "typecheck"
        ]
        acquisition_words = [
            "下载", "保存", "获取", "抓取", "下到", "存到", "导出",
            "download", "save", "fetch", "get", "obtain", "export"
        ]
        discovery_words = [
            "搜索", "查找", "检索", "查询", "找", "搜",
            "search", "find", "lookup", "query", "discover"
        ]
        transformation_words = [
            "转换", "提取", "转写", "总结", "分析", "convert", "extract", "transcribe", "summarize", "analyze"
        ]
        action_words.extend(["下载", "保存", "获取", "抓取", "下到", "存到", "导出", "搜索", "查找", "检索", "查询", "转换", "提取", "转写", "总结", "分析", "发送", "创建", "更新", "删除", "执行", "综述", "写作", "引用", "验证", "监控", "订阅", "追踪"])
        acquisition_words.extend(["下载", "保存", "获取", "抓取", "下到", "存到", "导出"])
        discovery_words.extend(["搜索", "查找", "检索", "查询", "找", "搜"])
        transformation_words.extend(["转换", "提取", "转写", "总结", "分析", "综述", "归纳", "验证"])
        action_words.extend(["下载", "保存", "获取", "抓取", "搜索", "查找", "检索", "查询", "找一下", "查一下", "总结", "分析", "给出", "生成", "写"])
        acquisition_words.extend(["下载", "保存", "获取", "抓取", "导出"])
        discovery_words.extend(["搜索", "查找", "检索", "查询", "找一下", "查一下", "给出"])
        transformation_words.extend(["转换", "提取", "转写", "总结", "分析", "综述", "归纳", "验证"])
        wants_action = any(word in lower for word in action_words)
        wants_acquisition = any(word in lower for word in acquisition_words)
        wants_discovery = any(word in lower for word in discovery_words)
        wants_transformation = any(word in lower for word in transformation_words)
        needs_input_completion = wants_action and not has_direct_resource
        return {
            "has_direct_resource": has_direct_resource,
            "wants_action": wants_action,
            "wants_acquisition": wants_acquisition,
            "wants_discovery": wants_discovery,
            "wants_transformation": wants_transformation,
            "needs_input_completion": needs_input_completion,
            "normalized_text": lower
        }

    def _lexical_skill_score(self, user_input: str, name: str, info: dict) -> float:
        query_terms = self._tokenize_for_routing(user_input)
        skill_terms = self._tokenize_for_routing(self._skill_search_text(name, info))
        if not query_terms or not skill_terms:
            return 0.0
        overlap = len(query_terms & skill_terms)
        score = overlap / max(len(query_terms), 1)
        lowered = self._normalize_text(user_input)
        if name.lower() in lowered:
            score += 1.0
        for keyword in [str(x).lower() for x in info.get("keywords", []) or []]:
            if keyword and keyword in lowered:
                score += 1.0
        intent = self._detect_task_intent(user_input)
        caps = self._infer_skill_capabilities(name, info)
        if intent["needs_input_completion"] and "input_provider" in caps:
            score += 0.5
        if intent["wants_action"] and ("input_consumer" in caps or "consumer" in caps):
            score += 0.4
        if intent["wants_discovery"] and "producer" in caps:
            score += 0.4
        if intent["wants_transformation"] and "transformer" in caps:
            score += 0.4
        return score

    def _rank_skills(self, user_input: str) -> list:
        ranked = []
        if not self.loaded_skills:
            return ranked
        active_category = self._detect_skill_category(user_input)
        if hasattr(self, "memory_engine") and getattr(self.memory_engine, "emb_model", None) is not None and self.skill_embeddings:
            try:
                query_text = self._normalize_text(user_input)
                query_emb = np.array(self.memory_engine.emb_model.encode([query_text])[0])
                norm_query = np.linalg.norm(query_emb)
                for name, emb in self.skill_embeddings.items():
                    info = self.loaded_skills.get(name)
                    if not info:
                        continue
                    norm_skill = np.linalg.norm(emb)
                    sim = float(np.dot(query_emb, emb) / (norm_query * norm_skill)) if norm_query and norm_skill else 0.0
                    ranked.append((sim + self._lexical_skill_score(user_input, name, info) + self._category_skill_score(active_category, info), name, info))
            except Exception:
                ranked = []
        if not ranked:
            for name, info in self.loaded_skills.items():
                ranked.append((self._lexical_skill_score(user_input, name, info) + self._category_skill_score(active_category, info), name, info))
        if active_category:
            same_category = [item for item in ranked if item[2].get("category", "general") in (active_category, "general")]
            if same_category:
                ranked = same_category
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked

    def _detect_skill_category(self, user_input: str) -> str:
        lowered = self._normalize_text(user_input)
        repaired = self._repair_mojibake_text(str(user_input or "")).lower()
        if self._looks_like_research_discussion(repaired):
            return "research"
        category_keywords = {
            "research": [
                "科研", "论文", "文献", "综述", "引用", "citation", "bibtex", "zotero",
                "paper", "literature", "review", "doi", "journal", "conference", "evidence"
            ],
            "media": [
                "视频", "下载", "剪辑", "转码", "字幕", "音频", "b站", "bilibili",
                "video", "download", "youtube", "mp4", "mp3", "transcode", "subtitle"
            ],
            "monitoring": [
                "监控", "订阅", "博客", "watch", "blog", "rss", "提醒", "定时"
            ],
            "code": [
                "\u4ee3\u7801", "\u7f16\u7a0b", "\u7801\u519c", "\u7a0b\u5e8f", "\u5f00\u53d1",
                "\u4fee\u590d", "\u8c03\u8bd5", "\u62a5\u9519", "\u91cd\u6784", "\u5355\u5143\u6d4b\u8bd5",
                "code", "coding", "program", "programmer", "bug", "debug", "fix", "refactor",
                "implement", "patch", "test", "pytest", "lint", "build", "typescript", "javascript",
                "python", "frontend", "backend", "claude code", "claudcode"
            ],
        }
        category_keywords["research"].extend([
            "科研", "论文", "文献", "综述", "引用", "参考文献", "顶刊", "顶会", "期刊", "会议",
            "研究现状", "国内外研究现状", "研究空白", "创新点", "证据矩阵"
        ])
        category_keywords["media"].extend(["视频", "下载", "剪辑", "转码", "字幕", "音频", "b站"])
        category_keywords["monitoring"].extend(["监控", "订阅", "博客", "追踪", "提醒", "定时"])
        category_keywords["research"].extend([
            "科研", "论文", "文献", "综述", "引用", "参考文献", "顶刊", "顶会", "期刊", "会议",
            "研究成果", "研究现状", "研究方向", "研究空白", "证据矩阵", "人机协同"
        ])
        category_keywords["media"].extend(["视频", "下载", "剪辑", "转码", "字幕", "音频"])
        category_keywords["monitoring"].extend(["监控", "订阅", "博客", "追踪", "提醒", "定时"])
        scores = {}
        for category, keywords in category_keywords.items():
            scores[category] = sum(1 for keyword in keywords if keyword and keyword.lower() in lowered)
        best, score = max(scores.items(), key=lambda item: item[1])
        return best if score > 0 else ""

    def _category_skill_score(self, active_category: str, info: dict) -> float:
        if not active_category:
            return 0.0
        category = info.get("category", "general")
        if category == active_category:
            return 2.0
        if category == "general":
            return 0.2
        return -0.3

    def _expand_skills_by_task(self, user_input: str, selected: dict) -> dict:
        intent = self._detect_task_intent(user_input)
        if not intent["wants_action"]:
            return selected
        active_category = self._detect_skill_category(user_input)
        producer_pool = []
        consumer_pool = []
        transformer_pool = []
        for name, info in self.loaded_skills.items():
            if active_category and info.get("category", "general") not in (active_category, "general"):
                continue
            caps = self._infer_skill_capabilities(name, info)
            score = self._lexical_skill_score(user_input, name, info)
            if "input_provider" in caps or "producer" in caps:
                producer_pool.append((score, name, info))
            if "input_consumer" in caps or "consumer" in caps:
                consumer_pool.append((score, name, info))
            if "transformer" in caps:
                transformer_pool.append((score, name, info))
        producer_pool.sort(key=lambda item: item[0], reverse=True)
        consumer_pool.sort(key=lambda item: item[0], reverse=True)
        transformer_pool.sort(key=lambda item: item[0], reverse=True)
        if intent["needs_input_completion"]:
            for score, name, info in producer_pool[:2]:
                if score > 0:
                    selected[name] = info
        if intent["wants_action"]:
            for score, name, info in consumer_pool[:3]:
                if score > 0:
                    selected[name] = info
        if intent["wants_transformation"]:
            for score, name, info in transformer_pool[:2]:
                if score > 0:
                    selected[name] = info
        return selected

    def _build_plan_hint(self, user_input: str, selected_skills: dict) -> str:
        intent = self._detect_task_intent(user_input)
        providers = []
        consumers = []
        transformers = []
        for name, info in selected_skills.items():
            caps = self._infer_skill_capabilities(name, info)
            if "input_provider" in caps or "producer" in caps:
                providers.append(name)
            if "input_consumer" in caps or "consumer" in caps:
                consumers.append(name)
            if "transformer" in caps:
                transformers.append(name)
        if intent["needs_input_completion"] and providers and consumers:
            return (
                "Planner Signal: The requested goal may not be directly executable because one or more required inputs are missing. "
                "Decompose the goal into tool steps. Use an available input-producing skill to obtain missing arguments, then pass the produced values into an input-consuming skill. "
                "Ask the user only if available tools cannot produce the missing input or the produced candidates are genuinely ambiguous."
            )
        if intent["has_direct_resource"] and consumers:
            return (
                "Planner Signal: The user supplied a direct resource or identifier. Prefer the smallest executable tool chain and pass that resource directly into the matching skill."
            )
        if intent["wants_transformation"] and transformers:
            return (
                "Planner Signal: The task appears to transform, extract, analyze, or summarize an input. If the input itself is missing, first obtain it with an input-producing skill; otherwise run the transformer directly."
            )
        if intent["wants_action"]:
            return (
                "Planner Signal: Treat the request as an objective with required inputs and outputs. If a single tool lacks required inputs, compose tools so that earlier outputs satisfy later inputs."
            )
        return "Planner Signal: No special decomposition signal. Use the minimum necessary tool calls."

    def _skill_budget_for_task(self, user_input: str) -> tuple[int, int]:
        if getattr(self, "skill_budget_mode", "auto") in {"off", "fixed"}:
            return self.top_k_skills, self.max_prompt_skills
        signals = self._task_complexity_signals(user_input)
        score = int(signals.get("score", 0))
        intent = signals.get("intent", {})
        category = signals.get("category", "general")
        if intent.get("has_direct_resource") and intent.get("wants_action"):
            top_k, max_prompt = 3, 5
        elif score >= 4:
            top_k, max_prompt = 5, 8
        elif score >= 2 or category in {"research", "code", "media"}:
            top_k, max_prompt = 4, 6
        elif intent.get("wants_action"):
            top_k, max_prompt = 3, 5
        else:
            top_k, max_prompt = 2, 3
        top_k = max(1, min(top_k, self.max_prompt_skills))
        max_prompt = max(top_k, min(max_prompt, self.max_prompt_skills))
        return top_k, max_prompt

    def _skill_allowed_for_request(self, skill_name: str, user_input: str) -> bool:
        name = str(skill_name or "").lower()
        lowered = self._normalize_text(user_input)
        repaired = self._repair_mojibake_text(str(user_input or "")).lower()
        if self._looks_like_research_discussion(repaired):
            if any(marker in name for marker in ("bilibili", "video", "download", "media", "subtitle", "audio")):
                return False
            if name in {"code_assistant", "write_and_execute_script"} and not self._has_explicit_code_intent(repaired):
                return False
        if name == "zotero_manager":
            explicit_zotero_terms = (
                "zotero", "bibtex", "文献库", "本地文献", "导出引用",
                "导出 bib", "参考文献库", "zotero desktop",
            )
            return any(term in lowered for term in explicit_zotero_terms)
        return True

    def _looks_like_research_discussion(self, text: str) -> bool:
        text = self._repair_mojibake_text(str(text or "")).lower()
        research_markers = (
            "科研", "论文", "文献", "综述", "顶刊", "顶会", "期刊", "会议",
            "研究空白", "研究方向", "研究问题", "创新点", "参考文献",
            "智能体记忆", "agent memory", "llm agent", "llm-based agent",
            "多智能体", "共享记忆", "社会记忆", "记忆冲突", "记忆拓扑",
        )
        advice_markers = (
            "从哪下手", "如何下手", "怎么下手", "切入", "合理", "方向",
            "选题", "课题", "研究", "方案", "实验设计", "发表",
        )
        return any(marker in text for marker in research_markers) and (
            any(marker in text for marker in advice_markers)
            or any(marker in text for marker in ("论文", "文献", "综述", "顶刊", "顶会", "研究空白"))
        )

    def _has_explicit_code_intent(self, text: str) -> bool:
        text = self._repair_mojibake_text(str(text or "")).lower()
        code_markers = (
            "代码", "编程", "程序", "开发", "实现", "修复", "调试", "报错",
            "重构", "单元测试", "接口", "后端", "前端", "typescript", "javascript",
            "python", "code", "implement", "debug", "bug", "fix", "refactor",
        )
        return any(marker in text for marker in code_markers)

    def _select_skills_for_prompt(self, user_input: str, ranked: list = None, budget: tuple[int, int] = None) -> dict:
        selected = {}
        ranked = ranked if ranked is not None else self._rank_skills(user_input)
        active_category = self._detect_skill_category(user_input)
        top_k, max_prompt = budget or self._skill_budget_for_task(user_input)
        for _, name, info in ranked[:top_k]:
            if not self._skill_allowed_for_request(name, user_input):
                continue
            selected[name] = info
        lowered = self._normalize_text(user_input)
        for name, info in self.loaded_skills.items():
            if not self._skill_allowed_for_request(name, user_input):
                continue
            if active_category and info.get("category", "general") not in (active_category, "general"):
                continue
            keywords = [str(x).lower() for x in info.get("keywords", []) or []]
            lex_score = self._lexical_skill_score(user_input, name, info)
            if any(keyword and keyword in lowered for keyword in keywords):
                selected[name] = info
            elif name.lower() in lowered:
                selected[name] = info
            elif lex_score >= 0.8:
                selected[name] = info
        selected = self._expand_skills_by_task(user_input, selected)
        selected = {
            name: info
            for name, info in selected.items()
            if self._skill_allowed_for_request(name, user_input)
        }
        if len(selected) > max_prompt:
            intent = self._detect_task_intent(user_input)
            def priority(item):
                name, info = item
                score = self._lexical_skill_score(user_input, name, info) + self._category_skill_score(active_category, info)
                caps = self._infer_skill_capabilities(name, info)
                if intent["needs_input_completion"] and ("input_provider" in caps or "producer" in caps):
                    score += 2.0
                if intent["wants_action"] and ("input_consumer" in caps or "consumer" in caps):
                    score += 2.0
                if intent["wants_transformation"] and "transformer" in caps:
                    score += 1.0
                return score
            selected = dict(sorted(selected.items(), key=priority, reverse=True)[:max_prompt])
        return selected

    def _build_skill_route_trace(self, user_input: str, ranked: list, selected_skills: dict, budget: tuple[int, int] = None) -> dict:
        intent = self._detect_task_intent(user_input).copy()
        intent.pop("normalized_text", None)
        candidates = []
        top_k, max_prompt = budget or self._skill_budget_for_task(user_input)
        for score, name, info in ranked[:max_prompt]:
            candidates.append({
                "skill": name,
                "score": round(float(score), 4),
                "category": info.get("category", "general"),
                "capabilities": sorted(list(self._infer_skill_capabilities(name, info)))[:12],
                "selected": name in selected_skills
            })
        return {
            "intent": intent,
            "category": self._detect_skill_category(user_input) or "general",
            "complexity": self._task_complexity_signals(user_input),
            "top_k": top_k,
            "max_prompt_skills": max_prompt,
            "budget_mode": getattr(self, "skill_budget_mode", "auto"),
            "selected_skills": list(selected_skills.keys()),
            "candidates": candidates
        }

    def _build_skill_instructions(self, selected_skills: dict) -> str:
        lines = []
        for name, info in selected_skills.items():
            params = json.dumps(info.get("parameters", {}), ensure_ascii=False) if info.get("parameters") else "None"
            consumes = json.dumps(info.get("consumes", {}), ensure_ascii=False) if info.get("consumes") else "None"
            produces = json.dumps(info.get("produces", {}), ensure_ascii=False) if info.get("produces") else "None"
            caps = sorted(list(self._infer_skill_capabilities(name, info)))
            caps_text = ", ".join(caps) if caps else "general"
            category = info.get("category", "general")
            lines.append(
                f"- Skill Name: `{name}`\n"
                f"  Category: {category}\n"
                f"  Capabilities: {caps_text}\n"
                f"  Description: {info.get('description', '')}\n"
                f"  Expected Parameters: {params}\n"
                f"  Consumes: {consumes}\n"
                f"  Produces: {produces}"
            )
        return "\n".join(lines) if lines else "No dynamically loaded single-file skills available."

    def _available_skills_can_cover_task(self, user_input: str, selected_skills: dict) -> bool:
        intent = self._detect_task_intent(user_input)
        if not intent["wants_action"] or not selected_skills:
            return False
        for _, info in selected_skills.items():
            caps = self._infer_skill_capabilities("", info)
            if "consumer" in caps or "input_consumer" in caps or "producer" in caps or "input_provider" in caps or "transformer" in caps:
                return True
        return False

    def _should_prefer_installed_skill_path(self, user_input: str, selected_skills: dict) -> bool:
        if self._available_skills_can_cover_task(user_input, selected_skills):
            return True
        active_category = self._detect_skill_category(user_input)
        if not active_category or not selected_skills:
            return False
        return any(info.get("category", "general") == active_category for info in selected_skills.values())

    def _blocked_script_tool_output(self, tool_name: str = "extension_tool") -> str:
        return json.dumps({
            "status": "error",
            "message": f"Blocked {tool_name} because installed skills are available for this objective. Use run_skill_script with complete JSON parameters, or summarize the current skill result and its evidence boundary.",
            "completed": False
        }, ensure_ascii=False)

    def _trace_has_installed_skill_call(self, task_trace: dict) -> bool:
        for call in task_trace.get("tool_calls", []) or []:
            if call.get("tool") == "run_skill_script":
                return True
        return False

    def _repair_mojibake_text(self, value: str) -> str:
        text = str(value or "")
        if not text:
            return text

        def cjk_count(item: str) -> int:
            return len(re.findall(r"[\u4e00-\u9fff]", item or ""))

        def badness(item: str) -> int:
            return len(re.findall(r"[ÃÂÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ�\x80-\x9f]", item or ""))

        def semantic_score(item: str) -> int:
            markers = (
                "人机", "协同", "合作", "信息系统", "信管", "信息管理",
                "智能体", "记忆", "论文", "文献", "综述",
            )
            return sum(1 for marker in markers if marker in (item or ""))

        candidates = [text]
        for source_encoding in ("latin1", "cp1252"):
            try:
                raw = text.encode(source_encoding)
            except UnicodeError:
                continue
            for target_encoding in ("utf-8", "gbk"):
                try:
                    decoded = raw.decode(target_encoding)
                except UnicodeError:
                    continue
                if decoded not in candidates:
                    candidates.append(decoded)
        return max(candidates, key=lambda item: (semantic_score(item), cjk_count(item), -badness(item), len(item)))

    def _is_simple_research_lookup(self, user_input: str) -> bool:
        text = self._repair_mojibake_text(str(user_input or "")).lower()
        lookup_terms = ("论文", "文献", "paper", "papers", "literature", "doi", "链接", "link", "links")
        deep_terms = (
            "综述", "review", "证据矩阵", "evidence matrix", "matrix", "全文", "read",
            "paper_reader", "citation_graph", "引用图", "引文图", "citation graph",
            "验证", "verify", "研究空白", "future direction", "未来方向",
        )
        return any(term in text for term in lookup_terms) and not any(term in text for term in deep_terms)

    def _should_use_deterministic_research_answer(self, payload: dict, user_input: str) -> bool:
        if not isinstance(payload, dict) or not payload.get("papers"):
            return False
        text = self._repair_mojibake_text(str(user_input or "")).lower()
        lookup_terms = (
            "检索", "搜索", "查找", "找一下", "论文", "文献", "paper", "papers",
            "literature", "doi", "链接", "link", "links", "白名单", "top venue",
            "top conference", "top journal",
        )
        synthesis_terms = (
            "综述", "review", "研究空白", "future direction", "未来方向", "对比",
            "compare", "设计", "research question", "citation_graph", "引用图",
            "引文图", "引用关系", "关系图", "图谱", "文献列表", "列表视图",
            "citation graph", "全文", "full text", "read",
        )
        return any(term in text for term in lookup_terms) and not any(term in text for term in synthesis_terms)

    def _should_use_deterministic_abstract_answer(self, payload: dict, user_input: str) -> bool:
        if not isinstance(payload, dict) or not payload.get("papers"):
            return False
        text = self._repair_mojibake_text(str(user_input or "")).lower()
        abstract_terms = ("摘要", "abstract", "详细信息", "详细摘要", "读取这些", "读取论文")
        paper_terms = ("论文", "文献", "paper", "papers", "顶会", "顶刊")
        return any(term in text for term in abstract_terms) and any(term in text for term in paper_terms)

    def _skill_name_from_tool_arguments(self, arguments: str) -> str:
        try:
            outer = json.loads(arguments or "{}")
            return str(outer.get("skill_name") or "")
        except Exception:
            return ""

    def _trace_has_successful_paper_fetch(self, task_trace: dict) -> bool:
        for call in task_trace.get("tool_calls", []) or []:
            if call.get("tool") != "run_skill_script":
                continue
            skill_name = self._skill_name_from_tool_arguments(str(call.get("arguments") or ""))
            if skill_name != "paper_fetch_review":
                continue
            parsed = call.get("parsed_output")
            if not isinstance(parsed, dict) or parsed.get("status") == "error":
                continue
            payload = parsed
            raw_output = parsed.get("output")
            if isinstance(raw_output, str):
                try:
                    decoded_output = json.loads(raw_output)
                    if isinstance(decoded_output, dict):
                        payload = decoded_output
                except Exception:
                    pass
            papers = payload.get("papers") or []
            valid_count = payload.get("valid_count")
            if (isinstance(valid_count, int) and valid_count > 0) or papers:
                return True
        return False

    def _unwrap_skill_payload(self, parsed: Any) -> dict:
        if not isinstance(parsed, dict):
            return {}
        payload = parsed
        raw_output = parsed.get("output")
        if isinstance(raw_output, str):
            try:
                decoded_output = json.loads(raw_output)
                if isinstance(decoded_output, dict):
                    payload = decoded_output
            except Exception:
                pass
        return payload if isinstance(payload, dict) else {}

    def _latest_research_payload_from_trace(self, task_trace: dict) -> dict:
        research_skills = {"paper_fetch_review", "review_pipeline"}
        for call in reversed(task_trace.get("tool_calls", []) or []):
            if call.get("tool") != "run_skill_script":
                continue
            skill_name = self._skill_name_from_tool_arguments(str(call.get("arguments") or ""))
            if skill_name not in research_skills:
                continue
            payload = self._unwrap_skill_payload(call.get("parsed_output"))
            if payload.get("status") == "error":
                continue
            if payload.get("papers") or payload.get("verification_matrix") or payload.get("reference_verification"):
                return payload
        return {}

    @staticmethod
    def _markdown_cell(value: Any, max_chars: int = 80) -> str:
        text = str(value or "-").replace("|", "\\|").replace("\n", " ").strip()
        return text[:max_chars] + ("..." if len(text) > max_chars else "")

    def _primary_reference_link(self, item: dict, paper: dict | None = None) -> str:
        links_info = item.get("links_info") if isinstance(item, dict) else {}
        links = links_info.get("links", {}) if isinstance(links_info, dict) else {}
        for key in ("doi", "openalex", "publisher", "url"):
            value = links.get(key)
            if value:
                return str(value)
        paper = paper or {}
        doi = str(item.get("doi") or paper.get("doi") or "").strip()
        if doi:
            return "https://doi.org/" + doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        openalex_id = str(item.get("openalex_id") or paper.get("openalex_id") or "").strip()
        if openalex_id:
            return openalex_id if openalex_id.startswith("http") else "https://openalex.org/" + openalex_id
        return str(paper.get("url") or "-")

    @staticmethod
    def _reference_link_status(item: dict) -> str:
        links_info = item.get("links_info") if isinstance(item, dict) else {}
        if not isinstance(links_info, dict):
            return "待确认"
        if links_info.get("reachable") is True:
            return "实时可达"
        if links_info.get("reachable") is False:
            return "不可达"
        if links_info.get("traceable"):
            return "可追踪，未实时验证"
        return "待确认"

    def _format_research_lookup_answer(self, payload: dict, user_input: str) -> str:
        papers = payload.get("papers") or []
        matrix = payload.get("verification_matrix") or []
        if not isinstance(papers, list) or not papers:
            return ""
        if not isinstance(matrix, list):
            matrix = []
        matrix_by_index = {
            int(item.get("index")): item
            for item in matrix
            if isinstance(item, dict) and str(item.get("index") or "").isdigit()
        }
        title = "检索完成。以下是白名单顶会/顶刊命中结果"
        valid_count = payload.get("valid_count")
        count_text = valid_count if isinstance(valid_count, int) else len(papers)
        lines = [
            f"{title}（共 {count_text} 篇）：",
            "",
            "### 白名单命中论文",
            "| # | 标题 | 年份 | 会议/期刊 | 引用 | DOI/链接 |",
            "|---|------|------|-----------|------|----------|",
        ]
        for idx, paper in enumerate(papers, start=1):
            if not isinstance(paper, dict):
                continue
            verifier = matrix_by_index.get(idx, {})
            link = self._primary_reference_link(verifier, paper)
            lines.append(
                f"| {idx} | {self._markdown_cell(paper.get('title'), 56)} | "
                f"{self._markdown_cell(paper.get('year') or paper.get('publication_year'), 8)} | "
                f"{self._markdown_cell(paper.get('venue'), 34)} | "
                f"{self._markdown_cell(paper.get('citations') or paper.get('cited_by_count') or 0, 8)} | "
                f"{self._markdown_cell(link, 70)} |"
            )
        if matrix:
            lines.extend([
                "",
                "### 引用与反幻觉验证矩阵",
                "| # | 元数据来源 | DOI / 链接 | 链接状态 | 幻觉风险 | 证据边界 |",
                "|---|------------|------------|----------|----------|----------|",
            ])
            for idx, paper in enumerate(papers, start=1):
                verifier = matrix_by_index.get(idx, {})
                if not verifier:
                    continue
                link = self._primary_reference_link(verifier, paper if isinstance(paper, dict) else {})
                status = self._reference_link_status(verifier)
                risk = verifier.get("hallucination_risk") or "unknown"
                source = verifier.get("metadata_source") or "unknown"
                if status == "实时可达":
                    boundary = "仅表示链接现场访问成功；全文结论仍需核验"
                elif status == "可追踪，未实时验证":
                    boundary = "仅表示题录有 DOI/OpenAlex 可追踪标识；未声明 HTTP 可达"
                elif status == "不可达":
                    boundary = "现场访问失败或返回错误；需人工复核"
                else:
                    boundary = "缺少可追踪标识；需人工确认"
                lines.append(
                    f"| {idx} | {self._markdown_cell(source, 28)} | {self._markdown_cell(link, 70)} | "
                    f"{status} | {self._markdown_cell(risk, 10)} | {boundary} |"
                )
        ref_verification = payload.get("reference_verification") if isinstance(payload.get("reference_verification"), dict) else {}
        boundary = ref_verification.get("boundary") or (
            "该结果基于结构化题录元数据、白名单 venue 匹配和 DOI/OpenAlex 可追踪标识；除非链接状态明确为“实时可达”，否则不代表现场 HTTP 访问成功。"
        )
        lines.extend(["", f"证据边界：{boundary}"])
        return "\n".join(lines)

    def _format_paper_abstract_answer(self, payload: dict, user_input: str) -> str:
        papers = payload.get("papers") or []
        if not isinstance(papers, list) or not papers:
            return ""
        lines = [
            f"已读取可用元数据摘要（共 {len(papers)} 篇）：",
            "",
        ]
        for idx, paper in enumerate(papers, start=1):
            if not isinstance(paper, dict):
                continue
            title = paper.get("title") or f"Paper {idx}"
            year = paper.get("year") or paper.get("publication_year") or "-"
            venue = paper.get("venue") or "-"
            doi = paper.get("doi") or "-"
            abstract = str(paper.get("abstract") or "").strip()
            if not abstract:
                abstract = "未在当前题录元数据中提供摘要；不能据此补写实验细节或结论。"
            lines.extend([
                f"### {idx}. {title}",
                f"- 年份：{year}",
                f"- 会议/期刊：{venue}",
                f"- DOI/链接：{doi}",
                f"- 摘要：{abstract}",
                "",
            ])
        lines.append("证据边界：以上只来自检索工具返回的题录元数据和摘要字段；若摘要缺失，不推断论文实验设置、数据集或定量结论。")
        return "\n".join(lines)

    def _blocked_extra_research_tool_output(self, skill_name: str) -> str:
        return json.dumps({
            "status": "success",
            "message": (
                f"No further research skill call is needed for '{skill_name}': paper_fetch_review already returned usable papers "
                "for this lookup request. Provide the final answer from the existing result and state the evidence boundary."
            ),
            "completed": True,
        }, ensure_ascii=False)

    def _repair_tool_arguments_for_context(self, tool_name: str, arguments: str, routing_input: str) -> str:
        if tool_name != "run_skill_script":
            return arguments
        try:
            outer = json.loads(arguments or "{}")
            skill_name = str(outer.get("skill_name") or "")
            if skill_name not in {"paper_fetch_review", "review_pipeline"}:
                return arguments
            inner = json.loads(outer.get("args_string") or outer.get("params_json") or "{}")
            query = self._repair_mojibake_text(str(inner.get("query") or "")).strip()
            if query:
                inner["query"] = query
            routing = self._repair_mojibake_text(str(routing_input or "")).strip()
            if not routing:
                return arguments
            routing_lower = routing.lower()
            query_lower = query.lower()
            important_terms = [
                "\u4eba\u673a\u534f\u540c",
                "\u4eba\u673a\u5408\u4f5c",
                "\u4eba\u673a\u4ea4\u4e92",
                "\u4fe1\u606f\u7cfb\u7edf",
                "\u4fe1\u7ba1",
                "\u4fe1\u606f\u7ba1\u7406",
                "人机协同",
                "人机合作",
                "人机交互",
                "human-ai collaboration",
                "human ai collaboration",
                "human-machine collaboration",
                "human machine collaboration",
                "information systems",
                "信息系统",
                "信管",
                "信息管理",
            ]
            missing_terms = [
                term for term in important_terms
                if term.lower() in routing_lower and term.lower() not in query_lower
            ]
            if missing_terms:
                inner["query"] = " ".join([query or routing, *missing_terms]).strip()
            if "year_start" not in inner and re.search(r"202[4-9]|203\d", routing):
                year_match = re.search(r"202[4-9]|203\d", routing)
                if year_match:
                    inner["year_start"] = int(year_match.group(0))
            outer["args_string"] = json.dumps(inner, ensure_ascii=False)
            return json.dumps(outer, ensure_ascii=False)
        except Exception:
            return arguments

    def _build_static_system_prompt(self, domain: str) -> str:
        cache_key = f"{platform.system()}::{domain}"
        if cache_key in self._static_system_prompt_cache:
            return self._static_system_prompt_cache[cache_key]
        os_name = platform.system()
        if os_name == "Windows":
            os_policy = "0. Identity (Windows): Use CMD commands (dir, type, copy). No PowerShell. When reading SKILL.md files, translate any Unix commands (python3, pip3, chmod, ./script.sh, /usr/bin/) to Windows equivalents before generating code."
        elif os_name == "Linux":
            os_policy = "0. Identity (Linux): Use bash commands (ls, cat, cp). When reading SKILL.md, note that commands may assume Unix."
        else:
            os_policy = f"0. Identity ({os_name}): Use standard shell commands."
        rules = (
            # ── Universal rules (0-5) ──
            "0. FAILURE HANDLING: If a tool or skill returns an error, read the error message, repair the inputs once, and retry only when the repair is clear. If the same operation fails again, stop and report the error.\n"
            "1. OBJECTIVE DECOMPOSITION: Treat the user request as an objective with required inputs, intermediate outputs, and final outputs. If one tool cannot complete the objective because required inputs are missing, split the objective into smaller tool steps.\n"
            "2. INPUT COMPLETION: Before asking the user for missing information, inspect Available Skills to see whether another skill can produce or infer the missing input. Use earlier tool outputs as later tool inputs.\n"
            "3. DIRECT EXECUTION: If the user already provided all required inputs for a matching skill, call that skill directly and avoid unnecessary discovery steps.\n"
            "4. TOOL CONTRACT: For run_skill_script, args_string must be exactly one valid JSON object string containing the skill parameters. Never pass plain text, markdown, command-line flags, or an empty object when required fields are known.\n"
            "5. PARAMETER REPAIR: If a skill fails because of malformed arguments, correct the JSON in the very next call. Preserve user constraints such as output path, format, language, resolution, time range, and mode.\n"
            # ── Research rules (6-7b) ──
            "6. AMBIGUITY: If tool-produced candidates are clearly ranked or one candidate best matches the request, choose it. Ask the user only when candidates are genuinely ambiguous or when no available skill can resolve the missing input.\n"
            "7. TOOL MINIMALITY: Use the smallest reliable chain of tools. Do not install new skills, write replacement scripts, or switch strategies when existing loaded skills can satisfy the objective.\n"
            "7a. RESEARCH TOOL MINIMALITY: For simple paper lookup or link requests, call paper_fetch_review once with the user's topic, year range, and inferred domain, then answer from that result. Use review_pipeline for literature reviews, evidence_matrix for matrix requests, and paper_reader/citation_graph only when the user explicitly asks for full-paper reading, citation graph, verification, or deeper expansion.\n"
            "7b. RESEARCH VERIFICATION VISIBILITY: If a research skill output contains verification_matrix, reference_verification, link_checks, or citation_verification, the final answer must include a visible '引用与反幻觉验证矩阵' / 'Reference Verification Matrix' section with per-reference source, DOI/link, link status, hallucination risk, and evidence boundary. Do not replace this with a generic link list.\n"
            # ── Engineering rules (8-14) ──
            "8. READ BEFORE WRITE: Never edit a file you haven't read. Use code_assistant read or search to understand the code first. Editing without reading causes bugs from incorrect assumptions about existing structure.\n"
            "9. EXPLORATION STRATEGY: For unknown codebases: (a) inspect → understand structure, (b) search → find relevant files, (c) read → understand the specific code, (d) edit → make changes. Never skip from inspect directly to edit.\n"
            "10. GIT SAFETY: Before any destructive edit (rename, refactor, delete), create a code_refactor snapshot. After edits, run code_assistant git_diff to review changes. If the user accepts, they can commit. If something breaks, restore the snapshot.\n"
            "11. TEST-DRIVEN WORKFLOW: Before fixing a bug, reproduce it. After fixing, run the test suite. If tests fail, read the failure output carefully before attempting a second fix. Do not guess at fixes — let error messages guide you.\n"
            "12. ERROR TRACEBACK LITERACY: When a command fails, read the full stderr output. Find the first error (not the last). Identify the file and line number. Read that file at that location. Fix the root cause, not the symptom.\n"
            "13. MULTI-FILE COORDINATION: When a change affects multiple files (e.g., renaming a function), plan the full set of edits before making the first one. List all affected files. Make edits in dependency order (callee before caller for definitions, caller before callee for deletions).\n"
            "14. CODE QUALITY GATES: After making changes: (a) run the linter, (b) run the type checker, (c) run the test suite. If any gate fails, fix the issues before reporting success. A change that breaks tests is not complete.\n"
            # ── Compound and safety (15-17) ──
            "15. SKILL COMPOUNDING: When write_and_execute_script succeeds and the user confirms the script is generally useful, call promote_script_to_skill instead of leaving the solution as a one-off script.\n"
            "16. SAFETY AND FILESYSTEM: Use available tools within the configured safety policy. The runtime enforces configured write-path restrictions before execution.\n"
            "17. TURN BOUNDARIES: Tool calls must serve the current user request. If the latest message is a follow-up, anchor it to the immediate previous user request instead of resuming older goals.\n"
            "18. TOOL FORMAT: Use the API tool_calls field for tools. Never write DSML/XML/markdown tool-call markup in assistant text.\n"
        )
        static_prompt = (
            f"{self.domain_meta.get(domain, {}).get('system_prompt', '')}\n\n"
            f"{os_policy}\n"
            f"{rules}"
        )
        self._static_system_prompt_cache[cache_key] = static_prompt
        return static_prompt

    def _build_dynamic_system_prompt(
        self,
        allowed_paths_str: str,
        core_mem_str: str,
        past_patterns_str: str,
        skill_instructions: str,
        plan_hint: str
    ) -> str:
        return (
            f"Runtime Safety\nAllowed write paths: {allowed_paths_str}. Any attempt to write outside these paths will be blocked by the runtime before execution.\n\n"
            f"Procedural Memory\n{self.get_clinical_rules()}\n\n"
            f"Core State\n{core_mem_str}\n\n"
            f"Relevant Past Workflow Patterns\n{past_patterns_str}\n\n"
            f"Available Skills\n{skill_instructions}\n\n"
            f"{plan_hint}\n\n"
            "Execution Contract:\n"
            "- Use run_skill_script for installed skills.\n"
            "- The args_string value must be one valid JSON object string.\n"
            "- Treat each skill as an operation with required inputs and possible outputs.\n"
            "- If the next operation lacks an input, first check whether another available skill can produce that input.\n"
            "- Chain tool outputs into later tool inputs when that directly advances the user's objective.\n"
            "- For research lookup/link requests, do not fan out across multiple research skills after a usable paper_fetch_review result. Summarize the returned papers and clearly state the evidence boundary.\n"
            "- When research outputs include verification_matrix/reference_verification/link_checks/citation_verification, show those checks explicitly as a compact table. Include metadata source, DOI or primary link, link status, hallucination risk, and what the check does not prove.\n"
            "- For coding tasks: read files before editing, create git snapshots before destructive changes, run tests after changes, and report test results.\n"
            "- For code search: prefer code_assistant search over raw grep; use code_assistant symbols to understand structure; use code_review to find issues before editing.\n"
            "- For debugging: reproduce the error first, read the full traceback, find the root cause file+line, read that code, then fix. Do not guess.\n"
            "- Ask the user only for missing inputs that cannot be produced by available skills or are genuinely ambiguous.\n"
            "- Preserve user constraints such as output folder, format, resolution, language, mode, time range, and file type.\n"
            "- Treat past workflow patterns as suggestions, not commands. Verify current skills and parameters before reusing a chain.\n"
            "- Do not call write_and_execute_script when installed skills can satisfy the objective. Use it only when no loaded skill or registered tool can reasonably perform the operation.\n"
            "- If a write_and_execute_script result includes promotion_candidate and the user says it worked or asks to keep it, call promote_script_to_skill with the script_hash."
        )

    def _summarize_tool_call_for_learning(self, call: dict) -> dict:
        parsed = call.get("parsed_output")
        status = None
        completed = None
        produces = None
        message = None
        if isinstance(parsed, dict):
            status = parsed.get("status")
            completed = parsed.get("completed")
            produces = parsed.get("produces") or parsed.get("result") or parsed.get("results")
            message = parsed.get("message")
        skill_name = None
        skill_args = None
        if call.get("tool") == "run_skill_script":
            try:
                args = json.loads(call.get("arguments") or "{}")
                skill_name = args.get("skill_name")
                raw_args = args.get("args_string") or args.get("params_json")
                if isinstance(raw_args, str):
                    try:
                        skill_args = json.loads(raw_args)
                    except Exception:
                        skill_args = raw_args
                else:
                    skill_args = raw_args
            except Exception:
                pass
        return {
            "tool": call.get("tool"),
            "skill": skill_name,
            "skill_args": skill_args,
            "status": status,
            "completed": completed,
            "produces": produces,
            "message": message
        }

    @staticmethod
    def _json_dumps_safe(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _message_role(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("role") or "")
        return str(getattr(message, "role", "") or "")

    @staticmethod
    def _message_tool_call_id(message: Any) -> str:
        if isinstance(message, dict):
            return str(message.get("tool_call_id") or "")
        return str(getattr(message, "tool_call_id", "") or "")

    @staticmethod
    def _message_tool_call_ids(message: Any) -> list[str]:
        if isinstance(message, dict):
            tool_calls = message.get("tool_calls") or []
        else:
            tool_calls = getattr(message, "tool_calls", None) or []
        ids: list[str] = []
        for call in tool_calls:
            call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", "")
            if call_id:
                ids.append(str(call_id))
        return ids

    def _sanitize_openai_tool_message_sequence(self, messages: list[Any]) -> list[Any]:
        """Ensure every assistant tool_call is immediately followed by matching tool messages."""
        sanitized: list[Any] = []
        i = 0
        while i < len(messages):
            message = messages[i]
            role = self._message_role(message)
            if role == "tool":
                i += 1
                continue
            tool_call_ids = self._message_tool_call_ids(message)
            if role != "assistant" or not tool_call_ids:
                sanitized.append(message)
                i += 1
                continue
            sanitized.append(message)
            answered: set[str] = set()
            j = i + 1
            while j < len(messages) and self._message_role(messages[j]) == "tool":
                tool_call_id = self._message_tool_call_id(messages[j])
                if tool_call_id in tool_call_ids and tool_call_id not in answered:
                    sanitized.append(messages[j])
                    answered.add(tool_call_id)
                j += 1
            for tool_call_id in tool_call_ids:
                if tool_call_id not in answered:
                    sanitized.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps({
                            "status": "error",
                            "message": "Tool response was missing and was replaced by the agent message sanitizer.",
                            "completed": False,
                        }, ensure_ascii=False),
                    })
            i = j
        return sanitized

    def _filter_relevant_past_patterns(self, query: str, patterns: list[dict], limit: int = 3) -> list[dict]:
        if not patterns:
            return []
        stop_tokens = {
            "2024", "2025", "2026", "论文", "文献", "研究", "科研", "综述", "帮我", "一下",
            "以来", "给出", "链接", "paper", "papers", "research", "review", "search", "fetch"
        }
        query_tokens = {tok for tok in self._tokenize_for_routing(query) if tok not in stop_tokens}
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
            if not isinstance(pattern, dict):
                continue
            text = " ".join([
                str(pattern.get("goal_type", "")),
                str(pattern.get("notes", "")),
                " ".join([str(x) for x in pattern.get("preconditions", []) or []]),
                " ".join([str(x) for x in pattern.get("successful_chain", []) or []]),
                " ".join([str(x) for x in pattern.get("example_user_goals", []) or []]),
            ]).lower()
            if any((zh in query_text or en in query_text) and not (zh in text or en in text) for zh, en in marker_groups):
                continue
            pattern_tokens = {tok for tok in self._tokenize_for_routing(text) if tok not in stop_tokens}
            overlap = len(query_tokens & pattern_tokens)
            ratio = overlap / max(1, min(len(query_tokens), len(pattern_tokens)))
            if overlap >= 2 and ratio >= 0.25:
                scored.append((overlap + ratio, pattern))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [pattern for _, pattern in scored[:limit]]

    def _score_task_trace(self, trace: dict) -> dict:
        tool_calls = trace.get("tool_calls", []) or []
        if not tool_calls:
            return {
                "reward": 0.2 if trace.get("final_answer") else 0.0,
                "confidence": 0.35 if trace.get("final_answer") else 0.0,
                "dimensions": {
                    "success": bool(trace.get("final_answer")),
                    "stability": 1.0,
                    "speed": 1.0,
                    "efficiency": 1.0,
                    "tool_count": 0,
                    "duration_ms": 0.0
                }
            }
        failures = 0
        successes = 0
        total_duration_ms = 0.0
        for call in tool_calls:
            parsed = call.get("parsed_output") if isinstance(call.get("parsed_output"), dict) else {}
            status = str(parsed.get("status") or "").lower()
            failed = status in {"error", "denied"} or bool(parsed.get("error"))
            failures += 1 if failed else 0
            successes += 0 if failed else 1
            total_duration_ms += float(call.get("duration_ms") or 0.0)
        tool_count = len(tool_calls)
        stability = successes / tool_count if tool_count else 1.0
        speed = 1.0 / (1.0 + (total_duration_ms / 60000.0))
        efficiency = 1.0 / (1.0 + max(0, tool_count - 3) * 0.18)
        completion = 1.0 if trace.get("success") else 0.0
        reward = max(0.0, min(1.0, 0.45 * completion + 0.25 * stability + 0.20 * speed + 0.10 * efficiency))
        confidence = max(0.0, min(1.0, 0.50 * stability + 0.25 * speed + 0.25 * completion))
        return {
            "reward": round(reward, 4),
            "confidence": round(confidence, 4),
            "dimensions": {
                "success": bool(trace.get("success")),
                "stability": round(stability, 4),
                "speed": round(speed, 4),
                "efficiency": round(efficiency, 4),
                "tool_count": tool_count,
                "failures": failures,
                "duration_ms": round(total_duration_ms, 2)
            }
        }

    async def _learn_from_task_trace(self, trace: dict):
        if not trace or not trace.get("tool_calls"):
            return
        reward_profile = self._score_task_trace(trace)
        trace["reward_profile"] = reward_profile
        try:
            await self.ctx.record_tool_reward_stats(trace.get("tool_calls", []), reward_profile)
        except Exception as e:
            logger.debug(f"Tool reward stats write skipped: {e}")
        summarized_calls = [self._summarize_tool_call_for_learning(call) for call in trace.get("tool_calls", [])]
        successful_chain = []
        for call in summarized_calls:
            status = call.get("status")
            if status == "error":
                continue
            name = call.get("skill") or call.get("tool")
            if name and name not in successful_chain:
                successful_chain.append(name)
        if not successful_chain:
            return
        compact_trace = {
            "user_goal": trace.get("user_goal"),
            "success": bool(trace.get("success")),
            "reward_profile": reward_profile,
            "selected_skills": trace.get("selected_skills", []),
            "tool_calls": summarized_calls[-10:]
        }
        prompt = (
            "Return one JSON object describing a reusable workflow pattern from this agent task. "
            "Do not create platform-specific rules unless they are explicitly represented by tool names or tool metadata. "
            "Prefer general preconditions and reusable tool-chain logic. "
            "Include whether this workflow should be reused or demoted based on reward_profile. "
            "Schema: {\"goal_type\": string, \"preconditions\": string[], \"successful_chain\": string[], \"notes\": string, \"should_create_skill\": boolean, \"reuse_confidence\": number}.\n"
            f"Trace: {json.dumps(compact_trace, ensure_ascii=False)}"
        )
        try:
            res = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You extract reusable workflow patterns for an autonomous tool-using agent. Return JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                **self.extra_params
            )
            pattern = json.loads(res.choices[0].message.content)
        except Exception:
            pattern = {
                "goal_type": "tool_chain_task",
                "preconditions": ["A similar user goal required multiple tool calls"],
                "successful_chain": successful_chain,
                "notes": "Reuse this chain only when current available skills and required parameters still match.",
                "should_create_skill": False
            }
        if not isinstance(pattern, dict):
            return
        chain = pattern.get("successful_chain")
        if not isinstance(chain, list) or not chain:
            pattern["successful_chain"] = successful_chain
        pattern["success_count"] = 1 if trace.get("success") else 0
        pattern["failure_count"] = 0 if trace.get("success") else 1
        pattern["example_user_goals"] = [str(trace.get("user_goal", ""))[:300]]
        pattern["metadata"] = {
            "selected_skills": trace.get("selected_skills", []),
            "tool_count": len(trace.get("tool_calls", [])),
            "reward_profile": reward_profile,
            "started_at": trace.get("started_at")
        }
        pattern["last_seen"] = time.time()
        try:
            await self.ctx.record_task_pattern(pattern)
        except Exception:
            pass
        try:
            workflow_id = await self.memory_engine.add_workflow_pattern(pattern)
            pattern["workflow_pattern_id"] = workflow_id
            if self.broadcast_event:
                await self.broadcast_event("workflow_pattern_learned", {
                    "id": workflow_id,
                    "goal_type": pattern.get("goal_type"),
                    "chain": pattern.get("successful_chain", [])
                })
        except Exception as e:
            logger.warning(f"Workflow pattern database persistence skipped: {e}")
        try:
            await self.dehydrator.process(trace.get("session_id", "default"), "Reusable Task Pattern:\n" + self._json_dumps_safe(pattern))
        except Exception:
            pass


    async def chat(self, session_id: str, user_input: str, domain: str = "auto"):
        session_id = session_id or "default"
        user_input = user_input or ""
        await self._cancel_pending_confirmations(session_id, "superseded_by_new_user_message")
        await self._set_active_chat_turn(session_id)
        if self.broadcast_event:
            await self.broadcast_event("chat_start", {"session_id": session_id, "user_input": user_input[:200]})

        await self.ctx.add_history(session_id, "user", user_input)
        history = await self._manage_context_window(session_id)
        history_without_current = history[:-1]
        if self.conversation_flow.should_refuse_tool_bypass_after_refusal(user_input, history_without_current):
            ans = self.conversation_flow.tool_bypass_refusal(user_input)
            await self.ctx.add_history(session_id, "assistant", ans)
            try:
                await self.dehydrator.process(session_id, f"User: {user_input}\nConsensus: {ans}")
            except Exception:
                pass
            if self.broadcast_event:
                await self.broadcast_event("chat_end", {"session_id": session_id, "answer_preview": ans[:200], "guardrail": "tool_bypass_after_refusal"})
            return ans
        task_focus = self.conversation_flow.build_task_focus(user_input, history_without_current)
        routing_input = task_focus.routing_text

        notifications = await self.ctx.pop_notifications(session_id)
        if notifications:
            return "Notifications\n" + "\n".join(notifications) + "\n\nPlease continue."
        if domain == "auto":
            domain = "general"
            for d, m in self.domain_meta.items():
                if any(kw in routing_input for kw in m.get("keywords", [])):
                    domain = d
                    break
        opinions_str = await self._gather_opinions(session_id, routing_input, domain, history_without_current)
        core_mem_str = json.dumps(await self.ctx.get_core_memory(session_id), ensure_ascii=False) or "None"
        try:
            past_patterns = await self.ctx.find_matching_task_patterns(routing_input)
        except Exception:
            past_patterns = []
        try:
            db_patterns = await self.memory_engine.search_workflow_patterns(routing_input, limit=3)
            if db_patterns:
                seen_ids = {str(p.get("id")) for p in past_patterns if isinstance(p, dict)}
                for pattern in db_patterns:
                    if str(pattern.get("id")) not in seen_ids:
                        past_patterns.append(pattern)
        except Exception as e:
            logger.debug(f"Workflow pattern database recall skipped: {e}")
        past_patterns = self._filter_relevant_past_patterns(routing_input, past_patterns)
        past_patterns_str = self._json_dumps_safe(past_patterns) if past_patterns else "None"
        ranked_skills = self._rank_skills(routing_input)
        skill_budget = self._skill_budget_for_task(routing_input)
        selected_skills = self._select_skills_for_prompt(routing_input, ranked_skills, skill_budget)
        if self.broadcast_event:
            await self.broadcast_event("skill_route", {
                "session_id": session_id,
                **self._build_skill_route_trace(routing_input, ranked_skills, selected_skills, skill_budget)
            })
        skill_instructions = self._build_skill_instructions(selected_skills)
        task_trace = {
            "session_id": session_id,
            "user_goal": user_input,
            "routing_goal": routing_input,
            "selected_skills": list(selected_skills.keys()),
            "tool_calls": [],
            "success": False,
            "final_answer": "",
            "started_at": time.time()
        }
        allowed_paths_str = ", ".join(self.allowed_paths) if self.allowed_paths else "No restrictions (workspace only)"
        static_system = self._build_static_system_prompt(domain)
        # Adapt system prompt to model tier
        static_system = adapt_system_rules(self.tier_config, static_system)
        plan_hint = self._build_plan_hint(routing_input, selected_skills)
        dynamic_system = self._build_dynamic_system_prompt(
            allowed_paths_str,
            core_mem_str,
            past_patterns_str,
            skill_instructions,
            plan_hint
        )
        # Adapt execution contract to model tier
        # Find the contract section and replace it
        contract_marker = "Execution Contract:"
        if contract_marker in dynamic_system and self.tier_config.execution_contract_mode != "full":
            pre = dynamic_system.split(contract_marker)[0]
            contract = contract_marker + dynamic_system.split(contract_marker)[1]
            adapted_contract = adapt_execution_contract(self.tier_config, contract)
            dynamic_system = pre + adapted_contract
        msgs = [{"role": "system", "content": static_system}]
        msgs.extend(history_without_current)
        msgs.append({"role": "system", "content": dynamic_system})
        user_prompt = f"User Query: {user_input}\nTask: Final consensus/execution."
        if task_focus.prompt_context:
            user_prompt += f"\nConversation Focus: {task_focus.prompt_context}"
        if task_focus.anchored_user_request:
            user_prompt += f"\nImmediate Previous User Request: {task_focus.anchored_user_request}"
        if opinions_str and opinions_str != "[]":
            user_prompt += f"\nExpert Opinions:\n{opinions_str}"
        msgs.append({"role": "user", "content": user_prompt})
        tool_failure_counter = {}
        last_tool_call = None
        repeat_count = 0
        skill_failures = {}
        for step in range(self.max_agent_steps):
            try:
                msgs = self._sanitize_openai_tool_message_sequence(msgs)
                res = await self.client.chat.completions.create(
                    model=self.model, messages=msgs,
                    tools=self.tool_manager.get_schemas(), tool_choice="auto",
                    **self.extra_params)
            except Exception as e:
                return f"System Error: {e}"
            msg = res.choices[0].message
            textual_tool_calls = []
            textual_tool_markup = False
            if not msg.tool_calls:
                textual_tool_markup = self.text_tool_call_parser.contains_tool_markup(msg.content or "")
                textual_tool_calls = self.text_tool_call_parser.parse(msg.content or "")
            if textual_tool_calls:
                msgs.append({
                    "role": "assistant",
                    "content": "Recovered textual tool call emitted by the model.",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in textual_tool_calls
                    ]
                })
                if self.broadcast_event:
                    await self.broadcast_event("text_tool_call_recovered", {
                        "session_id": session_id,
                        "count": len(textual_tool_calls),
                        "content_preview": (msg.content or "")[:200]
                    })
            else:
                msgs.append(msg)
            current_tool_calls = list(textual_tool_calls or msg.tool_calls or [])
            if current_tool_calls:
                task_completed = False
                ordered_tool_calls = []
                tool_tasks = []
                delayed_system_messages = []

                async def execute_one_tool_call(tc):
                    started_perf = time.perf_counter()
                    started_at = time.time()
                    try:
                        tool_arguments = self._repair_tool_arguments_for_context(
                            tc.function.name,
                            tc.function.arguments,
                            routing_input,
                        )
                        installed_skill_first_tools = {
                            "execute_system_command",
                            "write_and_execute_script",
                            "install_github_skill",
                            "register_new_tool",
                            "save_as_skill",
                            "promote_script_to_skill",
                        }
                        if (
                            tc.function.name in installed_skill_first_tools
                            and (
                                self._should_prefer_installed_skill_path(routing_input, selected_skills)
                                or self._trace_has_installed_skill_call(task_trace)
                            )
                        ):
                            out_value = self._blocked_script_tool_output(tc.function.name)
                        elif (
                            tc.function.name == "run_skill_script"
                            and self._skill_name_from_tool_arguments(tool_arguments) == "paper_fetch_review"
                            and self._trace_has_successful_paper_fetch(task_trace)
                        ):
                            out_value = self._blocked_extra_research_tool_output("paper_fetch_review")
                        elif (
                            tc.function.name == "run_skill_script"
                            and self._is_simple_research_lookup(routing_input)
                            and self._trace_has_successful_paper_fetch(task_trace)
                            and self._skill_name_from_tool_arguments(tool_arguments) in {
                                "paper_fetch_review",
                                "review_pipeline",
                                "citation_graph",
                                "paper_reader",
                                "evidence_matrix",
                                "citation_verifier",
                            }
                        ):
                            blocked_skill = self._skill_name_from_tool_arguments(tool_arguments)
                            out_value = self._blocked_extra_research_tool_output(blocked_skill)
                        else:
                            out_value = await self.tool_manager.call(tc.function.name, tool_arguments, session_id)
                        return {
                            "output": out_value,
                            "arguments": tool_arguments,
                            "started_at": started_at,
                            "duration_ms": round((time.perf_counter() - started_perf) * 1000, 2)
                        }
                    except Exception as e:
                        return {
                            "output": json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False),
                            "started_at": started_at,
                            "duration_ms": round((time.perf_counter() - started_perf) * 1000, 2)
                        }

                for tc in current_tool_calls:
                    current_call_sig = f"{tc.function.name}:{tc.function.arguments}"
                    if last_tool_call == current_call_sig:
                        repeat_count += 1
                    else:
                        repeat_count = 0
                        last_tool_call = current_call_sig
                    if repeat_count >= 2:
                        notification_msg = f"Agent is repeating the same tool call: {tc.function.name} with arguments {tc.function.arguments}. This may indicate a persistent failure. Please check logs or interrupt."
                        await self.ctx.push_notification(session_id, notification_msg)
                        delayed_system_messages.append({"role": "system", "content": notification_msg})
                        repeat_count = 0
                    if self.broadcast_event:
                        await self.broadcast_event("tool_start", {"name": tc.function.name, "arguments": tc.function.arguments, "session_id": session_id})
                    ordered_tool_calls.append(tc)
                    tool_tasks.append(execute_one_tool_call(tc))

                results = await asyncio.gather(*tool_tasks, return_exceptions=True)
                for tc, tool_result in zip(ordered_tool_calls, results):
                    if isinstance(tool_result, Exception):
                        tool_result = {
                            "output": json.dumps({"status": "error", "message": str(tool_result)}, ensure_ascii=False),
                            "started_at": time.time(),
                            "duration_ms": 0.0
                        }
                    out = tool_result.get("output", "")
                    if not isinstance(out, str):
                        out = str(out)
                    if self.broadcast_event:
                        tool_status = "success"
                        try:
                            parsed = json.loads(out) if isinstance(out, str) else out
                            if isinstance(parsed, dict):
                                st = parsed.get("status", "")
                                if st in ("error", "fail", "failure"):
                                    tool_status = "error"
                        except Exception:
                            if "error" in str(out).lower()[:500]:
                                tool_status = "error"
                        await self.broadcast_event("tool_end", {
                            "name": tc.function.name,
                            "status": tool_status,
                            "output_preview": out[:200],
                            "session_id": session_id,
                            "duration_ms": tool_result.get("duration_ms", 0),
                        })
                    parsed_out_for_trace = None
                    try:
                        parsed_out_for_trace = json.loads(out)
                    except Exception:
                        parsed_out_for_trace = None
                    task_trace["tool_calls"].append({
                        "tool": tc.function.name,
                        "arguments": tool_result.get("arguments", tc.function.arguments),
                        "raw_output": out[:5000] if isinstance(out, str) else str(out)[:5000],
                        "parsed_output": parsed_out_for_trace,
                        "started_at": tool_result.get("started_at"),
                        "duration_ms": tool_result.get("duration_ms", 0.0),
                        "timestamp": time.time()
                    })
                    msgs.append({"tool_call_id": tc.id, "role": "tool", "name": tc.function.name, "content": out})
                    try:
                        out_json = parsed_out_for_trace if isinstance(parsed_out_for_trace, dict) else json.loads(out)
                        if out_json.get("status") == "error":
                            tool_name = tc.function.name
                            tool_failure_counter[tool_name] = tool_failure_counter.get(tool_name, 0) + 1
                            if tool_failure_counter[tool_name] >= 2:
                                msgs.append({
                                    "role": "system",
                                    "content": f"Tool '{tool_name}' failed {tool_failure_counter[tool_name]} times. Correct parameters or change strategy."
                                })
                                tool_failure_counter.clear()
                        else:
                            tool_failure_counter.pop(tc.function.name, None)
                        if tc.function.name == 'run_skill_script':
                            try:
                                args = json.loads(tc.function.arguments)
                                skill_name = args.get('skill_name')
                            except:
                                skill_name = None
                            if skill_name:
                                if out_json.get("status") == "error":
                                    skill_failures[skill_name] = skill_failures.get(skill_name, 0) + 1
                                    if skill_failures[skill_name] >= 2:
                                        error_answer = (
                                            f"技能 '{skill_name}' 连续失败 {skill_failures[skill_name]} 次。\n"
                                            f"错误信息：{out_json.get('message', out)}\n"
                                            "已停止自动重试，避免静默切换资源或重复执行错误参数。"
                                        )
                                        task_trace["success"] = False
                                        task_trace["final_answer"] = error_answer
                                        await self._learn_from_task_trace(task_trace)
                                        return error_answer
                                else:
                                    skill_failures.pop(skill_name, None)
                    except:
                        pass
                    try:
                        if json.loads(out).get("completed"):
                            task_completed = True
                    except:
                        pass
                msgs.extend(delayed_system_messages)
                if task_completed:
                    msgs.append({
                        "role": "system",
                        "content": (
                            "One or more tools completed. If the result satisfies the user request, provide the final answer now. "
                            "If the result is insufficient and another tool call is needed, use the API tool_calls field only. "
                            "Never emit DSML/XML/markdown tool-call markup as assistant text."
                        )
                    })
                    continue
            else:
                if textual_tool_markup:
                    ans = "模型输出了无法解析的文本态工具调用，已阻止原样返回。请重试当前请求。"
                else:
                    research_payload = self._latest_research_payload_from_trace(task_trace)
                    if self._should_use_deterministic_abstract_answer(research_payload, routing_input):
                        deterministic_research_answer = self._format_paper_abstract_answer(research_payload, user_input)
                    elif self._should_use_deterministic_research_answer(research_payload, routing_input):
                        deterministic_research_answer = self._format_research_lookup_answer(research_payload, user_input)
                    else:
                        deterministic_research_answer = ""
                    ans = deterministic_research_answer or msg.content or "Task completed."
                task_trace["success"] = bool(task_trace.get("tool_calls"))
                task_trace["final_answer"] = ans
                await self.ctx.add_history(session_id, "assistant", ans)
                await self._learn_from_task_trace(task_trace)
                await self.dehydrator.process(session_id, f"User: {user_input}\nConsensus: {ans}")
                if self.broadcast_event:
                    await self.broadcast_event("chat_end", {"session_id": session_id, "answer_preview": ans[:200]})
                return ans
        timeout_answer = "Task took too long."
        task_trace["success"] = False
        task_trace["final_answer"] = timeout_answer
        await self._learn_from_task_trace(task_trace)
        if self.broadcast_event:
            await self.broadcast_event("chat_end", {"session_id": session_id, "answer_preview": timeout_answer})
        return timeout_answer

if FASTAPI_AVAILABLE:
    from integrations.feishu_bot import FeishuBotAdapter, FeishuConfig

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
            self.bound_host = "0.0.0.0"
            self.bound_port = 8000
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
            self.app.get("/memory/ontology")(self.get_memory_ontology_endpoint)
            self.app.get("/memory/hypergraph")(self.get_memory_hypergraph_endpoint)
            # Graph traversal endpoints (Neo4j-powered)
            self.app.get("/graph/citation_path")(self.graph_citation_path_endpoint)
            self.app.get("/graph/cited_by_chain")(self.graph_cited_by_chain_endpoint)
            self.app.get("/graph/coauthor_network")(self.graph_coauthor_network_endpoint)
            self.app.get("/graph/influential")(self.graph_influential_endpoint)
            self.app.get("/graph/research_gaps")(self.graph_research_gaps_endpoint)
            self.app.get("/graph/community")(self.graph_community_endpoint)
            # Engine status
            self.app.get("/engines/status")(self.engines_status_endpoint)
            self.app.post("/conversations/clear")(self.clear_conversations_endpoint)
            self.app.get("/skills/list")(self.list_skills_endpoint)
            self.app.get("/skills/read")(self.read_skill_file_endpoint)
            self.app.post("/skills/write")(self.write_skill_file_endpoint)
            self.app.post("/skills/rollback")(self.rollback_skill_file_endpoint)
            self.app.get("/check_confirmation")(self.check_confirmation_endpoint)
            self.app.post("/submit_confirmation")(self.submit_confirmation_endpoint)
            self.app.add_api_websocket_route("/ws/agent-events", self.agent_events_ws)
            # Decision tracker endpoints
            self.app.post("/decisions/record")(self.record_decision_endpoint)
            self.app.post("/decisions/conflicts")(self.detect_conflicts_endpoint)
            self.app.get("/decisions/list")(self.list_decisions_endpoint)
            self.app.get("/decisions/stats")(self.decisions_stats_endpoint)
            self.app.delete("/decisions/delete")(self.delete_decision_endpoint)

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

        def _write_runtime_files(self):
            runtime_dir = BASE_DIR.parent / ".runtime"
            try:
                runtime_dir.mkdir(exist_ok=True)
                (runtime_dir / "backend_port.txt").write_text(str(self.bound_port), encoding="utf-8")
                (runtime_dir / "backend_pid.txt").write_text(str(os.getpid()), encoding="utf-8")
                (runtime_dir / "backend_host.txt").write_text(str(self.bound_host), encoding="utf-8")
            except OSError as exc:
                logger.warning(f"Could not write backend runtime metadata: {exc}")

        def _clear_runtime_pid_file(self):
            pid_file = BASE_DIR.parent / ".runtime" / "backend_pid.txt"
            try:
                if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    pid_file.unlink()
            except OSError as exc:
                logger.warning(f"Could not clear backend pid metadata: {exc}")

        @asynccontextmanager
        async def lifespan(self, app: FastAPI):
            self._write_runtime_files()
            self.decision_tracker: DecisionTracker | None = None
            try:
                self.agent = await self.agent_factory()
                self.agent.broadcast_event = self.broadcast_agent_event
                self.agent_startup_error = None
                # Initialize decision tracker (hypergraph-backed)
                if self.agent:
                    try:
                        db = self.agent.memory_engine.db
                        if db and getattr(db, 'pg_pool', None) and getattr(db, 'service', None):
                            self.decision_tracker = DecisionTracker(db)
                            logger.info("Decision tracker initialized (hypergraph-backed)")
                    except Exception as e:
                        logger.warning(f"Decision tracker not available: {e}")
            except Exception as exc:
                self.agent = None
                self.agent_startup_error = f"{exc.__class__.__name__}: {exc}"
                logger.error(f"Agent startup failed; HTTP status API remains available: {exc}", exc_info=True)
            yield
            if self.agent:
                await self.agent.close()
            self._clear_runtime_pid_file()

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
            try:
                from memory_ontology import default_memory_ontology
                payload = default_memory_ontology()
                payload["status"] = "success"
                return payload
            except Exception as exc:
                logger.warning(f"Memory ontology endpoint failed: {exc}", exc_info=True)
                return {"status": "error", "message": str(exc), "node_types": [], "relation_types": []}

        # ── Graph traversal endpoints (Neo4j + Redis cache) ──

        async def graph_citation_path_endpoint(self, from_id: str = "", to_id: str = "", max_depth: int = 5):
            if not from_id or not to_id:
                return {"error": "Missing from_id or to_id"}
            svc = self._memory_service()
            return await svc.graph_citation_path(from_id, to_id, max_depth) if svc else {"error": "Memory unavailable"}

        async def graph_cited_by_chain_endpoint(self, paper_id: str = "", depth: int = 2):
            if not paper_id:
                return {"error": "Missing paper_id"}
            svc = self._memory_service()
            return await svc.graph_cited_by_chain(paper_id, depth) if svc else {"error": "Memory unavailable"}

        async def graph_coauthor_network_endpoint(self, author: str = "", depth: int = 2):
            if not author:
                return {"error": "Missing author name"}
            svc = self._memory_service()
            return await svc.graph_coauthor_network(author, depth) if svc else {"error": "Memory unavailable"}

        async def graph_influential_endpoint(self, topic: str = "", limit: int = 10):
            svc = self._memory_service()
            return await svc.graph_influential_papers(topic, limit) if svc else []

        async def graph_research_gaps_endpoint(self, keyword: str = "", limit: int = 10):
            svc = self._memory_service()
            return await svc.graph_research_gaps(keyword, limit) if svc else []

        async def graph_community_endpoint(self, label: str = "Paper"):
            db = self._memory_db()
            if db and getattr(db, 'graph_engine', None):
                return await db.graph_engine.community_detect(label)
            return {"error": "Graph engine unavailable"}

        async def engines_status_endpoint(self):
            db = self._memory_db()
            return {
                "postgres": "online" if db and db.pg_pool else "offline",
                "redis": "online" if db and db.redis else "offline",
                "neo4j": "online" if db and getattr(db, 'neo4j', None) else "offline",
                "graph_engine": "online" if db and getattr(db, 'graph_engine', None) and db.graph_engine.is_available() else "offline",
                "cache_engine": "online" if db and getattr(db, 'cache_engine', None) else "offline",
            }

        async def get_memory_hypergraph_endpoint(self, kind: str = "", limit: int = 200):
            db = self._memory_db()
            service = getattr(db, "service", None) if db else None
            if not service:
                return {
                    "status": "degraded",
                    "message": self.agent_startup_error or "memory database unavailable",
                    "nodes": [],
                    "edges": [],
                    "hyperedges": [],
                }
            try:
                safe_limit = max(1, min(int(limit or 200), 500))
                if kind:
                    nodes = await service.query_ontology_nodes(kind=kind, limit=safe_limit)
                    return {"status": "success", "nodes": nodes, "edges": [], "hyperedges": []}
                payload = await service.query_hypergraph(limit_nodes=safe_limit, limit_edges=min(safe_limit, 200))
                payload["status"] = "success"
                return payload
            except Exception as exc:
                logger.warning(f"Memory hypergraph endpoint failed: {exc}", exc_info=True)
                return {"status": "error", "message": str(exc), "nodes": [], "edges": [], "hyperedges": []}

        def _skill_root(self) -> Path:
            return (BASE_DIR / "skills").resolve()

        def _skill_rollback_file(self) -> Path:
            runtime_dir = BASE_DIR.parent / ".runtime"
            runtime_dir.mkdir(exist_ok=True)
            return runtime_dir / "skill_rollbacks.json"

        def _resolve_skill_file(self, relative_path: str, *, require_existing: bool = True) -> Path:
            rel = str(relative_path or "").strip().replace("\\", "/")
            if not rel:
                raise ValueError("Missing path.")
            root = self._skill_root()
            target = (root / rel).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("Path traversal denied.") from exc
            allowed_suffixes = {".md", ".py", ".toml", ".json", ".yaml", ".yml"}
            if target.suffix.lower() not in allowed_suffixes:
                raise ValueError("Unsupported skill file type.")
            if require_existing and not target.is_file():
                raise ValueError("Skill file not found.")
            return target

        async def list_skills_endpoint(self):
            skills_root = self._skill_root()
            result = []
            try:
                if skills_root.is_dir():
                    for md_path in sorted(skills_root.rglob("SKILL.md")):
                        rel_dir = md_path.parent.relative_to(skills_root)
                        files = []
                        for item in sorted(md_path.parent.rglob("*")):
                            if item.is_file() and item.suffix.lower() in {".md", ".py", ".toml", ".json", ".yaml", ".yml"}:
                                files.append({
                                    "name": item.name,
                                    "path": item.relative_to(skills_root).as_posix(),
                                })
                        result.append({
                            "name": rel_dir.as_posix() or md_path.parent.name,
                            "path": rel_dir.as_posix(),
                            "files": files,
                        })
                return {"status": "success", "skills": result, "total": len(result)}
            except Exception as exc:
                logger.warning(f"Failed to list skills: {exc}", exc_info=True)
                return {"status": "error", "message": str(exc), "skills": []}

        async def read_skill_file_endpoint(self, path: str = ""):
            try:
                target = self._resolve_skill_file(path)
                rel = target.relative_to(self._skill_root()).as_posix()
                return {"status": "success", "path": rel, "content": target.read_text(encoding="utf-8", errors="replace")}
            except Exception as exc:
                logger.warning(f"Failed to read skill file {path}: {exc}")
                return {"status": "error", "message": str(exc), "path": path}

        async def write_skill_file_endpoint(self, request: Request):
            data = await self._safe_request_json(request)
            rel_path = str(data.get("path") or "").strip()
            content = str(data.get("content") or "")
            try:
                target = self._resolve_skill_file(rel_path)
                original = target.read_text(encoding="utf-8", errors="replace")
                if original == content:
                    return {"status": "success", "message": "No changes.", "snapshot": None}
                snapshot = {
                    "id": str(uuid.uuid4()),
                    "skill_name": str(data.get("skill_name") or target.parent.name),
                    "path": target.relative_to(self._skill_root()).as_posix(),
                    "timestamp": time.time(),
                    "original_content": original,
                }
                target.write_text(content, encoding="utf-8")
                rollback_file = self._skill_rollback_file()
                rollbacks = []
                if rollback_file.is_file():
                    try:
                        loaded = json.loads(rollback_file.read_text(encoding="utf-8"))
                        rollbacks = loaded if isinstance(loaded, list) else []
                    except Exception:
                        rollbacks = []
                rollbacks.insert(0, snapshot)
                rollback_file.write_text(json.dumps(rollbacks[:50], ensure_ascii=False, indent=2), encoding="utf-8")
                return {"status": "success", "snapshot": snapshot["id"], "message": "File written and rollback snapshot created."}
            except Exception as exc:
                logger.warning(f"Failed to write skill file {rel_path}: {exc}", exc_info=True)
                return {"status": "error", "message": str(exc)}

        async def rollback_skill_file_endpoint(self, request: Request):
            data = await self._safe_request_json(request)
            snapshot_id = str(data.get("snapshot_id") or "").strip()
            rollback_file = self._skill_rollback_file()
            if not rollback_file.is_file():
                return {"status": "success", "rollbacks": [], "total": 0} if not snapshot_id else {"status": "error", "message": "No rollback file found."}
            try:
                loaded = json.loads(rollback_file.read_text(encoding="utf-8"))
                rollbacks = loaded if isinstance(loaded, list) else []
                if not snapshot_id:
                    public_rollbacks = [{k: v for k, v in item.items() if k != "original_content"} for item in rollbacks]
                    return {"status": "success", "rollbacks": public_rollbacks, "total": len(public_rollbacks)}
                target_snapshot = None
                remaining = []
                for snapshot in rollbacks:
                    if snapshot.get("id") == snapshot_id and target_snapshot is None:
                        target_snapshot = snapshot
                    else:
                        remaining.append(snapshot)
                if not target_snapshot:
                    return {"status": "error", "message": f"Snapshot {snapshot_id} not found."}
                target = self._resolve_skill_file(str(target_snapshot.get("path") or ""))
                target.write_text(str(target_snapshot.get("original_content") or ""), encoding="utf-8")
                rollback_file.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")
                return {"status": "success", "message": f"Rolled back {target_snapshot.get('path')}."}
            except Exception as exc:
                logger.warning(f"Failed to rollback skill file: {exc}", exc_info=True)
                return {"status": "error", "message": str(exc)}

        def _memory_db(self):
            if not self.agent:
                return None
            engine = getattr(self.agent, "memory_engine", None)
            return getattr(engine, "db", None)

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
                            f"{provider_label} 尚未配置 API Key。请在 pysrc/model.toml 中添加该厂商的 api_key，"
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

        # ── Decision tracker endpoints ─────────────────

        async def _get_decision_tracker(self) -> DecisionTracker | None:
            if self.decision_tracker:
                return self.decision_tracker
            # Lazy init: pass memory_db so DecisionTracker can use ontology + hypergraph
            if self.agent:
                try:
                    db = self.agent.memory_engine.db
                    if db and getattr(db, 'pg_pool', None) and getattr(db, 'service', None):
                        self.decision_tracker = DecisionTracker(db)
                        logger.info("DecisionTracker initialized (hypergraph-backed)")
                        return self.decision_tracker
                except Exception as e:
                    logger.warning(f"DecisionTracker lazy init failed: {e}")
            return None

        async def record_decision_endpoint(self, request: Request):
            try:
                dt = await self._get_decision_tracker()
                if not dt:
                    return {"status": "error", "message": "Decision tracker unavailable — database not connected"}
                data = await self._safe_request_json(request)
                decision_id = await dt.record(
                    session_id=str(data.get("session_id", "default")),
                    owner_id=str(data.get("owner_id", "shared")),
                    owner_name=str(data.get("owner_name", "")),
                    topic=str(data.get("topic", "")),
                    question=str(data.get("question", "")),
                    chosen=str(data.get("chosen", "")),
                    alternatives=data.get("alternatives", []),
                    rationale=str(data.get("rationale", "")),
                    tags=data.get("tags", []),
                    scope=str(data.get("scope", "project")),
                )
                return {"status": "success", "decision_id": decision_id}
            except Exception as exc:
                logger.error(f"record_decision error: {exc}", exc_info=True)
                return {"status": "error", "message": f"{exc.__class__.__name__}: {exc}"}

        async def detect_conflicts_endpoint(self, request: Request):
            dt = await self._get_decision_tracker()
            if not dt:
                return {"status": "degraded", "has_conflict": False, "conflicts": [], "message": "Decision tracker unavailable"}
            data = await self._safe_request_json(request)
            result = await dt.detect_conflict(
                topic_hint=str(data.get("topic_hint", "")),
                tags=data.get("tags", []),
                current_owner_id=str(data.get("owner_id", "")),
                scope=str(data.get("scope", "project")),
            )
            return {
                "has_conflict": result.has_conflict,
                "conflict_description": result.conflict_description,
                "suggestion": result.suggestion,
                "relevant_decisions": [
                    {
                        "id": d.id,
                        "topic": d.topic,
                        "question": d.question,
                        "chosen": d.chosen,
                        "owner_name": d.owner_name or d.owner_id,
                        "owner_id": d.owner_id,
                        "rationale": d.rationale,
                        "created_at": d.created_at,
                    }
                    for d in result.prior_decisions
                ],
            }

        async def list_decisions_endpoint(self, topic: str = "", owner_id: str = "", limit: int = 50, offset: int = 0):
            dt = await self._get_decision_tracker()
            if not dt:
                return {"status": "degraded", "decisions": [], "total": 0, "message": "Decision tracker unavailable"}
            records = await dt.list(topic=topic, owner_id=owner_id, limit=min(int(limit), 200), offset=int(offset))
            return {
                "decisions": [
                    {
                        "id": r.id,
                        "session_id": r.session_id,
                        "owner_name": r.owner_name or r.owner_id,
                        "owner_id": r.owner_id,
                        "topic": r.topic,
                        "question": r.question,
                        "chosen": r.chosen,
                        "alternatives": r.alternatives,
                        "rationale": r.rationale,
                        "tags": r.tags,
                        "scope": r.scope,
                        "created_at": r.created_at,
                    }
                    for r in records
                ],
                "total": len(records),
            }

        async def decisions_stats_endpoint(self):
            dt = await self._get_decision_tracker()
            if not dt:
                return {"status": "degraded", "message": "Decision tracker unavailable"}
            return await dt.get_stats()

        async def delete_decision_endpoint(self, request: Request):
            dt = await self._get_decision_tracker()
            if not dt:
                return {"status": "error", "message": "Decision tracker unavailable"}
            data = await self._safe_request_json(request)
            decision_id = str(data.get("id") or "").strip()
            if not decision_id:
                return {"status": "error", "message": "Missing decision id"}
            ok = await dt.delete(decision_id)
            return {"status": "success" if ok else "not_found"}

        def run(self, host="0.0.0.0", port=8000):
            self.bound_host = host
            self.bound_port = port
            logger.info(f"Starting HTTP API on http://{host}:{port}")
            uvicorn.run(self.app, host=host, port=port)

class CLIChannel:
    def __init__(self, agent_factory):
        self.agent_factory = agent_factory
        self.session_id = None

    async def run(self):
        username = input("Username: ").strip() or "anonymous"
        self.session_id = f"user_{username}"
        agent = await self.agent_factory()
        try:
            while True:
                user_input = await asyncio.to_thread(input, f"\n{username}> ")
                if user_input.lower() in ['exit', 'quit']:
                    break
                if user_input.strip():
                    answer = await agent.chat(self.session_id, user_input, domain='auto')
                    print(f"Agent: {answer}")
        finally:
            await agent.close()

async def main():
    config = load_config("model.toml")
    async def agent_factory():
        agent = YuanGeAgent(config)
        await agent.initialize()
        return agent
    channel = CLIChannel(agent_factory)
    await channel.run()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    parser = argparse.ArgumentParser(description="Autonomous Agent Runner")
    parser.add_argument("--serve", action="store_true", help="Start the FastAPI IM Gateway")
    parser.add_argument("--api", action="store_true", help="Start HTTP API for frontend (React)")
    parser.add_argument("--self-test", action="store_true", help="Initialize backend dependencies and exit")
    parser.add_argument("--host", default=os.environ.get("MEGATRON_BACKEND_HOST", "0.0.0.0"), help="Host for HTTP API or gateway")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MEGATRON_BACKEND_PORT", "8000")), help="Port for HTTP API or gateway")
    args = parser.parse_args()
    if args.self_test:
        async def self_test():
            config = load_config("model.toml")
            agent = YuanGeAgent(config)
            await agent.initialize()
            payload = {
                "status": "success",
                "neo4j": True,
                "postgres": True,
                "redis": True,
                "loaded_skills": sorted(agent.loaded_skills.keys())
            }
            print(json.dumps(payload, ensure_ascii=False))
            await agent.close()
        asyncio.run(self_test())
    elif args.api:
        if not FASTAPI_AVAILABLE:
            print("FastAPI is not installed. Please run: pip install fastapi uvicorn")
            sys.exit(1)
        config = load_config("model.toml")
        async def agent_factory():
            agent = YuanGeAgent(config)
            await agent.initialize()
            return agent
        api = AgentAPI(config, agent_factory)
        api.run(host=args.host, port=args.port)
    elif args.serve:
        if not FASTAPI_AVAILABLE:
            print("FastAPI is not installed. Please run: pip install fastapi uvicorn")
            sys.exit(1)
        config = load_config("model.toml")
        async def agent_factory():
            agent = YuanGeAgent(config)
            await agent.initialize()
            return agent
        gw = IMGateway(config, agent_factory)
        gw.run(host=args.host, port=args.port)
    else:
        asyncio.run(main())

