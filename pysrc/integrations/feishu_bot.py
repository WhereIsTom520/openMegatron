import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import aiohttp

try:
    from fastapi import HTTPException
except Exception:
    HTTPException = None

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:
    Cipher = None
    algorithms = None
    modes = None
    default_backend = None


# ── Constants ────────────────────────────────────────────────────────────────

APPROVE_WORDS = {"/approve", "approve", "同意", "允许", "确认", "y", "yes"}
DENY_WORDS = {"/deny", "deny", "拒绝", "不同意", "取消", "n", "no"}

# Slash commands the bot registers
SLASH_COMMANDS = [
    {"command": "/help", "description": "显示帮助信息", "description_en": "Show help"},
    {"command": "/status", "description": "查看系统状态", "description_en": "Check system status"},
    {"command": "/clear", "description": "清除当前对话上下文", "description_en": "Clear conversation context"},
    {"command": "/retry", "description": "重试上一次失败的请求", "description_en": "Retry last failed request"},
    {"command": "/search", "description": "在知识库中搜索", "description_en": "Search knowledge base"},
    {"command": "/skills", "description": "列出可用技能", "description_en": "List available skills"},
]

# Message type enum
class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    POST = "post"
    INTERACTIVE = "interactive"
    SHARE_CHAT = "share_chat"

# i18n strings
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
    "typing": {"zh": "正在输入...", "en": "Typing..."},
}


@dataclass
class FeishuConfig:
    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str = ""
    api_base: str = "https://open.feishu.cn"
    request_timeout_sec: int = 20
    reply_enabled: bool = True
    confirmation_watch_sec: int = 65
    max_retries: int = 3
    retry_delay_sec: float = 1.0
    enable_typing_indicator: bool = True
    enable_slash_commands: bool = True
    proactive_receive_id_type: str = "chat_id"
    lang: str = "zh"

    @classmethod
    def from_config(cls, config: dict) -> "FeishuConfig":
        integrations = config.get("integrations", {}) if isinstance(config, dict) else {}
        feishu_cfg = integrations.get("feishu", {}) or config.get("feishu", {}) or {}

        def env_or_cfg(env_name: str, key: str, default: Any = ""):
            value = os.environ.get(env_name)
            if value is not None:
                return value
            return feishu_cfg.get(key, default)

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
            app_id=str(env_or_cfg("FEISHU_APP_ID", "app_id", "")),
            app_secret=str(env_or_cfg("FEISHU_APP_SECRET", "app_secret", "")),
            verification_token=str(env_or_cfg("FEISHU_VERIFICATION_TOKEN", "verification_token", "")),
            encrypt_key=str(env_or_cfg("FEISHU_ENCRYPT_KEY", "encrypt_key", "")),
            api_base=str(env_or_cfg("FEISHU_API_BASE", "api_base", "https://open.feishu.cn")).rstrip("/"),
            request_timeout_sec=to_int(env_or_cfg("FEISHU_REQUEST_TIMEOUT_SEC", "request_timeout_sec", 20), 20),
            reply_enabled=to_bool(env_or_cfg("FEISHU_REPLY_ENABLED", "reply_enabled", True)),
            confirmation_watch_sec=to_int(env_or_cfg("FEISHU_CONFIRMATION_WATCH_SEC", "confirmation_watch_sec", 65), 65),
            max_retries=to_int(env_or_cfg("FEISHU_MAX_RETRIES", "max_retries", 3), 3),
            retry_delay_sec=float(env_or_cfg("FEISHU_RETRY_DELAY_SEC", "retry_delay_sec", 1.0)),
            enable_typing_indicator=to_bool(env_or_cfg("FEISHU_TYPING_INDICATOR", "enable_typing_indicator", True)),
            enable_slash_commands=to_bool(env_or_cfg("FEISHU_SLASH_COMMANDS", "enable_slash_commands", True)),
            proactive_receive_id_type=str(env_or_cfg("FEISHU_PROACTIVE_RECEIVE_ID_TYPE", "proactive_receive_id_type", "chat_id")),
            lang=str(env_or_cfg("FEISHU_LANG", "lang", "zh")).lower()[:2],
        )


