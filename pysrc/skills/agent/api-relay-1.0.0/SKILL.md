---
name: api_relay
version: 1.0.0
category: agent
description: AI API relay gateway client — connect to one-api/new-api/CliRelay relay services for unified multi-model access. Supports key management, model listing, channel status, and health checks.
risk: medium
actions:
  - list_models
  - check_channel
  - test_key
  - key_status
  - health
  - relay_request
keywords: [api, relay, proxy, one-api, new-api, gateway, 中转, 中转站, key, model, channel, 渠道]
parameters:
  action:
    type: string
    enum: [list_models, check_channel, test_key, key_status, health, relay_request]
    required: true
  relay_url:
    type: string
    description: Base URL of the relay service (e.g. https://relay.example.com).
  api_key:
    type: string
    description: API key for the relay service.
  channel_id:
    type: integer
    description: Channel ID for channel-specific checks.
  model_name:
    type: string
    description: Model name to test or query.
  prompt:
    type: string
    description: Test prompt for relay_request action. Default "Hello".
    default: "Hello"
produces:
  stdout: JSON with relay service response data.
side_effects:
  - Makes outbound HTTP requests to the relay service.
  - May consume API quota when testing keys.
risk: medium
---

# API Relay Gateway Client v1.0.0

Connect to one-api / new-api / CliRelay / ds2api style AI relay gateways.
Provides unified interface for key management, model discovery, and health checks.

## Supported Relay Services

| Service | GitHub | Features |
|---------|--------|----------|
| one-api | songquanpeng/one-api | 34k+ stars, original relay gateway |
| new-api | Calcium-Ion/new-api | one-api successor with monitoring |
| CliRelay | kittors/CliRelay | CLI-to-API bridge for External Agent JSONL |
| ds2api | CJackHwang/ds2api | DeepSeek protocol bridge |

## Actions

- **list_models** `<relay_url> [api_key]` — List all available models on the relay.
  Uses `/v1/models` endpoint (OpenAI-compatible).
  
- **check_channel** `<relay_url> <api_key> [channel_id]` — Check channel status and balance.
  Uses one-api admin API `/api/channel/`.
  
- **test_key** `<relay_url> <api_key> [model_name]` — Test an API key by making a minimal chat request.
  
- **key_status** `<relay_url> <api_key>` — Query key quota, usage, and expiration info.
  
- **health** `<relay_url>` — Basic connectivity check to the relay service.
  
- **relay_request** `<relay_url> <api_key> <model_name> [prompt]` — Send a chat request through the relay.

## Examples

```
→ api_relay health "https://relay.example.com"
→ api_relay list_models "https://relay.example.com" api_key="sk-xxx"
→ api_relay test_key "https://relay.example.com" api_key="sk-xxx" model_name="gpt-4o-mini"
→ api_relay key_status "https://relay.example.com" api_key="sk-xxx"
→ api_relay check_channel "https://relay.example.com" api_key="admin-key" channel_id=1
```
