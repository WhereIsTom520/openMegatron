"""WeChat Work (企业微信) bot adapter for OpenMegatron.

Provides the same interface as feishu_bot.py so the IMGateway can register
both adapters side-by-side without changes to the gateway itself.

Architecture:
  WecomConfig    – dataclass loaded from env vars or TOML config
  WecomBotAdapter – handles callback verification, message parsing,
                    deduplication, confirmation flow, and reply delivery

Callback flow:
  POST /integrations/wecom/events
    → verify URL (echostr decryption) or parse message
    → decrypt XML body → extract text / user / chat
    → deduplicate via Redis SETNX
    → agent.chat(session_id, text) + watch_confirmations()
    → reply via /cgi-bin/message/send
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import struct
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

try:
    from fastapi import HTTPException
except Exception:  # pragma: no cover
    HTTPException = None

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:  # pragma: no cover
    Cipher = None
    algorithms = None
    modes = None
    default_backend = None


APPROVE_WORDS = {"/approve", "approve", "同意", "允许", "确认", "y", "yes"}
DENY_WORDS = {"/deny", "deny", "拒绝", "不同意", "取消", "n", "no"}


# ── Config ──────────────────────────────────────────────────────────────

@dataclass
class WecomConfig:
    corp_id: str = ""
    corp_secret: str = ""
    token: str = ""            # callback verification token
    encoding_aes_key: str = "" # 43-char Base64 AES key for callback encryption
    agent_id: str = ""         # application agent_id (needed for message sending)
    api_base: str = "https://qyapi.weixin.qq.com"
    request_timeout_sec: int = 20
    reply_enabled: bool = True
    confirmation_watch_sec: int = 65

    @classmethod
    def from_config(cls, config: dict) -> "WecomConfig":
        integrations = config.get("integrations", {}) if isinstance(config, dict) else {}
        wecom_cfg = integrations.get("wecom", {}) or config.get("wecom", {}) or {}

        def env_or_cfg(env_name: str, key: str, default: Any = ""):
            value = os.environ.get(env_name)
            if value is not None:
                return value
            return wecom_cfg.get(key, default)

        def to_bool(value: Any, default: bool = True) -> bool:
            if value is None:
                return default
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

        return cls(
            corp_id=str(env_or_cfg("WECOM_CORP_ID", "corp_id", "")),
            corp_secret=str(env_or_cfg("WECOM_CORP_SECRET", "corp_secret", "")),
            token=str(env_or_cfg("WECOM_TOKEN", "token", "")),
            encoding_aes_key=str(env_or_cfg("WECOM_ENCODING_AES_KEY", "encoding_aes_key", "")),
            agent_id=str(env_or_cfg("WECOM_AGENT_ID", "agent_id", "")),
            api_base=str(env_or_cfg("WECOM_API_BASE", "api_base", "https://qyapi.weixin.qq.com")).rstrip("/"),
            request_timeout_sec=int(env_or_cfg("WECOM_REQUEST_TIMEOUT_SEC", "request_timeout_sec", 20)),
            reply_enabled=to_bool(env_or_cfg("WECOM_REPLY_ENABLED", "reply_enabled", True)),
            confirmation_watch_sec=int(env_or_cfg("WECOM_CONFIRMATION_WATCH_SEC", "confirmation_watch_sec", 65)),
        )


# ── WeChat Work crypto helpers ─────────────────────────────────────────

def _wecom_aes_key(key_b64: str) -> bytes:
    """Decode the 43-char Base64 encoding_aes_key to 32-byte AES key."""
    return base64.b64decode(key_b64 + "=")


def _wecom_decrypt(encrypted_text: str, encoding_aes_key: str, corp_id: str) -> str:
    """Decrypt a WeChat Work encrypted message (AES-256-CBC, PKCS#7, XML format).

    The encrypted payload is a Base64-encoded blob: 16-byte random + 4-byte
    msg_len (network order) + msg + corp_id.  After decryption the corp_id
    suffix is stripped and the plaintext XML is returned.
    """
    if Cipher is None:
        raise RuntimeError("cryptography is required to decrypt WeChat Work callback payloads")
    key = _wecom_aes_key(encoding_aes_key)
    encrypted = base64.b64decode(encrypted_text)
    iv = key[:16]  # WeChat Work uses the first 16 bytes of the AES key as IV
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend()).decryptor()
    plain = decryptor.update(encrypted) + decryptor.finalize()
    # PKCS#7 unpad
    pad_len = plain[-1]
    if pad_len < 1 or pad_len > 32:
        raise ValueError("Invalid WeChat Work encrypted payload padding")
    plain = plain[:-pad_len]
    # Skip 16-byte random prefix, then 4-byte network-order length
    msg_len = struct.unpack("!I", plain[16:20])[0]
    result = plain[20:20 + msg_len].decode("utf-8", errors="ignore")
    # Verify trailing corp_id matches
    tail = plain[20 + msg_len:].decode("utf-8", errors="ignore")
    if tail != corp_id:
        raise ValueError(f"WeChat Work corp_id mismatch: expected '{corp_id}', got '{tail}'")
    return result


def _wecom_encrypt(plaintext: str, encoding_aes_key: str, corp_id: str) -> str:
    """Encrypt a reply for WeChat Work (AES-256-CBC, PKCS#7)."""
    if Cipher is None:
        raise RuntimeError("cryptography is required to encrypt WeChat Work replies")
    key = _wecom_aes_key(encoding_aes_key)
    iv = key[:16]
    random_prefix = os.urandom(16)
    msg_bytes = plaintext.encode("utf-8")
    corp_bytes = corp_id.encode("utf-8")
    length_prefix = struct.pack("!I", len(msg_bytes))
    plain = random_prefix + length_prefix + msg_bytes + corp_bytes
    # PKCS#7 pad to 32-byte blocks
    pad_len = 32 - (len(plain) % 32)
    plain += bytes([pad_len] * pad_len)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend()).encryptor()
    return base64.b64encode(encryptor.update(plain) + encryptor.finalize()).decode("ascii")


def _wecom_verify_signature(token: str, timestamp: str, nonce: str, encrypt_msg: str,
                            expected_signature: str) -> bool:
    """Verify WeChat Work callback signature (SHA1 of sorted token+timestamp+nonce+encrypt)."""
    params = sorted([token, timestamp, nonce, encrypt_msg])
    computed = hashlib.sha1("".join(params).encode("utf-8")).hexdigest()
    return hmac.compare_digest(computed, expected_signature)


# ── Adapter ─────────────────────────────────────────────────────────────

class WecomBotAdapter:
    name = "wecom"

    def __init__(self, config: WecomConfig, logger=None):
        self.config = config
        self.logger = logger
        self._access_token: Optional[str] = None
        self._access_token_expire_at = 0.0

    # ── Callback entry point ────────────────────────────────────────────

    async def handle_callback(self, request, agent, background_tasks):
        """Main entry point for WeChat Work callback events."""
        raw_body = await request.body()
        params = dict(request.query_params)

        # URL verification (echostr)
        echostr = params.get("echostr")
        if echostr:
            return self._handle_url_verification(params)

        # Message callback: parse XML
        try:
            root = ET.fromstring(raw_body.decode("utf-8"))
        except ET.ParseError as e:
            return self._forbidden(f"invalid xml: {e}")

        encrypt_elem = root.find("Encrypt")
        if encrypt_elem is not None and encrypt_elem.text:
            # Verify signature before decrypting
            msg_signature = params.get("msg_signature", "")
            timestamp = params.get("timestamp", "")
            nonce = params.get("nonce", "")
            if self.config.token and msg_signature:
                if not _wecom_verify_signature(
                    self.config.token, timestamp, nonce, encrypt_elem.text, msg_signature
                ):
                    return self._forbidden("invalid signature")
            try:
                decrypted_xml = _wecom_decrypt(
                    encrypt_elem.text, self.config.encoding_aes_key, self.config.corp_id
                )
                root = ET.fromstring(decrypted_xml)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"WeChat Work decrypt failed: {e}")
                return self._forbidden(f"decrypt failed: {e}")

        message = self._parse_xml_message(root)
        if not message:
            return {"status": "ignored"}

        if await self._is_duplicate_event(agent, message):
            return {"status": "duplicate_ignored"}

        confirm_action = self._parse_confirmation_action(message.get("text", ""))
        if confirm_action:
            result = await self._apply_confirmation_action(agent, message, confirm_action)
            if result.get("status") == "success":
                await self._deliver(message, result.get("message", "已处理确认请求。"))
            return result

        background_tasks.add_task(self._process_and_reply, agent, message)
        return {"status": "processing"}

    def _handle_url_verification(self, params: dict):
        """Decrypt echostr and return plaintext (required by WeChat Work)."""
        echostr = params.get("echostr", "")
        msg_signature = params.get("msg_signature", "")
        timestamp = params.get("timestamp", "")
        nonce = params.get("nonce", "")
        if self.config.token and msg_signature:
            if not _wecom_verify_signature(
                self.config.token, timestamp, nonce, echostr, msg_signature
            ):
                return self._forbidden("invalid signature for url verification")
        if not self.config.encoding_aes_key:
            raise ValueError("WECOM_ENCODING_AES_KEY required for callback verification")
        try:
            plain = _wecom_decrypt(echostr, self.config.encoding_aes_key, self.config.corp_id)
            return plain
        except Exception as e:
            if self.logger:
                self.logger.error(f"WeChat Work echostr decrypt failed: {e}")
            return self._forbidden(f"echostr decrypt failed: {e}")

    # ── XML message parsing ─────────────────────────────────────────────

    @staticmethod
    def _parse_xml_message(root: ET.Element) -> Optional[dict]:
        """Extract message fields from WeChat Work XML payload."""
        msg_type_el = root.find("MsgType")
        if msg_type_el is None or (msg_type_el.text or "").strip() != "text":
            return None
        content_el = root.find("Content")
        text = (content_el.text or "").strip() if content_el is not None else ""
        if not text:
            return None
        from_user = (root.findtext("FromUserName") or "unknown").strip()
        to_user = (root.findtext("ToUserName") or "").strip()
        msg_id = (root.findtext("MsgId") or "").strip()
        agent_id_el = root.find("AgentID")
        agent_id = (agent_id_el.text or "").strip() if agent_id_el is not None else ""
        chat_id_el = root.find("ChatId")
        chat_id = (chat_id_el.text or "").strip() if chat_id_el is not None else ""
        chat_type = (root.findtext("ChatType") or "single").strip()
        return {
            "msg_id": msg_id,
            "from_user": from_user,
            "to_user": to_user,
            "agent_id": agent_id,
            "chat_id": chat_id or from_user,
            "chat_type": chat_type,
            "text": text,
            "raw_xml": ET.tostring(root, encoding="unicode"),
        }

    # ── Session identity ────────────────────────────────────────────────

    def _session_id_for(self, message: dict) -> str:
        corp = self.config.corp_id or "corp"
        chat = str(message.get("chat_id") or message.get("from_user") or "default")
        safe_corp = re.sub(r"[^a-zA-Z0-9_.:-]", "_", corp)
        safe_chat = re.sub(r"[^a-zA-Z0-9_.:-]", "_", chat)
        return f"wecom_{safe_corp}_{safe_chat}"

    # ── Deduplication ───────────────────────────────────────────────────

    async def _is_duplicate_event(self, agent, message: dict) -> bool:
        msg_id = message.get("msg_id")
        if not msg_id:
            return False
        try:
            key = f"wecom_event_seen:{msg_id}"
            inserted = await agent.ctx.redis.set(key, "1", ex=300, nx=True)
            return inserted is False
        except Exception:
            return False

    # ── Confirmation flow ───────────────────────────────────────────────

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

    async def _apply_confirmation_action(self, agent, message: dict,
                                         action_info: tuple) -> dict:
        action, request_id = action_info
        session_id = self._session_id_for(message)
        return await self._apply_confirmation_action_for_session(
            agent, session_id, action, request_id
        )

    async def _apply_confirmation_action_for_session(
        self, agent, session_id: str, action: str, request_id: Optional[str] = None
    ) -> dict:
        pending = await self._find_pending_confirmation(agent, session_id, request_id)
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

    async def _find_pending_confirmation(self, agent, session_id: str,
                                         request_id: Optional[str] = None):
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

    # ── Task processing ─────────────────────────────────────────────────

    async def _process_and_reply(self, agent, message: dict):
        session_id = self._session_id_for(message)
        chat_task = asyncio.create_task(
            agent.chat(session_id, message.get("text", ""), domain="auto")
        )
        watcher_task = asyncio.create_task(
            self._watch_confirmations(agent, message, session_id, chat_task)
        )
        try:
            answer = await chat_task
            await self._deliver(message, answer)
        except Exception as e:
            if self.logger:
                self.logger.error(f"WeChat Work adapter chat failed: {e}", exc_info=True)
            await self._deliver(message, f"处理失败：{e}")
        finally:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

    async def _watch_confirmations(self, agent, message: dict, session_id: str,
                                   chat_task: asyncio.Task):
        notified = set()
        deadline = time.time() + max(5, self.config.confirmation_watch_sec)
        while not chat_task.done() and time.time() < deadline:
            pending = await self._find_pending_confirmation(agent, session_id)
            if pending:
                _, data = pending
                request_id = data.get("request_id")
                if request_id and request_id not in notified:
                    notified.add(request_id)
                    prompt = data.get("prompt", "")
                    await self._deliver_confirmation(
                        message, session_id=session_id,
                        request_id=request_id, prompt=prompt,
                    )
            await asyncio.sleep(0.8)

    # ── Message delivery ────────────────────────────────────────────────

    async def _deliver(self, message: dict, answer: str):
        await self._deliver_text(message, answer or "")

    async def _deliver_text(self, message: dict, text: str):
        if not self.config.reply_enabled or not self.config.corp_id or not self.config.corp_secret:
            if self.logger:
                self.logger.info(
                    f"[WeChat Work dry-run reply to {message.get('chat_id')}]: {text[:200]}"
                )
            return
        for chunk in self._split_text(text, limit=2000):
            await self._send_message(message, "text", {"content": chunk})

    async def _deliver_confirmation(self, message: dict, session_id: str,
                                    request_id: str, prompt: str):
        """Send a text-based confirmation prompt (WeChat Work text cards)."""
        text = (
            f"【需要确认后继续执行】\n"
            f"请求 ID：{request_id}\n"
            f"{prompt}\n\n"
            f"回复 /approve {request_id} 允许执行\n"
            f"回复 /deny {request_id} 拒绝执行"
        )
        if not self.config.reply_enabled or not self.config.corp_id or not self.config.corp_secret:
            if self.logger:
                self.logger.info(
                    f"[WeChat Work dry-run confirmation to {message.get('chat_id')}]: {text}"
                )
            return
        await self._send_message(message, "text", {"content": text})

    async def _send_message(self, message: dict, msg_type: str, content: dict):
        """Send a message via WeChat Work API: POST /cgi-bin/message/send."""
        token = await self._access_token()
        url = f"{self.config.api_base}/cgi-bin/message/send?access_token={token}"
        body = {
            "touser": message.get("from_user", "@all"),
            "msgtype": msg_type,
            "agentid": int(self.config.agent_id) if self.config.agent_id else 0,
        }
        if msg_type == "text":
            body["text"] = content
        else:
            body[msg_type] = content
        # If it's a group chat, use toparty/totag or send to the ChatId
        if message.get("chat_type") == "group" and message.get("chat_id"):
            body.pop("touser", None)
            body["toparty"] = ""  # WeChat Work requires at least one recipient field
            # For group messages we still need touser or a valid recipient;
            # fall back to using the chat_id is not directly supported via
            # /message/send — we send to the original user as a DM instead
            body["touser"] = message.get("from_user", "@all")

        headers = {"Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                data = await resp.json(content_type=None)
        if data.get("errcode") != 0:
            raise RuntimeError(f"WeChat Work send message failed: {data}")

    @staticmethod
    def _split_text(text: str, limit: int = 2000):
        """Split long text into chunks respecting the WeChat Work byte limit (~2048)."""
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

    # ── Access token ────────────────────────────────────────────────────

    async def _access_token(self) -> str:
        """Get or refresh WeChat Work access token (2-hour TTL, 60s buffer)."""
        if self._access_token and time.time() < self._access_token_expire_at - 60:
            return self._access_token
        url = (
            f"{self.config.api_base}/cgi-bin/gettoken"
            f"?corpid={self.config.corp_id}&corpsecret={self.config.corp_secret}"
        )
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json(content_type=None)
        if data.get("errcode") != 0:
            raise RuntimeError(f"WeChat Work access_token failed: {data}")
        self._access_token = data["access_token"]
        self._access_token_expire_at = time.time() + int(data.get("expires_in", 7200))
        return self._access_token

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _forbidden(message: str):
        if HTTPException is not None:
            raise HTTPException(status_code=403, detail=message)
        return {"status": "error", "message": message}
