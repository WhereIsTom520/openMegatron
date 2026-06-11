---
name: browser_automation
version: 1.0.0
category: agent
description: AI-controlled browser automation via Playwright — navigate, click, type, screenshot, extract content, fill forms, and search the web.
risk: medium
actions:
  - navigate
  - click
  - type_text
  - screenshot
  - extract_text
  - extract_links
  - search_extract
  - fill_form
  - scroll
  - wait
  - pdf
  - close
keywords:
  - browser
  - web
  - automate
  - playwright
  - screenshot
  - scrape
  - form
  - click
  - navigate
  - 浏览器
  - 自动化
  - 截图
  - 填表
  - 搜索
parameters:
  action:
    type: string
    description: "Action to perform."
    enum: ["navigate", "click", "type_text", "screenshot", "extract_text", "extract_links", "search_extract", "fill_form", "scroll", "wait", "pdf", "close"]
    required: true
  url:
    type: string
    description: "URL to navigate to."
  selector:
    type: string
    description: "CSS selector for element to interact with."
  text:
    type: string
    description: "Text to type or search for."
  form_fields:
    type: object
    description: "Dict of CSS selector → value for fill_form action."
  headless:
    type: boolean
    description: "Run browser in headless mode. Default true."
    default: true
  screenshot_path:
    type: string
    description: "Output path for screenshot (PNG)."
  wait_ms:
    type: integer
    description: "Wait time in milliseconds."
    default: 1000
  extract_limit:
    type: integer
    description: "Max items to extract. Default 20."
    default: 20
produces:
  stdout: JSON with status, results, and optional base64 screenshot.
side_effects:
  - Launches Chromium browser via Playwright.
  - Reads/writes files for screenshots and PDFs.
---

# Browser Automation v1.0.0

AI-controlled browser via Playwright Chromium. Navigate, click, type, extract,
screenshot, fill forms, and search the web — all from the agent.

## Actions

### Navigation
- **navigate** `<url>` — Open a URL. Returns page title and final URL.
- **scroll** `[direction=down]` `[amount=500]` — Scroll the page.
- **wait** `[ms=1000]` — Wait for a specified time (for dynamic content to load).

### Interaction
- **click** `<selector>` — Click an element by CSS selector.
- **type_text** `<selector> <text>` — Type text into an input field.
- **fill_form** `<{selector: value, ...}>` — Fill multiple form fields at once.

### Extraction
- **extract_text** `[selector=body]` — Extract text content from page/element.
- **extract_links** `[limit=20]` — Extract all links from the page.
- **search_extract** `<search_query>` — Type into a search box, submit, and extract results.

### Capture
- **screenshot** `[path]` — Take a full-page screenshot.
- **pdf** `[path]` — Save page as PDF.

### Lifecycle
- **close** — Close the browser.

## Requirements

- Playwright Chromium must be installed (`playwright install chromium`)
- The browser runs locally — no external services
- Headless mode by default; set `headless: false` to see the browser

## Examples

```
# Search and extract results
→ browser_automation search_extract "transformer attention mechanism 2024"

# Navigate and screenshot
→ browser_automation navigate "https://arxiv.org/abs/1706.03762"
→ browser_automation screenshot "paper.png"

# Fill a form
→ browser_automation navigate "https://example.com/form"
→ browser_automation fill_form {"#name": "Alice", "#email": "alice@example.com"}
→ browser_automation click "button[type=submit]"
```
