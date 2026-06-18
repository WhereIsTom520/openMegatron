"""Telegram bot adapter for OpenMegatron.

Feature parity with feishu_bot.py:
  - Webhook callback handling (no long-polling — Telegram recommends webhooks)
  - Text + image + file + audio + voice + video + sticker + document handling
  - Slash commands via Telegram bot command menu (/help, /status, etc.)
  - Rich inline keyboard for confirmations (approve/deny buttons)
  - Message delivery with retry + exponential backoff
  - Delivery status tracking
  - Proactive notification (sendMessage to any chat_id)
  - Group chat @mention detection (via /botname or reply)
  - i18n (zh/en)
  - Session isolation: telegram_{user_id} (DM) or telegram_{chat_id} (group)

Telegram Bot API docs: https://core.telegram.org/bots/api
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp

try:
    from fastapi import HTTPException
except Exception:
    HTTPException = None

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

APPROVE_WORDS = {"/approve", "approve", "同意", "允许", "确认", "y", "yes"}
DENY_WORDS = {"/deny", "deny", "拒绝", "不同意", "取消", "n", "no"}

# Telegram bot commands (registered via setMyCommands API)
BOT_COMMANDS = [
    {"command": "help", "description": "Show help"},
    {"command": "status", "description": "Check system status"},
    {"command": "clear", "description": "Clear conversation context"},
    {"command": "retry", "description": "Retry last failed request"},
    {"command": "search", "description": "Search knowledge base"},
    {"command": "skills", "description": "List available skills"},
]

I18N = {
    "confirm_title": {"zh": "需要确认后继续执行", "en": "Confirmation Required"},
    "confirm_approve": {"zh": "允许", "en": "Approve"},
    "confirm_deny": {"zh": "拒绝", "en": "Deny"},
    "confirm_hint": {"zh": "也可以回复 /approve {id} 或 /deny {id}", "en": "Or reply /approve {id} or /deny {id}"},
    "processing": {"zh": "正在处理...", "en": "Processing..."},
    "error_generic": {"zh": "处理失败：{error}", "en": "Processing failed: {error}"},
    "help_text": {
        "zh": (
            "可用命令：\n"
            "/help - 帮助\n"
            "/status - 系统状态\n"
            "/clear - 清除上下文\n"
            "/retry - 重试上次请求\n"
            "/search <关键词> - 搜索知识库\n"
            "/skills - 技能列表"
        ),
        "en": (
            "Commands:\n"
            "/help - Help\n"
            "/status - System status\n"
            "/clear - Clear context\n"
            "/retry - Retry last request\n"
            "/search <query> - Search knowledge base\n"
            "/skills - List skills"
        ),
    },
    "status_text": {"zh": "系统运行正常", "en": "System operational"},
    "context_cleared": {"zh": "对话上下文已清除", "en": "Conversation context cleared"},
    "no_retry": {"zh": "没有可重试的请求", "en": "No request to retry"},
    "unknown_command": {"zh": "未知命令：{cmd}。输入 /help 查看可用命令。", "en": "Unknown command: {cmd}. Type /help for commands."},
    "delivery_retry": {"zh": "消息发送失败，第 {n} 次重试中...", "en": "Delivery failed, retry {n}..."},
    "unsupported_message": {"zh": "收到 {type} 消息，暂不支持自动处理。", "en": "Received {type} message, not yet supported."},
    "image_received": {"zh": "[用户发送了一张图片]", "en": "[User sent an image]"},
    "file_received": {"zh": "[用户发送了文件: {name}]", "en": "[User sent a file: {name}]"},
    "audio_received": {"zh": "[用户发送了一条语音消息]", "en": "[User sent a voice message]"},
    "video_received": {"zh": "[用户发送了一个视频]", "en": "[User sent a video]"},
    "sticker_received": {"zh": "[用户发送了一个贴纸: {emoji}]", "en": "[User sent a sticker: {emoji}]"},
    "location_received": {"zh": "[用户发送了位置: {lat}, {lon}]", "en": "[User shared location: {lat}, {lon}]"},
}


# ═══════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════

@dataclass
class TelegramConfig:
    bot_token: str = ""
    api_base: str = "https://api.telegram.org"
    webhook_secret: str = ""              # random string for webhook path security
    request_timeout_sec: int = 20
    reply_enabled: bool = True
    confirmation_watch_sec: int = 65
    max_retries: int = 3
    retry_delay_sec: float = 1.0
    enable_slash_commands: bool = True
    lang: str = "zh"

    @classmethod
    def from_config(cls, config: dict) -> "TelegramConfig":
        integrations = config.get("integrations", {}) if isinstance(config, dict) else {}
        tg_cfg = integrations.get("telegram", {}) or config.get("telegram", {}) or {}

        def env_or_cfg(env_name: str, key: str, default: Any = ""):
            value = os.environ.get(env_name)
            if value is not None:
                return value
            return tg_cfg.get(key, default)

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
            bot_token=str(env_or_cfg("TELEGRAM_BOT_TOKEN", "bot_token", "")),
            api_base=str(env_or_cfg("TELEGRAM_API_BASE", "api_base", "https://api.telegram.org")).rstrip("/"),
            webhook_secret=str(env_or_cfg("TELEGRAM_WEBHOOK_SECRET", "webhook_secret", "")),
            request_timeout_sec=to_int(env_or_cfg("TELEGRAM_REQUEST_TIMEOUT_SEC", "request_timeout_sec", 20), 20),
            reply_enabled=to_bool(env_or_cfg("TELEGRAM_REPLY_ENABLED", "reply_enabled", True)),
            confirmation_watch_sec=to_int(env_or_cfg("TELEGRAM_CONFIRMATION_WATCH_SEC", "confirmation_watch_sec", 65), 65),
            max_retries=to_int(env_or_cfg("TELEGRAM_MAX_RETRIES", "max_retries", 3), 3),
            retry_delay_sec=float(env_or_cfg("TELEGRAM_RETRY_DELAY_SEC", "retry_delay_sec", 1.0)),
            enable_slash_commands=to_bool(env_or_cfg("TELEGRAM_SLASH_COMMANDS", "enable_slash_commands", True)),
            lang=str(env_or_cfg("TELEGRAM_LANG", "lang", "zh")).lower()[:2],
        )


# ═══════════════════════════════════════════════════════════════
# Delivery Status
# ═══════════════════════════════════════════════════════════════

@dataclass
class DeliveryStatus:
    message_id: str = ""
    chat_id: str = ""
    status: str = "pending"
    attempts: int = 0
    last_error: str = ""
    created_at: float = field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════
# Telegram Bot Adapter
# ═══════════════════════════════════════════════════════════════

class TelegramBotAdapter:
    name = "telegram"

    def __init__(self, config: TelegramConfig, logger=None):
        self.config = config
        self.logger = logger
        self._delivery_statuses: dict[str, DeliveryStatus] = {}
        self._last_failed_request: dict[str, str] = {}
        self._bot_info: dict = {}  # cached getMe() result

    # ═══════════════════════════════════════════════════════════════
    # Webhook Setup
    # ═══════════════════════════════════════════════════════════════

    def webhook_path(self) -> str:
        """Generate the webhook callback path."""
        secret = self.config.webhook_secret or hashlib.sha256(
            self.config.bot_token.encode()
        ).hexdigest()[:16]
        return f"/integrations/telegram/webhook/{secret}"

    async def set_webhook(self, base_url: str) -> dict:
        """Register the webhook URL with Telegram. Call once on startup."""
        url = f"{base_url.rstrip('/')}{self.webhook_path()}"
        return await self._api_call("setWebhook", {
            "url": url,
            "allowed_updates": ["message", "edited_message", "callback_query"],
            "drop_pending_updates": False,
        })

    async def delete_webhook(self) -> dict:
        """Remove webhook (to switch back to getUpdates)."""
        return await self._api_call("deleteWebhook", {"drop_pending_updates": False})

    async def get_webhook_info(self) -> dict:
        return await self._api_call("getWebhookInfo")

    async def set_my_commands(self) -> dict:
        """Register bot commands for the menu."""
        if not self.config.enable_slash_commands:
            return {"ok": True, "description": "slash commands disabled"}
        lang = self.config.lang
        commands = []
        for cmd in BOT_COMMANDS:
            commands.append({
                "command": cmd["command"],
                "description": cmd["description"],
            })
        return await self._api_call("setMyCommands", {"commands": commands})

    async def get_me(self) -> dict:
        """Get bot info. Cached after first call."""
        if self._bot_info:
            return self._bot_info
        result = await self._api_call("getMe")
        if result.get("ok") and result.get("result"):
            self._bot_info = result["result"]
        return self._bot_info

    # ═══════════════════════════════════════════════════════════════
    # Callback Handling
    # ═══════════════════════════════════════════════════════════════

    async def handle_callback(self, request, agent, background_tasks):
        """Process incoming Telegram webhook update."""
        try:
            payload = await request.json()
        except Exception:
            return self.forbidden("invalid json")

        # Telegram sends an Update object
        # https://core.telegram.org/bots/api#update

        # ── Callback query (inline button click) ──
        callback_query = payload.get("callback_query")
        if callback_query:
            return await self._handle_callback_query(agent, callback_query)

        # ── Message (text, image, file, etc.) ──
        message_data = payload.get("message") or payload.get("edited_message")
        if not message_data:
            return {"status": "ignored"}

        message = self.parse_event(message_data)
        if not message:
            return {"status": "ignored"}

        if await self.is_duplicate_event(agent, message):
            return {"status": "duplicate_ignored"}

        # ── Text-based confirmation ──
        confirm_action = self.parse_confirmation_action(message.get("text", ""))
        if confirm_action:
            result = await self.apply_confirmation_action(agent, message, confirm_action)
            if result.get("status") == "success":
                await self.deliver(message, result.get("message", "已处理确认请求。"))
            return result

        # ── Slash command handling ──
        text = message.get("text", "")
        if self.config.enable_slash_commands and text.startswith("/"):
            slash_result = await self.handle_slash_command(agent, message, text)
            if slash_result:
                await self.deliver(message, slash_result)
                return {"status": "slash_command_handled"}

        # ── Save for retry ──
        session_id = self.session_id_for(message)
        self._last_failed_request[session_id] = text

        # ── Non-text message handling ──
        msg_type = message.get("message_type", "text")
        if msg_type != "text":
            await self.handle_non_text_message(agent, message)
            return {"status": "processing_non_text"}

        # ── Send typing indicator ──
        chat_id = message.get("chat_id", "")
        background_tasks.add_task(self._send_chat_action, chat_id, "typing")

        # ── Process ──
        background_tasks.add_task(self.process_and_reply, agent, message)
        return {"status": "processing"}

    async def _handle_callback_query(self, agent, callback_query: dict) -> dict:
        """Handle inline keyboard button clicks."""
        query_id = callback_query.get("id", "")
        data = callback_query.get("data", "")
        msg = callback_query.get("message", {})

        try:
            parsed = json.loads(data) if data else {}
        except json.JSONDecodeError:
            parsed = {}

        action = parsed.get("action", "")
        session_id = parsed.get("session_id", "")
        request_id = parsed.get("request_id", "")

        if action in ("approve", "deny") and session_id:
            result = await self.apply_confirmation_action_for_session(
                agent, session_id, action, request_id,
            )
            # Answer the callback query (required by Telegram)
            await self._api_call("answerCallbackQuery", {
                "callback_query_id": query_id,
                "text": result.get("message", "已处理"),
                "show_alert": False,
            })
            # Edit the original message to remove buttons
            if msg.get("chat", {}).get("id") and msg.get("message_id"):
                await self._api_call("editMessageReplyMarkup", {
                    "chat_id": msg["chat"]["id"],
                    "message_id": msg["message_id"],
                    "reply_markup": None,
                })
            return {"status": "success"}

        # Unknown callback
        await self._api_call("answerCallbackQuery", {
            "callback_query_id": query_id,
            "text": "未知操作",
            "show_alert": False,
        })
        return {"status": "ignored"}

    # ═══════════════════════════════════════════════════════════════
    # Event Parsing
    # ═══════════════════════════════════════════════════════════════

    def parse_event(self, msg: dict) -> Optional[dict]:
        """Parse a Telegram Message object into our normalized format."""
        chat = msg.get("chat") or {}
        from_user = msg.get("from") or {}
        chat_id = str(chat.get("id", ""))
        user_id = str(from_user.get("id", chat_id))
        user_name = (
            from_user.get("username")
            or f"{from_user.get('first_name', '')} {from_user.get('last_name', '')}".strip()
            or user_id
        )
        chat_type = chat.get("type", "private")  # "private" | "group" | "supergroup" | "channel"

        # Determine message type and extract text
        msg_type = "text"
        text = ""
        raw_content = {}

        if "text" in msg:
            msg_type = "text"
            text = msg.get("text", "")
        elif "photo" in msg:
            msg_type = "image"
            photos = msg.get("photo", [])
            raw_content = {"photo": photos, "caption": msg.get("caption", "")}
            text = msg.get("caption", "")
        elif "document" in msg:
            msg_type = "file"
            doc = msg.get("document", {})
            raw_content = {"document": doc, "caption": msg.get("caption", "")}
            text = msg.get("caption", "")
        elif "audio" in msg:
            msg_type = "audio"
            audio = msg.get("audio", {})
            raw_content = {"audio": audio}
        elif "voice" in msg:
            msg_type = "audio"
            voice = msg.get("voice", {})
            raw_content = {"voice": voice}
        elif "video" in msg:
            msg_type = "video"
            video = msg.get("video", {})
            raw_content = {"video": video, "caption": msg.get("caption", "")}
            text = msg.get("caption", "")
        elif "sticker" in msg:
            msg_type = "sticker"
            sticker = msg.get("sticker", {})
            raw_content = {"sticker": sticker}
        elif "location" in msg:
            msg_type = "location"
            loc = msg.get("location", {})
            raw_content = {"location": loc}
        elif "contact" in msg:
            msg_type = "contact"
            raw_content = {"contact": msg.get("contact", {})}
        else:
            return None  # unsupported

        # Check if bot was mentioned (group chats)
        is_mentioned = False
        if chat_type in ("group", "supergroup"):
            entities = msg.get("entities", [])
            for ent in entities:
                if ent.get("type") == "mention":
                    mention_text = text[ent.get("offset", 0):ent.get("offset", 0) + ent.get("length", 0)]
                    # Check if it mentions our bot
                    bot_username = self._bot_info.get("username", "")
                    if bot_username and bot_username.lower() in mention_text.lower():
                        is_mentioned = True
                        break
            # Also check if message starts with /command@botname
            if not is_mentioned and text.startswith("/"):
                bot_username = self._bot_info.get("username", "")
                if f"@{bot_username}" in text.split()[0] if bot_username else False:
                    is_mentioned = True

        return {
            "event_id": str(msg.get("message_id", "")),
            "tenant_key": "",
            "user_id": user_id,
            "user_name": user_name,
            "chat_id": chat_id,
            "message_id": str(msg.get("message_id", "")),
            "chat_type": chat_type,
            "message_type": msg_type,
            "text": text,
            "raw_content": raw_content,
            "raw": msg,
            "is_group": chat_type in ("group", "supergroup"),
            "is_mentioned": is_mentioned,
        }

    @staticmethod
    def parse_confirmation_action(text: str) -> Optional[tuple]:
        normalized = (text or "").strip()
        if not normalized:
            return None
        parts = normalized.split()
        command = parts[0].lower().lstrip("/")
        request_id = parts[1] if len(parts) > 1 else None
        if command in APPROVE_WORDS:
            return ("approve", request_id)
        if command in DENY_WORDS:
            return ("deny", request_id)
        return None

    # ═══════════════════════════════════════════════════════════════
    # Non-Text Message Handling
    # ═══════════════════════════════════════════════════════════════

    async def handle_non_text_message(self, agent, message: dict):
        msg_type = message.get("message_type", "text")
        raw = message.get("raw_content", {})

        if msg_type == "image":
            # Get the largest photo
            photos = raw.get("photo", [])
            if photos:
                file_id = photos[-1].get("file_id", "")
                caption = raw.get("caption", "")
                text = self.t("image_received")
                if caption:
                    text = f"{text}\n[说明: {caption}]"
                # Try to get file URL for agent context
                file_info = await self._api_call("getFile", {"file_id": file_id})
                if file_info.get("ok"):
                    file_path = file_info.get("result", {}).get("file_path", "")
                    if file_path:
                        file_url = f"https://api.telegram.org/file/bot{self.config.bot_token}/{file_path}"
                        text = f"{text}\n[图片链接: {file_url}]"
                message["text"] = text
                await self.process_and_reply(agent, message)
            else:
                await self.deliver(message, self.t("unsupported_message", type="image"))

        elif msg_type == "file":
            doc = raw.get("document", {})
            file_name = doc.get("file_name", "unknown")
            caption = raw.get("caption", "")
            text = self.t("file_received", name=file_name)
            if caption:
                text = f"{text}\n[说明: {caption}]"
            message["text"] = text
            await self.process_and_reply(agent, message)

        elif msg_type == "audio":
            voice = raw.get("voice", {})
            if voice:
                text = self.t("audio_received")
                message["text"] = text
                await self.process_and_reply(agent, message)
            else:
                await self.deliver(message, self.t("unsupported_message", type="audio"))

        elif msg_type == "video":
            caption = raw.get("caption", "")
            text = self.t("video_received")
            if caption:
                text = f"{text}\n[说明: {caption}]"
            message["text"] = text
            await self.process_and_reply(agent, message)

        elif msg_type == "sticker":
            sticker = raw.get("sticker", {})
            emoji = sticker.get("emoji", "?")
            await self.deliver(message, self.t("sticker_received", emoji=emoji))

        elif msg_type == "location":
            loc = raw.get("location", {})
            lat = loc.get("latitude", 0)
            lon = loc.get("longitude", 0)
            message["text"] = self.t("location_received", lat=lat, lon=lon)
            await self.process_and_reply(agent, message)

        else:
            await self.deliver(message, self.t("unsupported_message", type=msg_type))

    # ═══════════════════════════════════════════════════════════════
    # Slash Commands
    # ═══════════════════════════════════════════════════════════════

    async def handle_slash_command(self, agent, message: dict, text: str) -> Optional[str]:
        # Strip bot mention suffix: /help@MyBot -> /help
        cmd_text = text.strip()
        if "@" in cmd_text.split()[0]:
            cmd_text = cmd_text.split()[0].split("@")[0] + " " + " ".join(cmd_text.split()[1:])

        parts = cmd_text.split(maxsplit=1)
        cmd = parts[0].lower().lstrip("/")
        arg = parts[1] if len(parts) > 1 else ""
        session_id = self.session_id_for(message)

        if cmd == "help":
            return self.t("help_text")
        elif cmd == "status":
            return await self._cmd_status(agent)
        elif cmd == "clear":
            await self._cmd_clear(agent, session_id)
            return self.t("context_cleared")
        elif cmd == "retry":
            return await self._cmd_retry(agent, message, session_id)
        elif cmd == "search":
            if not arg:
                return self.t("unknown_command", cmd="/search").replace("/search", "/search <关键词>")
            return await self._cmd_search(agent, session_id, arg)
        elif cmd == "skills":
            return await self._cmd_skills(agent)
        elif cmd in ("approve", "deny"):
            return None  # handled by confirmation flow
        elif cmd == "start":
            return self.t("help_text")
        return self.t("unknown_command", cmd=f"/{cmd}")

    async def _cmd_status(self, agent) -> str:
        try:
            status = await agent.ctx.check_redis()
            redis_ok = status.get("redis", {}).get("ok", False)
            return f"System status: Redis {'OK' if redis_ok else 'DOWN'}"
        except Exception:
            return "System status: unavailable"

    async def _cmd_clear(self, agent, session_id: str):
        try:
            await agent.ctx.clear_history(session_id)
        except Exception:
            pass

    async def _cmd_retry(self, agent, message: dict, session_id: str) -> str:
        last_text = self._last_failed_request.get(session_id, "")
        if not last_text:
            return self.t("no_retry")
        message["text"] = last_text
        await self.process_and_reply(agent, message)
        return ""

    async def _cmd_search(self, agent, session_id: str, query: str) -> str:
        try:
            search_prompt = f"请在知识库中搜索以下内容：{query}"
            answer = await agent.chat(session_id, search_prompt, domain="research")
            return answer
        except Exception as e:
            return self.t("error_generic", error=str(e))

    async def _cmd_skills(self, agent) -> str:
        try:
            skills = getattr(agent, "loaded_skills", {}) or {}
            if not skills:
                return "No skills available."
            lines = ["Available skills:"]
            for name in sorted(skills.keys())[:20]:
                lines.append(f"- {name}")
            return "\n".join(lines)
        except Exception:
            return "Unable to list skills."

    # ═══════════════════════════════════════════════════════════════
    # Session & Dedup
    # ═══════════════════════════════════════════════════════════════

    def session_id_for(self, message: dict) -> str:
        chat_id = str(message.get("chat_id") or message.get("user_id") or "default")
        chat_type = message.get("chat_type", "private")
        # For group chats, use chat_id (shared session)
        # For private chats, use user_id (personal session)
        if chat_type in ("group", "supergroup"):
            safe = re.sub(r"[^a-zA-Z0-9_.:-]", "_", chat_id)
            return f"telegram_group_{safe}"
        user_id = str(message.get("user_id") or chat_id)
        safe = re.sub(r"[^a-zA-Z0-9_.:-]", "_", user_id)
        return f"telegram_{safe}"

    async def is_duplicate_event(self, agent, message: dict) -> bool:
        event_id = message.get("event_id")
        if not event_id:
            return False
        try:
            key = f"telegram_event_seen:{event_id}"
            inserted = await agent.ctx.redis.set(key, "1", ex=300, nx=True)
            return inserted is False
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════════
    # Confirmation Flow
    # ═══════════════════════════════════════════════════════════════

    async def apply_confirmation_action(self, agent, message: dict, action_info: tuple) -> dict:
        action, request_id = action_info
        session_id = self.session_id_for(message)
        return await self.apply_confirmation_action_for_session(agent, session_id, action, request_id)

    async def apply_confirmation_action_for_session(self, agent, session_id: str, action: str,
                                                     request_id: Optional[str] = None) -> dict:
        pending = await self._find_pending_confirmation(agent, session_id, request_id)
        if not pending:
            return {"status": "not_found", "message": "No pending confirmation found."}
        key, data = pending
        active_turn = await agent._get_active_chat_turn(session_id)
        request_turn = data.get("turn_id")
        if action == "approve" and request_turn and request_turn != active_turn:
            await agent._mark_confirmation_denied(key, data, "stale_confirmation_request")
            return {"status": "stale", "message": "Confirmation expired. Please retry."}
        data["status"] = "approved" if action == "approve" else "denied"
        await agent.ctx.redis.set(key, json.dumps(data, ensure_ascii=False), ex=60)
        return {
            "status": "success",
            "message": "Approved." if action == "approve" else "Denied.",
            "request_id": data.get("request_id"),
        }

    async def _find_pending_confirmation(self, agent, session_id: str, request_id: str = None):
        try:
            keys = await agent.ctx.redis.keys(f"confirm_req:{session_id}:*")
        except Exception:
            return None
        for key in keys:
            data = await agent.confirmation_state.get_request(key)
            if not data or data.get("status") != "pending":
                continue
            if request_id and data.get("request_id") != request_id:
                continue
            return key, data
        return None

    # ═══════════════════════════════════════════════════════════════
    # Core Processing
    # ═══════════════════════════════════════════════════════════════

    async def process_and_reply(self, agent, message: dict):
        session_id = self.session_id_for(message)
        chat_task = asyncio.create_task(agent.chat(session_id, message.get("text", ""), domain="auto"))
        watcher_task = asyncio.create_task(self._watch_confirmations(agent, message, session_id, chat_task))
        try:
            answer = await chat_task
            # Use Markdown parse_mode for Telegram (supports bold, italic, code, links)
            await self.deliver(message, answer)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Telegram adapter chat failed: {e}", exc_info=True)
            await self.deliver(message, f"❌ Processing failed: {e}")
        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

    async def _watch_confirmations(self, agent, message: dict, session_id: str, chat_task: asyncio.Task):
        notified = set()
        deadline = time.time() + max(5, self.config.confirmation_watch_sec)
        while not chat_task.done() and time.time() < deadline:
            pending = await self._find_pending_confirmation(agent, session_id)
            if pending:
                _, data = pending
                request_id = data.get("request_id")
                if request_id and request_id not in notified:
                    notified.add(request_id)
                    await self.deliver_confirmation(
                        message, session_id=session_id,
                        request_id=request_id, prompt=data.get("prompt", ""),
                    )
            await asyncio.sleep(0.8)

    async def deliver_confirmation(self, message: dict, session_id: str, request_id: str, prompt: str):
        """Send confirmation with inline keyboard (approve/deny buttons)."""
        chat_id = message.get("chat_id", "")
        if not chat_id:
            return

        lang = self.config.lang if self.config.lang in ("zh", "en") else "zh"
        text = (
            f"🔐 *{I18N['confirm_title'][lang]}*\n\n"
            f"请求 ID: `{request_id}`\n"
            f"{prompt}\n\n"
            f"{I18N['confirm_hint'][lang].replace('{id}', request_id)}"
        )
        reply_markup = {
            "inline_keyboard": [[
                {
                    "text": f"✅ {I18N['confirm_approve'][lang]}",
                    "callback_data": json.dumps({"action": "approve", "session_id": session_id, "request_id": request_id}),
                },
                {
                    "text": f"❌ {I18N['confirm_deny'][lang]}",
                    "callback_data": json.dumps({"action": "deny", "session_id": session_id, "request_id": request_id}),
                },
            ]]
        }
        await self._send_message(chat_id, text, reply_markup=reply_markup)

    # ═══════════════════════════════════════════════════════════════
    # Message Delivery
    # ═══════════════════════════════════════════════════════════════

    async def deliver(self, message: dict, answer: str):
        """Send a text reply to the user."""
        chat_id = message.get("chat_id", "")
        if not chat_id:
            return
        for chunk in self._split_text(answer or "", limit=4000):
            await self._send_message(chat_id, chunk)

    async def send_proactive_message(self, chat_id: str, text: str):
        """Send a bot-initiated message to any chat."""
        for chunk in self._split_text(text, limit=4000):
            await self._send_message(chat_id, chunk)

    async def notify_task_complete(self, chat_id: str, task_name: str, result_summary: str):
        """Send a proactive notification when a background task completes."""
        text = f"✅ *Task Complete: {task_name}*\n\n{result_summary[:3000]}"
        await self.send_proactive_message(chat_id, text)

    async def _send_message(self, chat_id: str, text: str,
                            reply_markup: dict = None, parse_mode: str = "Markdown"):
        """Send a message via Telegram Bot API (sendMessage)."""
        if not self.config.reply_enabled or not self.config.bot_token:
            if self.logger:
                self.logger.info(f"[Telegram dry-run to {chat_id}]: {text[:200]}")
            return

        body: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            body["reply_markup"] = reply_markup

        await self._deliver_with_retry("sendMessage", body)

    async def _send_chat_action(self, chat_id: str, action: str = "typing"):
        """Send chat action (typing, upload_photo, etc.)."""
        if not self.config.reply_enabled or not self.config.bot_token:
            return
        try:
            await self._api_call("sendChatAction", {"chat_id": chat_id, "action": action})
        except Exception:
            pass

    async def _deliver_with_retry(self, method: str, body: dict):
        """Send with exponential backoff retry."""
        last_error = ""
        for attempt in range(1, self.config.max_retries + 1):
            try:
                result = await self._api_call(method, body)
                if result.get("ok"):
                    return
                last_error = result.get("description", "unknown error")
            except Exception as e:
                last_error = str(e)

            if self.logger:
                self.logger.warning(
                    f"Telegram delivery attempt {attempt}/{self.config.max_retries} failed: {last_error}"
                )
            if attempt < self.config.max_retries:
                delay = self.config.retry_delay_sec * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

        if self.logger:
            self.logger.error(f"Telegram delivery permanently failed: {last_error}")

    @staticmethod
    def _split_text(text: str, limit: int = 4000) -> list:
        """Split long text into Telegram-friendly chunks (<4096 bytes)."""
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

    # ═══════════════════════════════════════════════════════════════
    # Telegram API
    # ═══════════════════════════════════════════════════════════════

    async def _api_call(self, method: str, params: dict = None) -> dict:
        """Call a Telegram Bot API method."""
        url = f"{self.config.api_base}/bot{self.config.bot_token}/{method}"
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if params:
                async with session.post(url, json=params) as resp:
                    return await resp.json(content_type=None)
            else:
                async with session.get(url) as resp:
                    return await resp.json(content_type=None)

    # ═══════════════════════════════════════════════════════════════
    # i18n
    # ═══════════════════════════════════════════════════════════════

    def t(self, key: str, **kwargs) -> str:
        lang = self.config.lang if self.config.lang in ("zh", "en") else "zh"
        entry = I18N.get(key, {})
        if isinstance(entry, dict):
            text = entry.get(lang, entry.get("zh", key))
        else:
            text = str(entry)
        if kwargs:
            text = text.format(**kwargs)
        return text

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def forbidden(message: str):
        if HTTPException is not None:
            raise HTTPException(status_code=403, detail=message)
        return {"status": "error", "message": message}

    def is_configured(self) -> bool:
        return bool(self.config.bot_token)
