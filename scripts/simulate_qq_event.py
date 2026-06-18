"""Simulate a QQ bot message event for local testing.

Since QQ bot uses WebSocket (not webhook), this script simulates the
internal message handling flow directly rather than sending HTTP requests.

Usage:
  # Test message parsing and processing
  python scripts/simulate_qq_event.py "帮我查一下2024年人机协同的论文"

  # Test slash command
  python scripts/simulate_qq_event.py "/help"

  # Test confirmation
  python scripts/simulate_qq_event.py "/approve req_123"

  # Test direct message event
  python scripts/simulate_qq_event.py "hello" --type direct

  # Test group @mention event
  python scripts/simulate_qq_event.py "hello" --type group

  # Print event payload only
  python scripts/simulate_qq_event.py "hello" --print-only

Environment variables:
  QQ_APP_ID     - QQ bot app ID (for access token, optional in dry-run)
  QQ_APP_SECRET - QQ bot app secret
"""

import argparse
import json
import time


def build_direct_message(text: str) -> dict:
    """Build a QQ direct message (C2C) event payload."""
    msg_id = f"test_dm_{int(time.time() * 1000)}"
    return {
        "op": 0,
        "s": 1,
        "t": "C2C_MESSAGE_CREATE",
        "id": msg_id,
        "d": {
            "id": msg_id,
            "author": {
                "id": "test_user_openid",
                "username": "测试用户",
                "avatar": "",
            },
            "content": text,
            "timestamp": int(time.time() * 1000),
        },
    }


def build_group_message(text: str) -> dict:
    """Build a QQ group @message event payload."""
    msg_id = f"test_gm_{int(time.time() * 1000)}"
    return {
        "op": 0,
        "s": 1,
        "t": "GROUP_AT_MESSAGE_CREATE",
        "id": msg_id,
        "d": {
            "id": msg_id,
            "author": {
                "id": "test_group_user",
                "username": "群成员",
                "avatar": "",
            },
            "group_openid": "test_group_openid",
            "content": f"<@!bot_id> {text}",
            "timestamp": int(time.time() * 1000),
        },
    }


def build_guild_message(text: str) -> dict:
    """Build a QQ guild (channel) message event payload."""
    msg_id = f"test_guild_{int(time.time() * 1000)}"
    return {
        "op": 0,
        "s": 1,
        "t": "AT_MESSAGE_CREATE",
        "id": msg_id,
        "d": {
            "id": msg_id,
            "author": {
                "id": "test_guild_user",
                "username": "频道成员",
                "avatar": "",
            },
            "guild_id": "test_guild_id",
            "channel_id": "test_channel_id",
            "content": text,
            "timestamp": int(time.time() * 1000),
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Simulate a QQ bot message event for local testing."
    )
    parser.add_argument(
        "text", nargs="?",
        default="帮我查一下2024年以来人机协同的论文",
    )
    parser.add_argument(
        "--type", choices=["direct", "group", "guild"],
        default="direct",
        help="Message event type (default: direct)",
    )
    parser.add_argument(
        "--print-only", action="store_true",
        help="Print the simulated event JSON without processing",
    )
    args = parser.parse_args()

    builders = {
        "direct": build_direct_message,
        "group": build_group_message,
        "guild": build_guild_message,
    }
    event = builders[args.type](args.text)

    if args.print_only:
        print(json.dumps(event, ensure_ascii=False, indent=2))
        print()
        print("--- How QQ bot processes this event ---")
        print(f"  Event type: {event.get('t')}")
        d = event.get("d", {})
        author = d.get("author", {})
        user_id = author.get("id", "unknown")
        content = d.get("content", "")
        print(f"  User ID: {user_id}")
        print(f"  Content: {content}")
        print(f"  Session ID: qq_{user_id}")
        return

    print("QQ bot uses WebSocket, not webhook.")
    print("To test, start the gateway and the bot will connect automatically.")
    print()
    print("Simulated event payload:")
    print(json.dumps(event, ensure_ascii=False, indent=2))
    print()
    print("The bot would:")
    print(f"  1. Receive event type: {event.get('t')}")
    print(f"  2. Parse message content")
    print(f"  3. Deduplicate via Redis (qq_event_seen:{event['d']['id']})")
    print(f"  4. Call agent.chat('qq_test_user_openid', '{args.text}', domain='auto')")
    print(f"  5. Reply via QQ HTTP API")


if __name__ == "__main__":
    main()
