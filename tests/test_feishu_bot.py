import asyncio
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PY_SRC = str(ROOT / "pysrc")
if PY_SRC not in sys.path:
    sys.path.insert(0, PY_SRC)

from pysrc.integrations.feishu_bot import FeishuBotAdapter, FeishuConfig


class FakeRequest:
    def __init__(self, payload: dict, headers: dict | None = None):
        self.payload = payload
        self.headers = headers or {}

    async def body(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class FakeBackgroundTasks:
    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args):
        self.tasks.append((fn, args))


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [key for key in self.values if key.startswith(prefix)]


class FakeContext:
    def __init__(self):
        self.redis = FakeRedis()
        self.cleared = []

    async def check_redis(self):
        return {"redis": {"ok": True}}

    async def clear_history(self, session_id):
        self.cleared.append(session_id)


class FakeConfirmationState:
    def __init__(self, redis):
        self.redis = redis

    async def get_request(self, key):
        raw = self.redis.values.get(key)
        return json.loads(raw) if raw else None


class FakeAgent:
    def __init__(self):
        self.ctx = FakeContext()
        self.confirmation_state = FakeConfirmationState(self.ctx.redis)
        self.loaded_skills = {"ppt_master": {"description": "Generate editable PPTX"}}
        self.chat_calls = []

    async def chat(self, session_id, text, domain="auto"):
        self.chat_calls.append((session_id, text, domain))
        return f"echo: {text}"

    async def _get_active_chat_turn(self, session_id):
        return "turn-1"

    async def _mark_confirmation_denied(self, key, data, reason):
        data["status"] = "denied"
        data["reason"] = reason


def feishu_message_payload(text="hello", event_id="evt-1", token="token"):
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "tenant_key": "tenant",
            "token": token,
        },
        "event": {
            "sender": {"sender_id": {"open_id": "user"}},
            "message": {
                "message_id": "mid",
                "chat_id": "chat",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


class TestFeishuBotAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_url_verification_returns_challenge(self):
        adapter = FeishuBotAdapter(FeishuConfig(verification_token="token", reply_enabled=False))
        payload = {"type": "url_verification", "token": "token", "challenge": "challenge-ok"}

        result = await adapter.handle_callback(FakeRequest(payload), FakeAgent(), FakeBackgroundTasks())

        self.assertEqual(result, {"challenge": "challenge-ok"})

    async def test_invalid_token_is_forbidden(self):
        adapter = FeishuBotAdapter(FeishuConfig(verification_token="expected", reply_enabled=False))
        payload = {"type": "url_verification", "token": "wrong", "challenge": "nope"}

        with self.assertRaises(Exception):
            await adapter.handle_callback(FakeRequest(payload), FakeAgent(), FakeBackgroundTasks())

    async def test_text_message_is_queued_for_processing(self):
        adapter = FeishuBotAdapter(FeishuConfig(verification_token="token", reply_enabled=False))
        bg = FakeBackgroundTasks()

        result = await adapter.handle_callback(
            FakeRequest(feishu_message_payload("hello <at user_id=\"bot\">bot</at>")),
            FakeAgent(),
            bg,
        )

        self.assertEqual(result["status"], "processing")
        self.assertEqual(len(bg.tasks), 2)

    async def test_duplicate_event_is_ignored(self):
        adapter = FeishuBotAdapter(FeishuConfig(verification_token="token", reply_enabled=False))
        agent = FakeAgent()
        payload = feishu_message_payload("hello", event_id="same-event")

        first = await adapter.handle_callback(FakeRequest(payload), agent, FakeBackgroundTasks())
        second = await adapter.handle_callback(FakeRequest(payload), agent, FakeBackgroundTasks())

        self.assertEqual(first["status"], "processing")
        self.assertEqual(second["status"], "duplicate_ignored")

    async def test_slash_commands_return_clean_text(self):
        adapter = FeishuBotAdapter(FeishuConfig(reply_enabled=False))
        agent = FakeAgent()
        message = {"tenant_key": "tenant", "chat_id": "chat", "text": "/status"}

        status = await adapter.handle_slash_command(agent, message, "/status")
        skills = await adapter.handle_slash_command(agent, message, "/skills")

        self.assertIn("系统状态", status)
        self.assertIn("ppt_master", skills)

    def test_confirmation_card_has_clean_labels(self):
        adapter = FeishuBotAdapter(FeishuConfig(lang="zh"))
        card = adapter.build_confirmation_card("session", "req-1", "Run command?")
        content = json.dumps(card, ensure_ascii=False)

        self.assertIn("需要确认后继续执行", content)
        self.assertIn("允许", content)
        self.assertIn("拒绝", content)
        self.assertNotIn("鍚", content)

    async def test_apply_confirmation_updates_pending_request(self):
        adapter = FeishuBotAdapter(FeishuConfig(reply_enabled=False))
        agent = FakeAgent()
        key = "confirm_req:feishu_tenant_chat:req-1"
        await agent.ctx.redis.set(
            key,
            json.dumps({"status": "pending", "request_id": "req-1", "turn_id": "turn-1"}),
        )

        result = await adapter.apply_confirmation_action_for_session(
            agent,
            "feishu_tenant_chat",
            "approve",
            "req-1",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["message"], "已允许执行。")

    async def test_proactive_message_uses_receive_id_type(self):
        adapter = FeishuBotAdapter(
            FeishuConfig(
                app_id="app",
                app_secret="secret",
                reply_enabled=True,
                proactive_receive_id_type="open_id",
            )
        )
        seen = {}

        async def fake_token():
            return "tenant-token"

        async def fake_post(url, headers, body):
            seen["url"] = url
            seen["headers"] = headers
            seen["body"] = body
            return {"code": 0}

        adapter.tenant_access_token = fake_token
        adapter._post_json = fake_post

        result = await adapter.send_proactive_message("ou_xxx", "text", {"text": "hello"})

        self.assertEqual(result["code"], 0)
        self.assertIn("receive_id_type=open_id", seen["url"])
        self.assertEqual(seen["body"]["receive_id"], "ou_xxx")


if __name__ == "__main__":
    unittest.main()
