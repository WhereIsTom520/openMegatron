---
name: api_client
version: 1.0.0
category: agent
description: HTTP API client with auth management — GET/POST/PUT/DELETE, Bearer/API-Key/Basic auth, JSON/Form body, webhook receiver, and response caching.
risk: medium
actions:
  - request
  - webhook_listen
  - webhook_stop
  - cache_clear
keywords: [api, http, rest, graphql, webhook, request, get, post, auth, 接口, 请求]
parameters:
  action:
    type: string
    enum: [request, webhook_listen, webhook_stop, cache_clear]
    required: true
  method:
    type: string
    enum: [GET, POST, PUT, DELETE, PATCH, HEAD]
    default: GET
  url:
    type: string
    description: Request URL.
  headers:
    type: object
    description: Custom headers dict.
  body:
    type: object
    description: JSON body for POST/PUT/PATCH.
  form_data:
    type: object
    description: Form-encoded body.
  auth_type:
    type: string
    enum: [bearer, api_key, basic, none]
    default: none
  auth_token:
    type: string
    description: Bearer token or API key value.
  auth_username:
    type: string
    description: Username for Basic auth.
  auth_password:
    type: string
    description: Password for Basic auth.
  timeout_sec:
    type: integer
    description: Request timeout. Default 30.
    default: 30
  follow_redirects:
    type: boolean
    description: Follow HTTP redirects. Default true.
    default: true
  webhook_port:
    type: integer
    description: Port for webhook listener. Default 9000.
    default: 9000
produces:
  stdout: JSON with status code, headers, body, and timing.
side_effects:
  - Makes outbound HTTP requests to external services.
  - Can start a local webhook server.
risk: medium
---

# API Client v1.0.0

HTTP client for calling external APIs, receiving webhooks, and managing auth.

## Actions

- **request** `<method> <url> [body] [auth]` — Make an HTTP request. Supports JSON and form bodies, Bearer/API-Key/Basic auth, custom headers. Returns status, headers, body, timing.
- **webhook_listen** `[port=9000]` — Start a local webhook receiver. Returns received payloads as they arrive.
- **webhook_stop** — Stop the webhook server.
- **cache_clear** — Clear the response cache.

## Auth Types

| Type | Parameters |
|------|-----------|
| `bearer` | `auth_token` → `Authorization: Bearer <token>` |
| `api_key` | `auth_token` + `header_name` (default: `X-API-Key`) |
| `basic` | `auth_username` + `auth_password` |
| `none` | No auth header |

## Examples

```
→ api_client request GET "https://api.github.com/repos/openai/tiktoken"
→ api_client request POST "https://httpbin.org/post" body={"key":"val"} auth_type=bearer auth_token="xxx"
```