@dataclass
class DeliveryStatus:
    """Track message delivery status for retry and observability."""
    message_id: str = ""
    target_message_id: str = ""
    msg_type: str = ""
    status: str = "pending"  # pending | delivered | failed | retrying
    attempts: int = 0
    last_error: str = ""
    created_at: float = field(default_factory=time.time)


class FeishuBotAdapter:
    name = "feishu"

    def __init__(self, config: FeishuConfig, logger=None):
        self.config = config
        self.logger = logger
        self._tenant_access_token: Optional[str] = None
        self._tenant_access_token_expire_at = 0.0
        self._delivery_statuses: dict[str, DeliveryStatus] = {}
        self._last_failed_request: dict[str, str] = {}  # session_id -> last user text
        self._retry_count: dict[str, int] = {}  # session_id -> retry count

    # ═══════════════════════════════════════════════════════════════
    # Callback Handling
    # ═══════════════════════════════════════════════════════════════

    async def handle_callback(self, request, agent, background_tasks):
        raw_body = await request.body()
        if not self.verify_signature(request.headers, raw_body):
            return self.forbidden("invalid signature")
        payload = self.decode_payload(raw_body)
        if not self.verify_token(payload):
            return self.forbidden("invalid token")
        if self.is_url_verification(payload):
            return {"challenge": payload.get("challenge", "")}

        # ── Card action (button click) ──
        card_action = self.parse_card_action(payload)
        if card_action:
            result = await self.apply_confirmation_action_for_session(
                agent, card_action["session_id"], card_action["action"], card_action.get("request_id"),
            )
            return {
                "status": result.get("status"),
                "toast": {
                    "type": "success" if result.get("status") == "success" else "warning",
                    "content": result.get("message", "确认请求已处理。"),
                },
            }

        # ── Parse incoming message ──
        message = self.parse_event(payload)
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

        # ── Typing indicator ──
        if self.config.enable_typing_indicator:
            background_tasks.add_task(self._send_typing_indicator, message)

        # ── Process ──
        background_tasks.add_task(self.process_and_reply, agent, message)
        return {"status": "processing"}

    # ═══════════════════════════════════════════════════════════════
    # Non-Text Message Handling (IMAGE, FILE, AUDIO, POST)
    # ═══════════════════════════════════════════════════════════════

    async def handle_non_text_message(self, agent, message: dict):
        """Handle image, file, audio, and post message types."""
        msg_type = message.get("message_type", "text")
        content = message.get("raw_content", {})

        if msg_type == "image":
            image_key = content.get("image_key", "")
            image_url = await self._get_image_url(image_key) if image_key else ""
            if image_url:
                # Forward image info to agent as text context
                text = f"[用户发送了一张图片: {image_url}]"
                message["text"] = text
                await self.process_and_reply(agent, message)
            else:
                await self.deliver(message, self.t("error_generic", error="无法获取图片"))

        elif msg_type == "file":
            file_key = content.get("file_key", "")
            file_name = content.get("file_name", "unknown_file")
            if file_key:
                text = f"[用户发送了文件: {file_name} (file_key: {file_key})]"
                message["text"] = text
                await self.process_and_reply(agent, message)
            else:
                await self.deliver(message, self.t("error_generic", error="无法获取文件"))

        elif msg_type == "audio":
            # Audio: transcribe via Feishu ASR if available, else note receipt
            text = "[用户发送了一条语音消息]"
            message["text"] = text
            await self.process_and_reply(agent, message)

        elif msg_type == "post":
            # Rich text post: extract text content
            post_content = content.get("content", "")
            if isinstance(post_content, list):
                text_parts = []
                for block in post_content:
                    for elem in block if isinstance(block, list) else [block]:
                        if isinstance(elem, dict) and elem.get("tag") == "text":
                            text_parts.append(elem.get("text", ""))
                text = " ".join(text_parts) if text_parts else "[用户发送了一条富文本消息]"
            elif isinstance(post_content, str):
                text = post_content
            else:
                text = "[用户发送了一条富文本消息]"
            message["text"] = text
            await self.process_and_reply(agent, message)

        else:
            await self.deliver(message, self.t("error_generic", error=f"不支持的消息类型: {msg_type}"))

    async def _get_image_url(self, image_key: str) -> str:
        """Get downloadable image URL from Feishu."""
        try:
            token = await self.tenant_access_token()
            url = f"{self.config.api_base}/open-apis/im/v1/images/{image_key}"
            headers = {"Authorization": f"Bearer {token}"}
            timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json(content_type=None)
            if data.get("code") == 0 and data.get("data", {}).get("image_key"):
                return str(data["data"].get("download_url", "")) or url
            return url
        except Exception:
            return ""

    async def _send_typing_indicator(self, message: dict):
        """Send typing indicator while agent is processing."""
        if not self.config.reply_enabled or not message.get("message_id"):
            return
        try:
            token = await self.tenant_access_token()
            # Feishu doesn't have a native typing indicator API, but we can send
            # a temporary "processing" card that gets updated later
            # For now, send a brief note
            pass
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    # Slash Commands
    # ═══════════════════════════════════════════════════════════════

    async def handle_slash_command(self, agent, message: dict, text: str) -> Optional[str]:
        """Handle built-in slash commands. Returns reply text or None if not a command."""
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        session_id = self.session_id_for(message)

        if cmd == "/help":
            return self.t("help_text")
        elif cmd == "/status":
            return await self._cmd_status(agent)
        elif cmd == "/clear":
            await self._cmd_clear(agent, session_id)
            return self.t("context_cleared")
        elif cmd == "/retry":
            return await self._cmd_retry(agent, message, session_id)
        elif cmd == "/search":
            if not arg:
                return self.t("unknown_command", cmd="/search").replace("/search", "/search <关键词>")
            return await self._cmd_search(agent, session_id, arg)
        elif cmd == "/skills":
            return await self._cmd_skills(agent)
        elif cmd == "/approve" or cmd == "/deny":
            return None  # handled by confirmation flow
        return self.t("unknown_command", cmd=cmd)

    async def _cmd_status(self, agent) -> str:
        try:
            status = await agent.ctx.check_redis()
            redis_ok = status.get("redis", {}).get("ok", False)
            return f"系统状态: Redis {'✓' if redis_ok else '✗'}"
        except Exception:
            return "系统状态: 无法获取"

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
            # Use agent's RAG search capability
            from rag_retrieval import classify_query, SearchStrategy
            strategy = classify_query(query)
            # For now, delegate to agent.chat with a search-focused prompt
            search_prompt = f"请在知识库中搜索以下内容，用中文回复：{query}"
            answer = await agent.chat(session_id, search_prompt, domain="research")
            return answer
        except Exception as e:
            return self.t("error_generic", error=str(e))

    async def _cmd_skills(self, agent) -> str:
        try:
            skills = getattr(agent, "loaded_skills", {}) or {}
            if not skills:
                return "当前没有可用技能。"
            lines = ["可用技能："]
            for name in sorted(skills.keys())[:20]:
                lines.append(f"- {name}")
            return "\n".join(lines)
        except Exception:
            return "无法获取技能列表。"

    # ═══════════════════════════════════════════════════════════════
    # Rich Card Messages
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def build_result_card(title: str, content: str, *, status: str = "info",
                          details: list[dict] = None) -> dict:
        """Build a rich result card for agent responses.

        status: 'success' (green), 'error' (red), 'warning' (yellow), 'info' (blue)
        details: list of {label, value} pairs
        """
        colors = {"success": "green", "error": "red", "warning": "yellow", "info": "blue"}
        color = colors.get(status, "blue")

        elements = [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": content[:8000]},
            },
        ]

        if details:
            detail_lines = []
            for d in details[:10]:
                label = d.get("label", "")
                value = d.get("value", "")
                detail_lines.append(f"**{label}**: {value}")
            if detail_lines:
                elements.insert(0, {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": "\n".join(detail_lines)},
                })

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color,
                "title": {"tag": "plain_text", "content": title[:100]},
            },
            "elements": elements,
        }

    @staticmethod
    def build_progress_card(title: str, message: str, progress_pct: int = 0) -> dict:
        """Build a progress card for long-running tasks."""
        bar_len = 20
        filled = int(bar_len * progress_pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title[:100]},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**进度**: {progress_pct}%\n{bar}\n\n{message}"},
                },
            ],
        }

    @staticmethod
    def build_error_card(title: str, error: str, suggestion: str = "") -> dict:
        """Build an error card with optional fix suggestion."""
        elements = [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"❌ **错误**: {error}"},
            },
        ]
        if suggestion:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"💡 **建议**: {suggestion}"},
            })

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red",
                "title": {"tag": "plain_text", "content": title[:100]},
            },
            "elements": elements,
        }

    @staticmethod
    def build_skills_card(skills: dict) -> dict:
        """Build a card listing available skills."""
        lines = []
        for name, info in sorted(skills.items())[:20]:
            desc = (info.get("description") or "")[:80]
            lines.append(f"**{name}**: {desc}")

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "可用技能"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines) if lines else "无可用技能"}},
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "输入 /skills 随时查看"}]},
            ],
        }

    # ═══════════════════════════════════════════════════════════════
    # Enhanced Message Delivery with Retry
    # ═══════════════════════════════════════════════════════════════

    async def deliver(self, message: dict, answer: str):
        """Deliver text response with automatic splitting."""
        await self.deliver_message(message, "text", {"text": answer or ""})

    async def deliver_card(self, message: dict, card: dict):
        """Deliver an interactive card message."""
        await self.deliver_message(message, "interactive", card)

    async def deliver_confirmation(self, message: dict, session_id: str, request_id: str, prompt: str):
        card = self.build_confirmation_card(session_id, request_id, prompt)
        if not self.config.reply_enabled or not self.config.app_id or not self.config.app_secret:
            fallback = (
                "需要你确认后才能继续执行。\n"
                f"请求 ID：{request_id}\n"
                f"{prompt}\n\n"
                f"回复 `/approve {request_id}` 允许，或 `/deny {request_id}` 拒绝。"
            )
            if self.logger:
                self.logger.info(f"[Feishu dry-run confirmation to {message.get('chat_id')}]: {fallback}")
            return
        await self.deliver_message(message, "interactive", card)

    async def deliver_message(self, message: dict, msg_type: str, content: Any):
        """Deliver a message with retry logic and status tracking."""
        if not self.config.reply_enabled or not self.config.app_id or not self.config.app_secret:
            if self.logger:
                self.logger.info(f"[Feishu dry-run reply to {message.get('chat_id')}]: {content}")
            return
        if not message.get("message_id"):
            if self.logger:
                self.logger.warning("Feishu reply skipped: missing message_id")
            return

        # Text: split and send
        if msg_type == "text":
            text = content.get("text", "")
            for chunk in self.split_text(text):
                await self._deliver_with_retry(message["message_id"], "text", {"text": chunk})
            return

        # Non-text: send directly
        await self._deliver_with_retry(message["message_id"], msg_type, content)

    async def _deliver_with_retry(self, message_id: str, msg_type: str, content: dict):
        """Send message with exponential backoff retry."""
        last_error = ""
        for attempt in range(1, self.config.max_retries + 1):
            try:
                await self.reply_to_message(message_id, msg_type, content)
                return  # success
            except Exception as e:
                last_error = str(e)
                if self.logger:
                    self.logger.warning(
                        f"Feishu delivery attempt {attempt}/{self.config.max_retries} failed: {e}"
                    )
                if attempt < self.config.max_retries:
                    delay = self.config.retry_delay_sec * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)

        # All retries exhausted
        delivery_id = hashlib.sha256(f"{message_id}:{time.time()}".encode()).hexdigest()[:12]
        self._delivery_statuses[delivery_id] = DeliveryStatus(
            message_id=delivery_id,
            target_message_id=message_id,
            msg_type=msg_type,
            status="failed",
            attempts=self.config.max_retries,
            last_error=last_error,
        )
        if self.logger:
            self.logger.error(f"Feishu delivery permanently failed after {self.config.max_retries} attempts: {last_error}")

    async def reply_to_message(self, message_id: str, msg_type: str, content: dict):
        """Low-level Feishu API call to reply to a message."""
        token = await self.tenant_access_token()
        url = f"{self.config.api_base}/open-apis/im/v1/messages/{message_id}/reply"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)}
        data = await self._post_json(url, headers, body)
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu reply failed (code={data.get('code')}): {data.get('msg', 'unknown')}")

    async def _post_json(self, url: str, headers: dict, body: dict) -> dict:
        """POST JSON to Feishu and return the decoded response."""
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                return await resp.json(content_type=None)

    # ═══════════════════════════════════════════════════════════════
    # Proactive Notification (bot-initiated message)
    # ═══════════════════════════════════════════════════════════════

    async def send_proactive_message(self, chat_id: str, msg_type: str, content: Any):
        """Send a bot-initiated message to a chat (not a reply).

        Requires the bot to have permission to send messages in the chat.
        """
        if not self.config.reply_enabled or not self.config.app_id:
            return
        token = await self.tenant_access_token()
        receive_id_type = self.config.proactive_receive_id_type or "chat_id"
        url = f"{self.config.api_base}/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {
            "receive_id": chat_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        data = await self._post_json(url, headers, body)
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu proactive message failed (code={data.get('code')}): {data.get('msg', 'unknown')}")
        return data

    async def notify_task_complete(self, chat_id: str, task_name: str, result_summary: str):
        """Send a proactive notification when a background task completes."""
        card = self.build_result_card(
            title=f"任务完成: {task_name}",
            content=result_summary[:3000],
            status="success",
        )
        await self.send_proactive_message(chat_id, "interactive", card)

    # ═══════════════════════════════════════════════════════════════
    # Group Chat @Mention Detection
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def is_bot_mentioned(message: dict, bot_open_id: str = "") -> bool:
        """Check if the bot was @mentioned in a group chat message.

        Feishu sends <at user_id="xxx">@bot_name</at> in the raw content.
        This checks if the raw content contains an @mention.
        """
        raw_content = message.get("raw_content", {})
        if isinstance(raw_content, str):
            try:
                raw_content = json.loads(raw_content)
            except Exception:
                raw_content = {}
        text = raw_content.get("text", "") if isinstance(raw_content, dict) else ""
        # Check for <at> tags in raw text
        has_at = bool(re.search(r"<at[^>]*>", text))
        # If bot_open_id is provided, check specifically for it
        if bot_open_id and has_at:
            return bot_open_id in text
        return has_at

    # ═══════════════════════════════════════════════════════════════
    # Event Parsing (extended for non-text messages)
    # ═══════════════════════════════════════════════════════════════

    def parse_event(self, payload: dict) -> Optional[dict]:
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        event_type = header.get("event_type") or payload.get("type")
        if event_type != "im.message.receive_v1":
            return None

        message = event.get("message") or {}
        msg_type = message.get("message_type", "text")
        content = self.parse_message_content(message.get("content"))

        # Extract text for all supported types
        text = ""
        if msg_type == "text":
            text = self.clean_message_text(content.get("text", ""))
        elif msg_type == "image":
            text = ""  # handled by handle_non_text_message
        elif msg_type == "file":
            text = ""  # handled by handle_non_text_message
        elif msg_type == "audio":
            text = ""  # handled by handle_non_text_message
        elif msg_type == "post":
            text = ""  # handled by handle_non_text_message
        else:
            return None  # unsupported type, still return message info for logging

        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}
        tenant_key = header.get("tenant_key") or ""
        user_id = sender_id.get("user_id") or sender_id.get("open_id") or sender_id.get("union_id") or "unknown"
        chat_id = message.get("chat_id") or user_id

        return {
            "event_id": header.get("event_id") or event.get("event_id"),
            "tenant_key": tenant_key,
            "user_id": user_id,
            "chat_id": chat_id,
            "message_id": message.get("message_id"),
            "chat_type": message.get("chat_type"),
            "message_type": msg_type,
            "text": text,
            "raw_content": content,
            "raw": payload,
            # @mention detection
            "is_group": message.get("chat_type") == "group",
            "is_mentioned": self.is_bot_mentioned({"raw_content": content}, ""),
        }

    @staticmethod
    def parse_card_action(payload: dict) -> Optional[dict]:
        header = payload.get("header") or {}
        event = payload.get("event") or {}
        event_type = header.get("event_type") or payload.get("type")
        if event_type not in {"card.action.trigger", "interactive_card.action_trigger"}:
            return None
        action = event.get("action") or payload.get("action") or {}
        value = action.get("value") or {}
        action_name = value.get("action")
        session_id = value.get("session_id")
        if action_name not in {"approve", "deny"} or not session_id:
            return None
        return {
            "action": action_name,
            "request_id": value.get("request_id"),
            "session_id": session_id,
        }

    @staticmethod
    def parse_message_content(content: Any) -> dict:
        if isinstance(content, dict):
            return content
        if not content:
            return {}
        try:
            return json.loads(content)
        except Exception:
            return {"text": str(content)}

    @staticmethod
    def clean_message_text(text: str) -> str:
        cleaned = re.sub(r"<at[^>]*>.*?</at>", "", text or "", flags=re.I | re.S)
        cleaned = re.sub(r"@_user_\d+", "", cleaned)
        return cleaned.strip()

    # ═══════════════════════════════════════════════════════════════
    # Session & Dedup
    # ═══════════════════════════════════════════════════════════════

    def session_id_for(self, message: dict) -> str:
        tenant = str(message.get("tenant_key") or "tenant")
        chat = str(message.get("chat_id") or message.get("user_id") or "default")
        safe_tenant = re.sub(r"[^a-zA-Z0-9_.:-]", "_", tenant)
        safe_chat = re.sub(r"[^a-zA-Z0-9_.:-]", "_", chat)
        return f"feishu_{safe_tenant}_{safe_chat}"

    async def is_duplicate_event(self, agent, message: dict) -> bool:
        event_id = message.get("event_id")
        if not event_id:
            return False
        try:
            key = f"feishu_event_seen:{event_id}"
            inserted = await agent.ctx.redis.set(key, "1", ex=300, nx=True)
            return inserted is False
        except Exception:
            return False

    # ═══════════════════════════════════════════════════════════════
    # Confirmation Flow
    # ═══════════════════════════════════════════════════════════════

    def parse_confirmation_action(self, text: str) -> Optional[tuple[str, Optional[str]]]:
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

    async def apply_confirmation_action(self, agent, message: dict, action_info: tuple[str, Optional[str]]) -> dict:
        action, request_id = action_info
        session_id = self.session_id_for(message)
        return await self.apply_confirmation_action_for_session(agent, session_id, action, request_id)

    async def apply_confirmation_action_for_session(self, agent, session_id: str, action: str, request_id: Optional[str] = None) -> dict:
        pending = await self.find_pending_confirmation(agent, session_id, request_id)
        if not pending:
            return {"status": "not_found", "message": "没有找到待处理的确认请求。"}
        key, data = pending
        active_turn = await agent._get_active_chat_turn(session_id)
        request_turn = data.get("turn_id")
        if action == "approve" and request_turn and request_turn != active_turn:
            await agent._mark_confirmation_denied(key, data, "stale_confirmation_request")
            return {"status": "stale", "message": "确认请求已过期，请重新发起任务。"}
        data["status"] = "approved" if action == "approve" else "denied"
        await agent.ctx.redis.set(key, json.dumps(data, ensure_ascii=False), ex=60)
        return {
            "status": "success",
            "message": "已允许执行。" if action == "approve" else "已拒绝执行。",
            "request_id": data.get("request_id"),
        }

    async def find_pending_confirmation(self, agent, session_id: str, request_id: Optional[str] = None):
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
        watcher_task = asyncio.create_task(self.watch_confirmations(agent, message, session_id, chat_task))
        try:
            answer = await chat_task
            # Use rich card for structured results, plain text otherwise
            if len(answer) > 500 and ("结果" in answer or "完成" in answer or "成功" in answer):
                card = self.build_result_card(
                    title="处理结果",
                    content=answer[:8000],
                    status="success",
                )
                await self.deliver_card(message, card)
            else:
                await self.deliver(message, answer)
        except Exception as e:
            if self.logger:
                self.logger.error(f"Feishu adapter chat failed: {e}", exc_info=True)
            error_card = self.build_error_card(
                title="处理失败",
                error=str(e),
                suggestion="请重试或输入 /retry 重新执行",
            )
            await self.deliver_card(message, error_card)
        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

    async def watch_confirmations(self, agent, message: dict, session_id: str, chat_task: asyncio.Task):
        notified = set()
        deadline = time.time() + max(5, self.config.confirmation_watch_sec)
        while not chat_task.done() and time.time() < deadline:
            pending = await self.find_pending_confirmation(agent, session_id)
            if pending:
                _, data = pending
                request_id = data.get("request_id")
                if request_id and request_id not in notified:
                    notified.add(request_id)
                    prompt = data.get("prompt", "")
                    await self.deliver_confirmation(message, session_id=session_id, request_id=request_id, prompt=prompt)
            await asyncio.sleep(0.8)

    # ═══════════════════════════════════════════════════════════════
    # Confirmation Card
    # ═══════════════════════════════════════════════════════════════

    def build_confirmation_card(self, session_id: str, request_id: str, prompt: str) -> dict:
        lang = self.config.lang if self.config.lang in ("zh", "en") else "zh"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": I18N["confirm_title"][lang]},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**请求 ID**：{request_id}\n\n{prompt}",
                    },
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": I18N["confirm_approve"][lang]},
                            "type": "primary",
                            "value": {"action": "approve", "session_id": session_id, "request_id": request_id},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": I18N["confirm_deny"][lang]},
                            "type": "danger",
                            "value": {"action": "deny", "session_id": session_id, "request_id": request_id},
                        },
                    ],
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": I18N["confirm_hint"][lang].replace("{id}", request_id),
                        }
                    ],
                },
            ],
        }

    # ═══════════════════════════════════════════════════════════════
    # Utilities
    # ═══════════════════════════════════════════════════════════════

    def t(self, key: str, **kwargs) -> str:
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
    def split_text(text: str, limit: int = 3500):
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

    @staticmethod
    def forbidden(message: str):
        if HTTPException is not None:
            raise HTTPException(status_code=403, detail=message)
        return {"status": "error", "message": message}

    # ═══════════════════════════════════════════════════════════════
    # Auth & Crypto (unchanged)
    # ═══════════════════════════════════════════════════════════════

    def decode_payload(self, raw_body: bytes) -> dict:
        payload = json.loads(raw_body.decode("utf-8"))
        encrypted = payload.get("encrypt")
        if not encrypted:
            return payload
        if not self.config.encrypt_key:
            raise ValueError("Encrypted Feishu callback requires FEISHU_ENCRYPT_KEY")
        return json.loads(self.decrypt_event_payload(encrypted))

    def decrypt_event_payload(self, encrypted_text: str) -> str:
        if Cipher is None:
            raise RuntimeError("cryptography is required to decrypt Feishu callback payloads")
        key = hashlib.sha256(self.config.encrypt_key.encode("utf-8")).digest()
        encrypted = base64.b64decode(encrypted_text)
        iv, cipher_text = encrypted[:16], encrypted[16:]
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend()).decryptor()
        padded = decryptor.update(cipher_text) + decryptor.finalize()
        pad_len = padded[-1]
        if pad_len < 1 or pad_len > 16:
            raise ValueError("Invalid Feishu encrypted payload padding")
        return padded[:-pad_len].decode("utf-8")

    def verify_signature(self, headers, raw_body: bytes) -> bool:
        signature = headers.get("X-Lark-Signature") or headers.get("x-lark-signature")
        if not signature:
            return True
        if not self.config.encrypt_key:
            return False
        timestamp = headers.get("X-Lark-Request-Timestamp") or headers.get("x-lark-request-timestamp") or ""
        nonce = headers.get("X-Lark-Request-Nonce") or headers.get("x-lark-request-nonce") or ""
        content = f"{timestamp}{nonce}{self.config.encrypt_key}".encode("utf-8") + raw_body
        expected = hashlib.sha256(content).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_token(self, payload: dict) -> bool:
        expected = self.config.verification_token
        if not expected:
            return True
        actual = payload.get("token") or payload.get("header", {}).get("token")
        return hmac.compare_digest(str(actual or ""), expected)

    @staticmethod
    def is_url_verification(payload: dict) -> bool:
        return payload.get("type") == "url_verification" and "challenge" in payload

    async def tenant_access_token(self) -> str:
        if self._tenant_access_token and time.time() < self._tenant_access_token_expire_at - 60:
            return self._tenant_access_token
        url = f"{self.config.api_base}/open-apis/auth/v3/tenant_access_token/internal"
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json={"app_id": self.config.app_id, "app_secret": self.config.app_secret}) as resp:
                data = await resp.json(content_type=None)
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu tenant_access_token failed: {data}")
        self._tenant_access_token = data["tenant_access_token"]
        self._tenant_access_token_expire_at = time.time() + int(data.get("expire", 7200))
        return self._tenant_access_token
