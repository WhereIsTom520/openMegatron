"""QQ bot adapter for OpenMegatron.

Supports QQ official bot platform (QQ开放平台) via WebSocket + HTTP API.
Reference: https://bot.q.qq.com/wiki/develop/api/

Architecture:
  QQBotConfig    – dataclass loaded from env vars or TOML config
  QQBotAdapter   – handles WebSocket events, message parsing,
                   deduplication, confirmation flow, and reply delivery

Unlike Feishu/WeChat Work which use webhook callbacks, QQ bot uses a
persistent WebSocket connection.  The adapter runs a background task
that connects to the QQ WebSocket gateway and processes events.

Message flow:
  QQ Gateway (WebSocket) → on_message event
    → parse text content
    → deduplicate via Redis SETNX
    → agent.chat(session_id, text) + watch_confirmations()
    → reply via HTTP API (POST /v2/users/{openid}/messages)
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import aiohttp

try:
    from fastapi import HTTPException
except Exception:  # pragma: no cover
    HTTPException = None


logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────────────

APPROVE_WORDS = {"/approve", "approve", "同意", "允许", "确认", "y", "yes"}
DENY_WORDS = {"/deny", "deny", "拒绝", "不同意", "取消", "n", "no"}

SLASH_COMMANDS = [
    {"command": "/help", "description": "显示帮助信息"},
    {"command": "/status", "description": "查看系统状态"},
    {"command": "/clear", "description": "清除当前对话上下文"},
    {"command": "/retry", "description": "重试上一次失败的请求"},
    {"command": "/search", "description": "在知识库中搜索"},
    {"command": "/skills", "description": "列出可用技能"},
]

# QQ WebSocket gateway opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# QQ WebSocket intents
INTENT_PUBLIC_GUILD_MESSAGES = 1 << 30
INTENT_DIRECT_MESSAGE = 1 << 12
INTENT_GROUP_AT_MESSAGE = 1 << 25
DEFAULT_INTENTS = INTENT_PUBLIC_GUILD_MESSAGES | INTENT_DIRECT_MESSAGE | INTENT_GROUP_AT_MESSAGE

# i18n
I18N = {
    "confirm_title": {"zh": "需要确认后继续执行", "en": "Confirmation Required"},
    "confirm_approve": {"zh": "允许", "en": "Approve"},
    "confirm_deny": {"zh": "拒绝", "en": "Deny"},
    "confirm_hint": {"zh": "也可以回复 /approve {id} 或 /deny {id}", "en": "Or reply /approve {id} or /deny {id}"},
    "processing": {"zh": "正在处理...", "en": "Processing..."},
    "error_generic": {"zh": "处理失败：{error}", "en": "Processing failed: {error}"},
    "help_text": {
        "zh": "可用命令：\n/help - 帮助\n/status - 状态\n/clear - 清上下文\n/retry - 重试\n/search <关键词> - 搜索\n/skills - 技能列表",
        "en": "Commands:\n/help - Help\n/status - Status\n/clear - Clear context\n/retry - Retry\n/search <query> - Search\n/skills - List skills",
    },
    "status_text": {"zh": "系统运行正常", "en": "System operational"},
    "context_cleared": {"zh": "对话上下文已清除", "en": "Conversation context cleared"},
    "no_retry": {"zh": "没有可重试的请求", "en": "No request to retry"},
    "unknown_command": {"zh": "未知命令：{cmd}。输入 /help 查看可用命令。", "en": "Unknown command: {cmd}. Type /help for commands."},
    "delivery_retry": {"zh": "消息发送失败，第 {n} 次重试中...", "en": "Delivery failed, retry {n}..."},
}


# ── Config ───────────────────────────────────────────────────────────────────

@dataclass
class QQBotConfig:
    app_id: str = ""
    app_secret: str = ""         # client secret (also used as bot token in some contexts)
    bot_app_id: str = ""         # QQ bot's app_id (may differ from app_id for some setups)
    api_base: str = "https://api.sgroup.qq.com"  # sandbox: https://sandbox.api.sgroup.qq.com
    gateway_url: str = "wss://api.sgroup.qq.com/websocket"  # WebSocket gateway
    request_timeout_sec: int = 20
    reply_enabled: bool = True
    confirmation_watch_sec: int = 65
    max_retries: int = 3
    retry_delay_sec: float = 1.0
    enable_slash_commands: bool = True
    lang: str = "zh"
    # WebSocket
    ws_reconnect_delay_sec: float = 3.0
    ws_max_reconnect_attempts: int = 10
    ws_heartbeat_interval_sec: int = 30
    intents: int = DEFAULT_INTENTS

    @classmethod
    def from_config(cls, config: dict) -> "QQBotConfig":
        integrations = config.get("integrations", {}) if isinstance(config, dict) else {}
        qq_cfg = integrations.get("qq", {}) or config.get("qq", {}) or {}

        def env_or_cfg(env_name: str, key: str, default: Any = ""):
            value = os.environ.get(env_name)
            if value is not None:
                return value
            return qq_cfg.get(key, default)

        def to_bool(value: Any, default: bool = True) -> bool:
            if value is None:
                return default
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

        def to_int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        return cls(
            app_id=str(env_or_cfg("QQ_APP_ID", "app_id", "")),
            app_secret=str(env_or_cfg("QQ_APP_SECRET", "app_secret", "")),
            bot_app_id=str(env_or_cfg("QQ_BOT_APP_ID", "bot_app_id", "")),
            api_base=str(env_or_cfg("QQ_API_BASE", "api_base", "https://api.sgroup.qq.com")).rstrip("/"),
            gateway_url=str(env_or_cfg("QQ_GATEWAY_URL", "gateway_url", "wss://api.sgroup.qq.com/websocket")),
            request_timeout_sec=to_int(env_or_cfg("QQ_REQUEST_TIMEOUT_SEC", "request_timeout_sec", 20), 20),
            reply_enabled=to_bool(env_or_cfg("QQ_REPLY_ENABLED", "reply_enabled", True)),
            confirmation_watch_sec=to_int(env_or_cfg("QQ_CONFIRMATION_WATCH_SEC", "confirmation_watch_sec", 65), 65),
            max_retries=to_int(env_or_cfg("QQ_MAX_RETRIES", "max_retries", 3), 3),
            retry_delay_sec=float(env_or_cfg("QQ_RETRY_DELAY_SEC", "retry_delay_sec", 1.0)),
            enable_slash_commands=to_bool(env_or_cfg("QQ_SLASH_COMMANDS", "enable_slash_commands", True)),
            lang=str(env_or_cfg("QQ_LANG", "lang", "zh")).lower()[:2],
            ws_reconnect_delay_sec=float(env_or_cfg("QQ_WS_RECONNECT_DELAY", "ws_reconnect_delay_sec", 3.0)),
            ws_max_reconnect_attempts=to_int(env_or_cfg("QQ_WS_MAX_RECONNECT", "ws_max_reconnect_attempts", 10), 10),
            ws_heartbeat_interval_sec=to_int(env_or_cfg("QQ_WS_HEARTBEAT", "ws_heartbeat_interval_sec", 30), 30),
            intents=to_int(env_or_cfg("QQ_INTENTS", "intents", DEFAULT_INTENTS), DEFAULT_INTENTS),
        )


# ── Delivery Status ──────────────────────────────────────────────────────────

@dataclass
class DeliveryStatus:
    message_id: str = ""
    target_id: str = ""
    status: str = "pending"
    attempts: int = 0
    last_error: str = ""
    created_at: float = field(default_factory=time.time)


# ── Adapter ──────────────────────────────────────────────────────────────────

class QQBotAdapter:
    name = "qq"

    def __init__(self, config: QQBotConfig, logger_instance=None):
        self.config = config
        self.log = logger_instance or logger
        self._access_token: Optional[str] = None
        self._access_token_expire_at = 0.0
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_session_num: Optional[int] = None
        self._ws_sequence: Optional[int] = None
        self._ws_connected = False
        self._ws_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._delivery_statuses: dict[str, DeliveryStatus] = {}
        self._last_failed_request: dict[str, str] = {}
        self._retry_count: dict[str, int] = {}
        self._agent = None  # set when connected

    # ═══════════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════════

    async def start(self, agent) -> None:
        """Start the WebSocket connection to QQ Gateway."""
        self._agent = agent
        if self._ws_task and not self._ws_task.done():
            return
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._ws_connected = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None

    async def _ws_loop(self) -> None:
        """Main WebSocket loop with automatic reconnection."""
        attempt = 0
        while self._agent is not None and attempt < self.config.ws_max_reconnect_attempts:
            try:
                await self._ws_connect()
                attempt = 0  # reset on successful connection
                await self._ws_listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                attempt += 1
                self.log.warning(
                    f"QQ WebSocket disconnected (attempt {attempt}/{self.config.ws_max_reconnect_attempts}): {e}"
                )
                await asyncio.sleep(self.config.ws_reconnect_delay_sec * min(attempt, 5))

    async def _ws_connect(self) -> None:
        """Establish WebSocket connection and identify."""
        # Step 1: Get gateway URL (QQ may return a different wss URL)
        try:
            gateway_url = await self._get_gateway_url()
        except Exception:
            gateway_url = self.config.gateway_url

        # Step 2: Connect
        timeout = aiohttp.ClientWSTimeout(ws_close=self.config.request_timeout_sec)
        self._ws = await aiohttp.ClientSession().ws_connect(
            gateway_url, timeout=timeout, heartbeat=self.config.ws_heartbeat_interval_sec,
        )
        self._ws_connected = True
        self.log.info(f"QQ WebSocket connected to {gateway_url}")

        # Step 3: Wait for HELLO
        hello = await self._ws.receive_json()
        if hello.get("op") != OP_HELLO:
            raise RuntimeError(f"Expected HELLO opcode, got {hello.get('op')}")
        heartbeat_ms = hello.get("d", {}).get("heartbeat_interval", 30000)
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(heartbeat_ms / 1000.0)
        )

        # Step 4: Identify
        token = await self._get_access_token()
        identify_payload = {
            "op": OP_IDENTIFY,
            "d": {
                "token": f"QQBot {token}",
                "intents": self.config.intents,
                "shard": [0, 1],
                "properties": {},
            },
        }
        await self._ws.send_json(identify_payload)

    async def _ws_listen(self) -> None:
        """Listen for WebSocket events."""
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                await self._handle_ws_event(data)
            elif msg.type == aiohttp.WSMsgType.CLOSED:
                break
            elif msg.type == aiohttp.WSMsgType.ERROR:
                self.log.error(f"QQ WebSocket error: {self._ws.exception()}")
                break

    async def _handle_ws_event(self, data: dict) -> None:
        """Dispatch a WebSocket event."""
        op = data.get("op")
        d = data.get("d", {})
        seq = data.get("s")
        if seq is not None:
            self._ws_sequence = seq

        if op == OP_DISPATCH:
            event_type = data.get("t", "")
            if event_type == "READY":
                self._ws_session_num = d.get("session_id")
                self.log.info(f"QQ bot ready (session={self._ws_session_num})")
            elif event_type == "RESUMED":
                self.log.info("QQ bot session resumed")
            elif event_type in ("AT_MESSAGE_CREATE", "MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE",
                                "GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"):
                await self._handle_message_event(d, event_type)
            elif event_type == "INTERACTION_CREATE":
                await self._handle_interaction_event(d)

        elif op == OP_HEARTBEAT_ACK:
            pass  # heartbeat acknowledged

        elif op == OP_RECONNECT:
            self.log.info("QQ Gateway requested reconnect")
            self._ws_connected = False

        elif op == OP_INVALID_SESSION:
            self.log.warning("QQ Gateway reported invalid session, will re-identify")
            self._ws_session_num = None

    async def _heartbeat_loop(self, interval: float) -> None:
        """Send periodic heartbeats."""
        while self._ws_connected:
            await asyncio.sleep(interval)
            if self._ws and not self._ws.closed and self._ws_sequence is not None:
                try:
                    await self._ws.send_json({"op": OP_HEARTBEAT, "d": self._ws_sequence})
                except Exception:
                    break

    # ═══════════════════════════════════════════════════════════════
    # Gateway URL
    # ═══════════════════════════════════════════════════════════════

    async def _get_gateway_url(self) -> str:
        """Get the recommended WebSocket gateway URL from QQ API."""
        token = await self._get_access_token()
        url = f"{self.config.api_base}/gateway"
        headers = {"Authorization": f"QQBot {token}"}
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json(content_type=None)
        if data.get("url"):
            return data["url"]
        return self.config.gateway_url

    # ═══════════════════════════════════════════════════════════════
    # Message Handling
    # ═══════════════════════════════════════════════════════════════

    async def _handle_message_event(self, event: dict, event_type: str) -> None:
        """Process an incoming message event from QQ."""
        if not self._agent:
            return

        message = self._parse_qq_message(event, event_type)
        if not message:
            return

        if await self._is_duplicate_event(message):
            return

        text = message.get("text", "")

        # Confirmation action
        confirm_action = self._parse_confirmation_action(text)
        if confirm_action:
            result = await self._apply_confirmation_action(message, confirm_action)
            if result.get("status") == "success":
                await self._deliver_text(message, result.get("message", "已处理确认请求。"))
            return

        # Slash commands
        if self.config.enable_slash_commands and text.startswith("/"):
            slash_result = await self._handle_slash_command(message, text)
            if slash_result:
                await self._deliver_text(message, slash_result)
                return

        # Save for retry
        session_id = self._session_id_for(message)
        self._last_failed_request[session_id] = text

        # Process
        asyncio.create_task(self._process_and_reply(message))

    async def _handle_interaction_event(self, event: dict) -> None:
        """Handle QQ interaction (button clicks from message components)."""
        if not self._agent:
            return
        interaction_data = event.get("data", {})
        button_data = interaction_data.get("resolved", {})
        action = button_data.get("action", "")
        session_id = button_data.get("session_id", "")
        request_id = button_data.get("request_id", "")

        if action in ("approve", "deny") and session_id:
            result = await self._apply_confirmation_action_for_session(
                session_id, action, request_id,
            )
            # QQ expects a callback response
            pass

    def _parse_qq_message(self, event: dict, event_type: str) -> Optional[dict]:
        """Parse a QQ message event into a normalized message dict."""
        author = event.get("author", {})
        content = (event.get("content") or "").strip()
        msg_id = event.get("id", "")
        channel_id = event.get("channel_id", "")
        guild_id = event.get("guild_id", "")
        group_id = event.get("group_openid", event.get("group_id", ""))

        # Determine chat type
        if event_type in ("DIRECT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"):
            chat_type = "direct"
        elif event_type in ("GROUP_AT_MESSAGE_CREATE",):
            chat_type = "group"
        elif guild_id:
            chat_type = "guild"
        else:
            chat_type = "direct"

        # Strip @mentions
        text = re.sub(r"<@!\d+>", "", content).strip()
        if not text:
            return None

        # Determine user and chat IDs
        user_id = author.get("id", "unknown")
        if chat_type == "guild":
            chat_id = f"{guild_id}:{channel_id}"
        elif chat_type == "group":
            chat_id = group_id or user_id
        else:
            chat_id = user_id

        return {
            "msg_id": msg_id,
            "user_id": user_id,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "text": text,
            "raw": event,
        }

    # ═══════════════════════════════════════════════════════════════
    # Session & Dedup
    # ═══════════════════════════════════════════════════════════════

    def _session_id_for(self, message: dict) -> str:
        chat = str(message.get("chat_id") or message.get("user_id") or "default")
        safe_chat = re.sub(r"[^a-zA-Z0-9_.:-]", "_", chat)
        return f"qq_{safe_chat}"

    async def _is_duplicate_event(self, message: dict) -> bool:
        msg_id = message.get("msg_id")
        if not msg_id:
            return False
        try:
            key = f"qq_event_seen:{msg_id}"
            inserted = await self._agent.ctx.redis.set(key, "1", ex=300, nx=True)
            return inserted is False
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════════
    # Slash Commands
    # ═══════════════════════════════════════════════════════════════

    async def _handle_slash_command(self, message: dict, text: str) -> Optional[str]:
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        session_id = self._session_id_for(message)

        if cmd == "/help":
            return self._t("help_text")
        elif cmd == "/status":
            return self._t("status_text")
        elif cmd == "/clear":
            try:
                await self._agent.ctx.clear_history(session_id)
            except Exception:
                pass
            return self._t("context_cleared")
        elif cmd == "/retry":
            last_text = self._last_failed_request.get(session_id, "")
            if not last_text:
                return self._t("no_retry")
            message["text"] = last_text
            asyncio.create_task(self._process_and_reply(message))
            return ""
        elif cmd == "/search":
            if not arg:
                return self._t("unknown_command", cmd="/search").replace("/search", "/search <关键词>")
            search_prompt = f"请在知识库中搜索以下内容，用中文回复：{arg}"
            answer = await self._agent.chat(session_id, search_prompt, domain="research")
            return answer
        elif cmd == "/skills":
            skills = getattr(self._agent, "loaded_skills", {}) or {}
            if not skills:
                return "当前没有可用技能。"
            lines = ["可用技能："]
            for name in sorted(skills.keys())[:20]:
                lines.append(f"- {name}")
            return "\n".join(lines)
        elif cmd in ("/approve", "/deny"):
            return None  # handled by confirmation flow
        return self._t("unknown_command", cmd=cmd)

    # ═══════════════════════════════════════════════════════════════
    # Confirmation Flow
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_confirmation_action(text: str) -> Optional[tuple]:
        normalized = (text or "").strip()
        if not normalized:
            return None
        parts = normalized.split()
        command = parts[0].lower()
        request_id = parts[1] if len(parts) > 1 else None
        if command in APPROVE_WORDS:
            return ("approve", request_id)
        if command in DENY_WORDS:
            return ("deny", request_id)
        return None

    async def _apply_confirmation_action(self, message: dict, action_info: tuple) -> dict:
        action, request_id = action_info
        session_id = self._session_id_for(message)
        return await self._apply_confirmation_action_for_session(session_id, action, request_id)

    async def _apply_confirmation_action_for_session(
        self, session_id: str, action: str, request_id: Optional[str] = None
    ) -> dict:
        pending = await self._find_pending_confirmation(session_id, request_id)
        if not pending:
            return {"status": "not_found", "message": "没有找到待处理的确认请求。"}
        key, data = pending
        active_turn = await self._agent._get_active_chat_turn(session_id)
        request_turn = data.get("turn_id")
        if action == "approve" and request_turn and request_turn != active_turn:
            await self._agent._mark_confirmation_denied(key, data, "stale_confirmation_request")
            return {"status": "stale", "message": "确认请求已过期，请重新发起任务。"}
        data["status"] = "approved" if action == "approve" else "denied"
        await self._agent.ctx.redis.set(key, json.dumps(data, ensure_ascii=False), ex=60)
        return {
            "status": "success",
            "message": "已允许执行。" if action == "approve" else "已拒绝执行。",
            "request_id": data.get("request_id"),
        }

    async def _find_pending_confirmation(self, session_id: str, request_id: Optional[str] = None):
        try:
            keys = await self._agent.ctx.redis.keys(f"confirm_req:{session_id}:*")
        except Exception:
            return None
        for key in keys:
            data = await self._agent.confirmation_state.get_request(key)
            if not data or data.get("status") != "pending":
                continue
            if request_id and data.get("request_id") != request_id:
                continue
            return key, data
        return None

    # ═══════════════════════════════════════════════════════════════
    # Core Processing
    # ═══════════════════════════════════════════════════════════════

    async def _process_and_reply(self, message: dict) -> None:
        session_id = self._session_id_for(message)
        chat_task = asyncio.create_task(
            self._agent.chat(session_id, message.get("text", ""), domain="auto")
        )
        watcher_task = asyncio.create_task(
            self._watch_confirmations(message, session_id, chat_task)
        )
        try:
            answer = await chat_task
            await self._deliver_text(message, answer)
        except Exception as e:
            self.log.error(f"QQ adapter chat failed: {e}", exc_info=True)
            await self._deliver_text(message, f"处理失败：{e}")
        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

    async def _watch_confirmations(
        self, message: dict, session_id: str, chat_task: asyncio.Task
    ) -> None:
        notified = set()
        deadline = time.time() + max(5, self.config.confirmation_watch_sec)
        while not chat_task.done() and time.time() < deadline:
            pending = await self._find_pending_confirmation(session_id)
            if pending:
                _, data = pending
                request_id = data.get("request_id")
                if request_id and request_id not in notified:
                    notified.add(request_id)
                    prompt = data.get("prompt", "")
                    await self._deliver_confirmation(message, session_id, request_id, prompt)
            await asyncio.sleep(0.8)

    # ═══════════════════════════════════════════════════════════════
    # Message Delivery
    # ═══════════════════════════════════════════════════════════════

    async def _deliver_text(self, message: dict, text: str) -> None:
        """Send a text message to the user via QQ HTTP API."""
        if not self.config.reply_enabled or not self.config.app_id:
            self.log.info(f"[QQ dry-run reply to {message.get('chat_id')}]: {text[:200]}")
            return

        target_id = message.get("user_id", "")
        channel_id = message.get("channel_id", "")
        chat_type = message.get("chat_type", "direct")

        # QQ supports sending to: direct (user openid), group (group openid),
        # or channel (channel_id within a guild)
        for chunk in self._split_text(text, limit=1900):
            await self._send_qq_message(target_id, channel_id, chat_type, chunk)

    async def _deliver_confirmation(
        self, message: dict, session_id: str, request_id: str, prompt: str
    ) -> None:
        """Send a text-based confirmation prompt."""
        text = (
            f"【需要确认后继续执行】\n"
            f"请求 ID：{request_id}\n"
            f"{prompt}\n\n"
            f"回复 /approve {request_id} 允许执行\n"
            f"回复 /deny {request_id} 拒绝执行"
        )
        await self._deliver_text(message, text)

    async def _send_qq_message(
        self, target_id: str, channel_id: str, chat_type: str, text: str
    ) -> None:
        """Low-level QQ HTTP API call to send a message."""
        token = await self._get_access_token()

        # Determine the correct API endpoint based on chat type
        if chat_type == "guild" and channel_id:
            # Guild channel message
            url = f"{self.config.api_base}/channels/{channel_id}/messages"
        elif chat_type == "group":
            # Group message
            url = f"{self.config.api_base}/v2/groups/{target_id}/messages"
        else:
            # Direct message (C2C)
            url = f"{self.config.api_base}/v2/users/{target_id}/messages"

        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
        }
        body = {
            "content": text,
            "msg_type": 0,  # 0 = text
            "msg_id": hashlib.md5(f"{target_id}:{time.time()}".encode()).hexdigest()[:16],
        }

        for attempt in range(1, self.config.max_retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, headers=headers, json=body) as resp:
                        data = await resp.json(content_type=None)
                if data.get("code") == 0 or data.get("id"):
                    return  # success
                err_code = data.get("code", -1)
                err_msg = data.get("message", "unknown error")
                if err_code in (304023, 304024, 50001):  # rate limit, retry
                    if attempt < self.config.max_retries:
                        delay = self.config.retry_delay_sec * (2 ** (attempt - 1))
                        self.log.warning(
                            f"QQ message send rate-limited (code={err_code}), "
                            f"retrying in {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                raise RuntimeError(f"QQ send message failed (code={err_code}): {err_msg}")
            except RuntimeError:
                raise
            except Exception as e:
                if attempt < self.config.max_retries:
                    delay = self.config.retry_delay_sec * (2 ** (attempt - 1))
                    self.log.warning(f"QQ delivery attempt {attempt} failed: {e}")
                    await asyncio.sleep(delay)
                else:
                    raise RuntimeError(f"QQ send message failed after {self.config.max_retries} attempts: {e}")

    # ═══════════════════════════════════════════════════════════════
    # Auth
    # ═══════════════════════════════════════════════════════════════

    async def _get_access_token(self) -> str:
        """Get or refresh QQ bot access token."""
        if self._access_token and time.time() < self._access_token_expire_at - 60:
            return self._access_token

        url = f"{self.config.api_base}/app/getAppAccessToken"
        body = {
            "appId": self.config.app_id,
            "clientSecret": self.config.app_secret,
        }
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body) as resp:
                data = await resp.json(content_type=None)

        err_code = data.get("code", data.get("ret", -1))
        if err_code != 0:
            raise RuntimeError(
                f"QQ access_token failed (code={err_code}): {data.get('msg', data.get('message', 'unknown'))}"
            )
        self._access_token = data.get("access_token", data.get("data", {}).get("access_token", ""))
        self._access_token_expire_at = time.time() + int(
            data.get("expires_in", data.get("data", {}).get("expires_in", 7200))
        )
        return self._access_token

    # ═══════════════════════════════════════════════════════════════
    # Utilities
    # ═══════════════════════════════════════════════════════════════

    def _t(self, key: str, **kwargs) -> str:
        """Get i18n string for current language."""
        lang = self.config.lang if self.config.lang in ("zh", "en") else "zh"
        entry = I18N.get(key, {})
        if isinstance(entry, dict):
            text = entry.get(lang, entry.get("zh", key))
        else:
            text = str(entry)
        if kwargs:
            text = text.format(**kwargs)
        return text

    @staticmethod
    def _split_text(text: str, limit: int = 1900) -> list[str]:
        """Split long text for QQ message limits (~2000 bytes)."""
        text = text or ""
        if len(text) <= limit:
            return [text]
        chunks = []
        current = []
        current_len = 0
        for line in text.splitlines(True):
            if current_len + len(line) > limit and current:
                chunks.append("".join(current))
                current = []
                current_len = 0
            if len(line) > limit:
                for start in range(0, len(line), limit):
                    chunks.append(line[start:start + limit])
                continue
            current.append(line)
            current_len += len(line)
        if current:
            chunks.append("".join(current))
        return chunks
