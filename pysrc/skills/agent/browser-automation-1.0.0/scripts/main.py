#!/usr/bin/env python3
"""browser-automation v1.0.0 — Playwright-powered browser control."""

from __future__ import annotations
import json, sys, base64, time
from pathlib import Path

def _get_browser():
    """Lazy-init Playwright browser."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "Playwright not installed. Run: pip install playwright && playwright install chromium"

    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    return (playwright, browser, context, page), None


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Missing action JSON parameter."}))
        sys.exit(1)

    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON parameter."}))
        sys.exit(1)

    action = params.get("action", "")
    if not action:
        print(json.dumps({"error": "Missing action."}))
        sys.exit(1)

    # Lazy browser init
    browser_tuple, err = _get_browser()
    if err:
        print(json.dumps({"status": "error", "error": err}))
        sys.exit(1)

    playwright, browser, context, page = browser_tuple

    try:
        result = {"status": "success", "action": action}

        # ── Navigation ────────────────────────────
        if action == "navigate":
            url = params.get("url", "")
            if not url:
                result = {"status": "error", "error": "Missing url"}
            else:
                if not url.startswith("http"):
                    url = "https://" + url
                page.goto(url, wait_until="networkidle", timeout=30000)
                result["url"] = page.url
                result["title"] = page.title()

        elif action == "close":
            browser.close()
            playwright.stop()
            result["closed"] = True
            print(json.dumps(result, ensure_ascii=False))
            return

        # ── Interaction ───────────────────────────
        elif action == "click":
            sel = params.get("selector", "")
            if not sel:
                result = {"status": "error", "error": "Missing selector"}
            else:
                page.click(sel, timeout=10000)
                result["clicked"] = sel

        elif action == "type_text":
            sel = params.get("selector", "")
            text = params.get("text", "")
            if not sel:
                result = {"status": "error", "error": "Missing selector"}
            else:
                page.fill(sel, text, timeout=10000)
                result["typed"] = {"selector": sel, "text": text[:50]}

        elif action == "fill_form":
            fields = params.get("form_fields") or params.get("fields") or {}
            if not fields:
                result = {"status": "error", "error": "Missing form_fields"}
            else:
                for sel, value in fields.items():
                    page.fill(sel, str(value), timeout=5000)
                result["filled"] = list(fields.keys())

        # ── Extraction ────────────────────────────
        elif action == "extract_text":
            sel = params.get("selector", "body")
            limit = int(params.get("extract_limit", 20))
            try:
                el = page.locator(sel).first
                text = el.inner_text(timeout=5000)
                result["text"] = text[:limit * 200]
                result["length"] = len(text)
            except Exception as e:
                result = {"status": "error", "error": f"extract_text failed: {e}"}

        elif action == "extract_links":
            limit = int(params.get("extract_limit", 20))
            links = page.evaluate("""(limit) => {
                const anchors = document.querySelectorAll('a[href]');
                const results = [];
                for (let i = 0; i < Math.min(anchors.length, limit); i++) {
                    results.push({
                        text: (anchors[i].textContent || '').trim().substring(0, 100),
                        href: anchors[i].href
                    });
                }
                return results;
            }""", limit)
            result["links"] = links
            result["count"] = len(links)

        elif action == "search_extract":
            query = params.get("text") or params.get("query", "")
            if not query:
                result = {"status": "error", "error": "Missing search query"}
            else:
                # Go to a search engine and search
                search_url = f"https://www.google.com/search?q={query}"
                page.goto(search_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(1500)
                # Extract search results
                try:
                    results = page.evaluate("""() => {
                        const items = document.querySelectorAll('h3');
                        const snippets = document.querySelectorAll('.VwiC3b');
                        const out = [];
                        for (let i = 0; i < Math.min(items.length, 10); i++) {
                            out.push({
                                title: (items[i].textContent || '').trim(),
                                snippet: snippets[i] ? (snippets[i].textContent || '').trim().substring(0, 200) : ''
                            });
                        }
                        return out;
                    }""")
                    result["results"] = results
                    result["count"] = len(results)
                except Exception as e:
                    result["results"] = []
                    result["count"] = 0
                    result["note"] = f"Search result extraction limited: {e}"

        # ── Capture ───────────────────────────────
        elif action == "screenshot":
            out_path = params.get("screenshot_path") or params.get("path", "")
            if out_path:
                page.screenshot(path=out_path, full_page=True)
                result["screenshot_path"] = out_path
            else:
                screenshot_bytes = page.screenshot(full_page=True)
                result["screenshot_base64"] = base64.b64encode(screenshot_bytes).decode("ascii")
                result["size_bytes"] = len(screenshot_bytes)

        elif action == "pdf":
            out_path = params.get("path") or "output.pdf"
            page.pdf(path=out_path)
            result["pdf_path"] = out_path

        # ── Scroll & Wait ─────────────────────────
        elif action == "scroll":
            direction = params.get("direction", "down")
            amount = int(params.get("amount", 500))
            if direction == "down":
                page.evaluate(f"window.scrollBy(0, {amount})")
            elif direction == "up":
                page.evaluate(f"window.scrollBy(0, -{amount})")
            result["scrolled"] = f"{direction} {amount}px"

        elif action == "wait":
            ms = int(params.get("wait_ms", 1000))
            page.wait_for_timeout(ms)
            result["waited_ms"] = ms

        else:
            result = {"status": "error", "error": f"Unknown action: {action}"}

        print(json.dumps(result, ensure_ascii=False))

    except Exception as exc:
        print(json.dumps({"status": "error", "action": action,
                          "error": f"{exc.__class__.__name__}: {exc}"}, ensure_ascii=False))
    finally:
        try:
            context.close()
            browser.close()
            playwright.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
