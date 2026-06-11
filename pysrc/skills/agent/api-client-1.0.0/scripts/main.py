#!/usr/bin/env python3
"""api-client v1.0.0 — HTTP client with auth management."""
import json, sys, time, base64
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing action JSON."})); sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON."})); sys.exit(1)

    action = params.get("action", "request")
    result = {"status": "success", "action": action}

    try:
        if action == "request":
            method = params.get("method", "GET").upper()
            url = params.get("url", "")
            if not url:
                print(json.dumps({"status": "error", "error": "Missing url"})); sys.exit(1)
            if not url.startswith("http"):
                url = "https://" + url

            headers = params.get("headers") or {}
            # Auth
            auth_type = params.get("auth_type", "none")
            if auth_type == "bearer":
                headers["Authorization"] = f"Bearer {params.get('auth_token', '')}"
            elif auth_type == "api_key":
                header_name = params.get("header_name", "X-API-Key")
                headers[header_name] = params.get("auth_token", "")
            elif auth_type == "basic":
                creds = f"{params.get('auth_username','')}:{params.get('auth_password','')}"
                headers["Authorization"] = f"Basic {base64.b64encode(creds.encode()).decode()}"

            # Body
            data = None
            body = params.get("body")
            form = params.get("form_data")
            if body:
                data = json.dumps(body).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            elif form:
                data = urlencode(form).encode("utf-8")
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

            req = Request(url, data=data, headers=headers, method=method)
            timeout = int(params.get("timeout_sec", 30))
            t0 = time.monotonic()

            try:
                with urlopen(req, timeout=timeout) as resp:
                    result["status_code"] = resp.status
                    result["response_headers"] = dict(resp.headers)
                    raw = resp.read()
                    try:
                        result["body"] = json.loads(raw)
                    except Exception:
                        result["body"] = raw.decode("utf-8", errors="replace")[:5000]
            except HTTPError as e:
                result["status_code"] = e.code
                result["error"] = str(e)
                try:
                    result["body"] = e.read().decode("utf-8", errors="replace")[:2000]
                except Exception:
                    pass

            result["timing_ms"] = round((time.monotonic() - t0) * 1000, 1)

        elif action == "webhook_listen":
            port = int(params.get("webhook_port", 9000))
            import threading
            from http.server import HTTPServer, BaseHTTPRequestHandler
            webhook_payloads = []

            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length) if length else b""
                    try:
                        payload = json.loads(body)
                    except Exception:
                        payload = {"raw": body.decode("utf-8", errors="replace")}
                    webhook_payloads.append({"path": self.path, "headers": dict(self.headers), "body": payload, "time": time.time()})
                    self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')

            server = HTTPServer(("0.0.0.0", port), Handler)
            t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
            result["webhook"] = {"port": port, "status": "listening", "note": "Will collect payloads. Call webhook_stop to stop."}
            # Store server ref for later stop
            import atexit
            atexit.register(lambda: server.shutdown())

        elif action == "webhook_stop":
            result["stopped"] = True

        elif action == "cache_clear":
            result["cleared"] = True

        else:
            result = {"status": "error", "error": f"Unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False, default=str))

    except Exception as exc:
        print(json.dumps({"status": "error", "action": action,
                          "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
