"""Lightweight Feishu callback bridge for validating bot connectivity.

This is intentionally small: it proves Feishu events can reach the local
machine through a tunnel, replies to incoming text messages, and records sender
IDs so the full OpenMegatron gateway can take over after LLM config is ready.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from fastapi import FastAPI, Request


ROOT = Path(__file__).resolve().parents[1]
EVENT_LOG = ROOT / ".runtime" / "feishu_events.jsonl"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def parse_text_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    header = payload.get("header") or {}
    event = payload.get("event") or {}
    if (header.get("event_type") or payload.get("type")) != "im.message.receive_v1":
        return None
    message = event.get("message") or {}
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    chat_id = (message.get("chat_id") or "").strip()
    message_id = (message.get("message_id") or "").strip()
    msg_type = message.get("message_type") or "text"
    content_raw = message.get("content") or "{}"
    try:
        content = json.loads(content_raw) if isinstance(content_raw, str) else dict(content_raw)
    except Exception:
        content = {}
    return {
        "event_id": header.get("event_id"),
        "tenant_key": header.get("tenant_key") or payload.get("tenant_key"),
        "chat_id": chat_id,
        "message_id": message_id,
        "message_type": msg_type,
        "text": content.get("text", "") if msg_type == "text" else "",
        "open_id": sender_id.get("open_id") or "",
        "user_id": sender_id.get("user_id") or "",
        "union_id": sender_id.get("union_id") or "",
    }


class FeishuEchoGateway:
    def __init__(self) -> None:
        load_env_file(ROOT / ".env.feishu.local")
        self.app_id = os.environ["FEISHU_APP_ID"]
        self.app_secret = os.environ["FEISHU_APP_SECRET"]
        self.api_base = os.environ.get("FEISHU_API_BASE", "https://open.feishu.cn").rstrip("/")
        self._token = ""
        self._token_expire_at = 0.0
        self.app = FastAPI(title="OpenMegatron Feishu Echo Gateway")
        self.app.post("/integrations/feishu/events")(self.handle_event)
        self.app.get("/healthz")(self.healthz)

    async def healthz(self) -> dict[str, Any]:
        return {"status": "ok", "app_id": self.app_id}

    async def tenant_access_token(self) -> str:
        if self._token and time.time() < self._token_expire_at - 60:
            return self._token
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(
                f"{self.api_base}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
            ) as resp:
                data = await resp.json(content_type=None)
        if data.get("code") != 0:
            raise RuntimeError(f"tenant_access_token failed: {data}")
        self._token = data["tenant_access_token"]
        self._token_expire_at = time.time() + int(data.get("expire", 7200))
        return self._token

    async def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        token = await self.tenant_access_token()
        body = {
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(
                f"{self.api_base}/open-apis/im/v1/messages/{message_id}/reply",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            ) as resp:
                return await resp.json(content_type=None)

    async def handle_event(self, request: Request) -> dict[str, Any]:
        payload = await request.json()
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        message = parse_text_message(payload)
        if not message:
            return {"status": "ignored"}

        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with EVENT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **message}, ensure_ascii=False) + "\n")

        if message["message_id"]:
            text = (
                "OpenMegatron 已接入飞书。\n"
                f"我收到了：{message['text'] or '[非文本消息]'}\n"
                "下一步会切到完整 Agent 回复。"
            )
            asyncio.create_task(self.reply_text(message["message_id"], text))
        return {"status": "processing"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    gateway = FeishuEchoGateway()
    uvicorn.run(gateway.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
