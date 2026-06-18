"""Feishu long-connection bridge for local bot validation.

The Feishu agent-ready template uses long connection by default. This script
keeps that mode and receives private/group messages without a public callback
URL. When an LLM provider is configured it forwards messages to OpenMegatron;
otherwise it returns a helpful local capability/configuration response.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi import ws
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1


ROOT = Path(__file__).resolve().parents[1]
PYSRC = ROOT / "pysrc"
EVENT_LOG = ROOT / ".runtime" / "feishu_events.jsonl"

if str(PYSRC) not in sys.path:
    sys.path.insert(0, str(PYSRC))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def post_json(url: str, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"code": exc.code, "msg": raw}


class FeishuLongConnectionBridge:
    def __init__(self) -> None:
        load_env_file(ROOT / ".env.feishu.local")
        self.app_id = os.environ["FEISHU_APP_ID"]
        self.app_secret = os.environ["FEISHU_APP_SECRET"]
        self.api_base = os.environ.get("FEISHU_API_BASE", "https://open.feishu.cn").rstrip("/")
        self.reply_timeout_sec = float(os.environ.get("FEISHU_AGENT_REPLY_TIMEOUT_SEC", "120"))
        self._token = ""
        self._token_expire_at = 0.0
        self._agent: Any | None = None
        self._agent_error = ""
        self._agent_lock = threading.Lock()

    def tenant_access_token(self) -> str:
        if self._token and time.time() < self._token_expire_at - 60:
            return self._token
        data = post_json(
            f"{self.api_base}/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        if data.get("code") != 0:
            raise RuntimeError(f"tenant_access_token failed: {data}")
        self._token = data["tenant_access_token"]
        self._token_expire_at = time.time() + int(data.get("expire", 7200))
        return self._token

    def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        body = {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}
        return post_json(
            f"{self.api_base}/open-apis/im/v1/messages/{message_id}/reply",
            body,
            token=self.tenant_access_token(),
        )

    def _load_agent_config(self) -> dict[str, Any]:
        from agent import load_config

        return load_config("model.toml")

    def _configured_provider(self, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        provider_id = str(config.get("llm_provider") or "openai").strip().lower()
        provider_cfg = (config.get("llm_providers") or {}).get(provider_id) or config.get("llm", {})
        return provider_id, provider_cfg

    def _has_llm_key(self, config: dict[str, Any]) -> bool:
        _, provider_cfg = self._configured_provider(config)
        return bool(str(provider_cfg.get("api_key") or "").strip())

    def _clean_feishu_text(self, text: str) -> str:
        text = text or ""
        text = re.sub(r"<at\b[^>]*>.*?</at>", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"@_user_\d+", " ", text)
        text = text.replace("\\n", "\n")
        return re.sub(r"\s+", " ", text).strip()

    def _local_reply(self, user_text: str, missing_key: bool = False, error: str = "") -> str:
        normalized = user_text.strip().lower()
        asks_capability = any(
            key in normalized
            for key in ("你能做啥", "你是谁", "能做什么", "help", "/help", "what can you do")
        )
        if asks_capability:
            prefix = "我是 OpenMegatron，已经接入飞书。"
            if missing_key:
                prefix += " 现在飞书链路已通，但本地还没有配置 LLM API Key，所以先以本地说明模式回复。"
            return (
                f"{prefix}\n\n"
                "我可以帮你做这些事：\n"
                "1. 生成、修改、检查可编辑 PPT，适配 PPT Master skill。\n"
                "2. 读论文、做综述、证据矩阵、引用核验和期刊匹配。\n"
                "3. 做代码分析、测试、重构、评审和项目自动化。\n"
                "4. 处理本地文件、表格、文档、图片和视频工作流。\n"
                "5. 作为飞书机器人接收群聊/私聊任务，并把结果回传到飞书。\n\n"
                "要启用完整智能体回复，请在 `pysrc/model.toml` 配好当前 provider 的 `api_key`，"
                "或设置对应环境变量后重启长连接脚本。"
            )
        if missing_key:
            return (
                "飞书连接已经正常，但完整 OpenMegatron 还没有可用的 LLM API Key。\n"
                "请在 `pysrc/model.toml` 配置当前 provider 的 `api_key`，或设置环境变量后重启我。"
            )
        if error:
            return (
                "我收到消息了，但调用 OpenMegatron 时失败。\n"
                f"错误摘要：{error[:500]}"
            )
        return "我收到消息了。"

    async def _get_agent(self) -> Any:
        if self._agent is not None:
            return self._agent
        if self._agent_error:
            raise RuntimeError(self._agent_error)
        with self._agent_lock:
            if self._agent is not None:
                return self._agent
            if self._agent_error:
                raise RuntimeError(self._agent_error)
            try:
                from agent import YuanGeAgent

                config = self._load_agent_config()
                agent = YuanGeAgent(config)
                self._agent = agent
            except Exception as exc:
                self._agent_error = str(exc)
                raise
        await self._agent.initialize()
        return self._agent

    async def answer_text(self, record: dict[str, Any]) -> str:
        user_text = self._clean_feishu_text(record.get("text", ""))
        if not user_text:
            return "我收到了一条非文本消息。目前飞书桥接先支持文本任务。"
        try:
            config = self._load_agent_config()
            if not self._has_llm_key(config):
                return self._local_reply(user_text, missing_key=True)
        except Exception as exc:
            return self._local_reply(user_text, error=str(exc))

        session_id = f"feishu_{record.get('tenant_key') or 'tenant'}_{record.get('chat_id') or 'chat'}"
        try:
            agent = await self._get_agent()
            return await asyncio.wait_for(
                agent.chat(session_id=session_id, user_input=user_text, domain="auto"),
                timeout=self.reply_timeout_sec,
            )
        except Exception as exc:
            return self._local_reply(user_text, error=str(exc))

    def on_message(self, event: P2ImMessageReceiveV1) -> None:
        data = event.event
        if not data or not data.message:
            return
        message = data.message
        sender = data.sender
        sender_id = getattr(sender, "sender_id", None) if sender else None
        content: dict[str, Any] = {}
        try:
            content = json.loads(message.content or "{}")
        except Exception:
            content = {}
        record = {
            "ts": time.time(),
            "event_id": getattr(event.header, "event_id", "") if event.header else "",
            "tenant_key": getattr(event.header, "tenant_key", "") if event.header else "",
            "chat_id": message.chat_id or "",
            "message_id": message.message_id or "",
            "message_type": message.message_type or "",
            "text": content.get("text", ""),
            "open_id": getattr(sender_id, "open_id", "") if sender_id else "",
            "user_id": getattr(sender_id, "user_id", "") if sender_id else "",
            "union_id": getattr(sender_id, "union_id", "") if sender_id else "",
        }
        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with EVENT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        def send_reply() -> None:
            text = asyncio.run(self.answer_text(record))
            result = self.reply_text(record["message_id"], text)
            with EVENT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "reply_result": result}, ensure_ascii=False) + "\n")

        if record["message_id"]:
            threading.Thread(target=send_reply, daemon=True).start()

    def run(self) -> None:
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self.on_message)
            .build()
        )
        client = ws.Client(self.app_id, self.app_secret, event_handler=handler)
        client.start()


if __name__ == "__main__":
    FeishuLongConnectionBridge().run()
