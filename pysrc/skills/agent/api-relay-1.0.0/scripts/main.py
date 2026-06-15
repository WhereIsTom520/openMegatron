#!/usr/bin/env python3
"""api-relay v1.0.0 — AI API relay gateway client.

Supports one-api, new-api, CliRelay, ds2api, and other OpenAI-compatible
relay services.  Provides key management, model discovery, channel status,
and health-check operations.
"""
from __future__ import annotations

import json
import sys
import time
import base64
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


def _make_request(url: str, method: str = "GET", headers: dict = None,
                  body: dict = None, timeout: int = 30) -> dict:
    """Make an HTTP request and return (status, headers, body, timing)."""
    t0 = time.monotonic()
    req_headers = dict(headers or {})
    req_headers.setdefault("Content-Type", "application/json")
    req_headers.setdefault("Accept", "application/json")

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = Request(url, data=data, headers=req_headers, method=method)
    try:
        resp = urlopen(req, timeout=timeout)
        resp_body = resp.read().decode("utf-8", errors="replace")
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        try:
            resp_json = json.loads(resp_body)
        except json.JSONDecodeError:
            resp_json = {"_raw": resp_body[:2000]}
        return {
            "status_code": resp.status,
            "headers": dict(resp.headers),
            "body": resp_json,
            "elapsed_ms": elapsed,
        }
    except HTTPError as e:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        err_body = e.read().decode("utf-8", errors="replace")[:2000]
        try:
            err_json = json.loads(err_body)
        except json.JSONDecodeError:
            err_json = {"_raw": err_body}
        return {
            "status_code": e.code,
            "headers": dict(e.headers),
            "body": err_json,
            "elapsed_ms": elapsed,
            "error": f"HTTP {e.code}: {e.reason}",
        }
    except URLError as e:
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        return {
            "status_code": None,
            "body": {},
            "elapsed_ms": elapsed,
            "error": f"Connection failed: {e.reason}",
        }


def _normalize_url(raw: str) -> str:
    """Ensure URL has a scheme."""
    url = (raw or "").strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    return url


def health(relay_url: str) -> dict:
    """Basic connectivity check."""
    url = _normalize_url(relay_url)
    result = _make_request(url + "/v1/models", timeout=10)
    return {
        "action": "health",
        "reachable": result["status_code"] is not None,
        "status_code": result.get("status_code"),
        "elapsed_ms": result.get("elapsed_ms"),
        "detail": result.get("error") or "OK",
    }


def list_models(relay_url: str, api_key: str = "") -> dict:
    """List models via OpenAI-compatible /v1/models."""
    url = _normalize_url(relay_url) + "/v1/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    result = _make_request(url, headers=headers)
    models = []
    if isinstance(result.get("body"), dict):
        models = result["body"].get("data", [])
    return {
        "action": "list_models",
        "status_code": result.get("status_code"),
        "elapsed_ms": result.get("elapsed_ms"),
        "model_count": len(models),
        "models": [m.get("id", str(m)) for m in models[:100]],
        "error": result.get("error"),
    }


def test_key(relay_url: str, api_key: str, model_name: str = "gpt-4o-mini") -> dict:
    """Test an API key by making a minimal chat completion request."""
    url = _normalize_url(relay_url) + "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 5,
    }
    result = _make_request(url, method="POST", headers=headers, body=body)
    return {
        "action": "test_key",
        "model": model_name,
        "status_code": result.get("status_code"),
        "elapsed_ms": result.get("elapsed_ms"),
        "success": result.get("status_code") == 200,
        "response": (
            result["body"].get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(result.get("body"), dict) else ""
        ),
        "error": result.get("error"),
    }


def key_status(relay_url: str, api_key: str) -> dict:
    """Query key quota and status from the relay (one-api/new-api /api/tokens)."""
    url = _normalize_url(relay_url) + "/api/tokens"
    headers = {"Authorization": f"Bearer {api_key}"}
    result = _make_request(url, headers=headers, timeout=15)
    return {
        "action": "key_status",
        "status_code": result.get("status_code"),
        "elapsed_ms": result.get("elapsed_ms"),
        "data": result.get("body") if isinstance(result.get("body"), dict) else {},
        "error": result.get("error"),
    }


def check_channel(relay_url: str, api_key: str, channel_id: int = None) -> dict:
    """Check channel status using one-api admin API."""
    url = _normalize_url(relay_url)
    if channel_id is not None:
        url += f"/api/channel/{channel_id}"
    else:
        url += "/api/channel/"
    headers = {"Authorization": f"Bearer {api_key}"}
    result = _make_request(url, headers=headers, timeout=15)
    return {
        "action": "check_channel",
        "channel_id": channel_id,
        "status_code": result.get("status_code"),
        "elapsed_ms": result.get("elapsed_ms"),
        "data": result.get("body") if isinstance(result.get("body"), dict) else {},
        "error": result.get("error"),
    }


def relay_request(relay_url: str, api_key: str, model_name: str,
                  prompt: str = "Hello") -> dict:
    """Send a full chat completion through the relay."""
    url = _normalize_url(relay_url) + "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,
    }
    result = _make_request(url, method="POST", headers=headers, body=body, timeout=60)
    response_text = ""
    if isinstance(result.get("body"), dict):
        choices = result["body"].get("choices", [])
        if choices:
            response_text = choices[0].get("message", {}).get("content", "")
    return {
        "action": "relay_request",
        "model": model_name,
        "status_code": result.get("status_code"),
        "elapsed_ms": result.get("elapsed_ms"),
        "success": result.get("status_code") == 200,
        "response": response_text,
        "usage": result.get("body", {}).get("usage", {}) if isinstance(result.get("body"), dict) else {},
        "error": result.get("error"),
    }


ACTION_MAP = {
    "list_models": list_models,
    "check_channel": check_channel,
    "test_key": test_key,
    "key_status": key_status,
    "health": health,
    "relay_request": relay_request,
}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing action JSON."}))
        sys.exit(1)

    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON."}))
        sys.exit(1)

    action = params.get("action", "health")
    func = ACTION_MAP.get(action)
    if func is None:
        print(json.dumps({
            "status": "error",
            "error": f"Unknown action: {action}. Available: {list(ACTION_MAP)}",
        }))
        sys.exit(1)

    try:
        result = func(
            relay_url=params.get("relay_url", ""),
            api_key=params.get("api_key", ""),
            model_name=params.get("model_name", "gpt-4o-mini"),
            **(params.get("extra", {}) or {}),
        )
        # Inject channel_id if provided directly
        if action == "check_channel" and params.get("channel_id"):
            result["channel_id"] = params["channel_id"]
        if action in ("relay_request", "test_key") and params.get("prompt"):
            result["prompt"] = params["prompt"]
        result["status"] = "success"
        print(json.dumps(result, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
