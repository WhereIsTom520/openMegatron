"""Multi-channel adapter framework for OpenMegatron.

Inspired by OpenClaw's channel architecture, this provides a unified
abstraction over Feishu, WeChat Work, and future channels (Telegram,
DingTalk, Slack, Discord, WhatsApp, etc.).

Key design decisions from OpenClaw:
  - Each channel is a self-contained adapter with its own config, auth,
    message parsing, dedup, confirmation flow, and delivery.
  - The IMGateway auto-registers endpoints from a list of adapters.
  - Session isolation: {channel}_{tenant}_{chat_id} format.
  - All adapters share the same agent.chat() entry point.
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Unified message format
# ═══════════════════════════════════════════════════════════════

@dataclass
class ChannelMessage:
    """Normalized message format across all channels."""
    channel: str                        # "feishu" | "wecom" | "telegram" | ...
    event_id: str = ""
    tenant_key: str = ""                # org/workspace identifier
    user_id: str = ""                   # sender
    user_name: str = ""                 # sender display name (when available)
    chat_id: str = ""                   # conversation
    chat_type: str = "single"           # "single" | "group"
    message_id: str = ""                # platform message ID (for reply)
    message_type: str = "text"          # "text" | "image" | "file" | "audio" | "post"
    text: str = ""                      # cleaned text content
    raw_content: dict = field(default_factory=dict)  # original parsed content
    raw_payload: dict = field(default_factory=dict)  # full platform payload
    is_mentioned: bool = False          # was bot @mentioned (group chats)
    is_group: bool = False
    created_at: float = field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════
# Abstract base adapter
# ═══════════════════════════════════════════════════════════════

class BaseChannelAdapter(abc.ABC):
    """Abstract base for all channel adapters.

    Each adapter MUST implement:
      - handle_callback(request, agent, background_tasks) → dict
      - session_id_for(message: ChannelMessage) → str
      - deliver(message: ChannelMessage, answer: str) → None

    Each adapter MAY override:
      - parse_event(payload: dict) → Optional[ChannelMessage]
      - handle_slash_command(agent, message: ChannelMessage, text: str) → Optional[str]
      - deliver_card(message: ChannelMessage, card: dict) → None
      - send_proactive_message(chat_id: str, msg_type: str, content: Any) → None
    """

    name: str = "base"
    display_name: str = "Base Channel"
    endpoint_path: str = "/integrations/base/events"

    @abc.abstractmethod
    async def handle_callback(self, request, agent, background_tasks) -> dict:
        """Process an incoming webhook/event from the platform."""

    @abc.abstractmethod
    def session_id_for(self, message: ChannelMessage) -> str:
        """Generate a unique session ID for this conversation."""

    @abc.abstractmethod
    async def deliver(self, message: ChannelMessage, answer: str):
        """Send a text reply back to the user."""

    # ── Optional overrides ──

    async def deliver_card(self, message: ChannelMessage, card: dict):
        """Send a rich card. Default: fall back to text."""
        await self.deliver(message, json.dumps(card, ensure_ascii=False))

    async def send_proactive_message(self, chat_id: str, msg_type: str, content: Any) -> Any:
        """Send a bot-initiated message (not a reply). Default: not supported."""
        raise NotImplementedError(f"{self.name} does not support proactive messages")

    async def handle_slash_command(self, agent, message: ChannelMessage, text: str) -> Optional[str]:
        """Handle platform slash commands. Default: no commands."""
        return None

    def is_configured(self) -> bool:
        """Check if this adapter has valid credentials configured."""
        return True

    # ── Shared utilities for subclasses ──

    async def _dedup_event(self, agent, event_id: str, prefix: str = "event") -> bool:
        """Returns True if event was already processed (duplicate)."""
        if not event_id:
            return False
        try:
            key = f"{prefix}_seen:{event_id}"
            inserted = await agent.ctx.redis.set(key, "1", ex=300, nx=True)
            return inserted is False
        except Exception:
            return False

    async def _find_pending_confirmation(self, agent, session_id: str, request_id: str = None):
        """Find a pending HITL confirmation request for this session."""
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

    async def _apply_confirmation(self, agent, session_id: str, action: str,
                                  request_id: str = None) -> dict:
        """Apply approve/deny to a pending confirmation."""
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

    async def _watch_confirmations(self, agent, message: ChannelMessage,
                                   session_id: str, chat_task: asyncio.Task,
                                   confirmation_watch_sec: int = 65,
                                   on_confirmation=None):
        """Poll for pending confirmations and notify user."""
        notified = set()
        deadline = time.time() + max(5, confirmation_watch_sec)
        while not chat_task.done() and time.time() < deadline:
            pending = await self._find_pending_confirmation(agent, session_id)
            if pending:
                _, data = pending
                request_id = data.get("request_id")
                if request_id and request_id not in notified:
                    notified.add(request_id)
                    if on_confirmation:
                        await on_confirmation(message, session_id, request_id,
                                             data.get("prompt", ""))
            await asyncio.sleep(0.8)

    @staticmethod
    def _split_text(text: str, limit: int = 3500) -> list:
        """Split long text into chunks respecting the platform limit."""
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
    def _normalize_session_id(*parts: str) -> str:
        """Build a safe session ID from channel, tenant, chat parts."""
        safe_parts = []
        for p in parts:
            safe_parts.append(re.sub(r"[^a-zA-Z0-9_.:-]", "_", str(p)))
        return "_".join(safe_parts)


# ═══════════════════════════════════════════════════════════════
# Channel registry
# ═══════════════════════════════════════════════════════════════

class ChannelRegistry:
    """Registry of all configured channel adapters."""

    def __init__(self):
        self._adapters: dict[str, BaseChannelAdapter] = {}

    def register(self, adapter: BaseChannelAdapter):
        self._adapters[adapter.name] = adapter
        logger.info(f"Channel registered: {adapter.name} ({adapter.display_name})")

    def get(self, name: str) -> Optional[BaseChannelAdapter]:
        return self._adapters.get(name)

    def list_configured(self) -> list[BaseChannelAdapter]:
        """Return adapters that have valid credentials."""
        return [a for a in self._adapters.values() if a.is_configured()]

    def list_all(self) -> list[BaseChannelAdapter]:
        return list(self._adapters.values())

    @property
    def count(self) -> int:
        return len(self._adapters)
