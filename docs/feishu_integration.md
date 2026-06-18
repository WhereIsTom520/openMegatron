# Feishu Integration

OpenMegatron supports two Feishu/Lark receiving modes:

1. Long connection bridge for local development and Feishu agent-template apps.
2. HTTPS callback gateway for production deployments.

The production callback endpoint is:

```text
POST /integrations/feishu/events
```

## Capabilities

- URL verification returns the Feishu `challenge` value.
- Message events are mapped to stable sessions like `feishu_<tenant_key>_<chat_id>`.
- Duplicate event IDs are ignored for a short TTL to reduce retry duplicates.
- Text messages are sent to `agent.chat(..., domain="auto")`.
- The long-connection bridge forwards messages to OpenMegatron when an LLM provider API key is configured.
- If no LLM API key is configured, the long-connection bridge returns a local capability/configuration response instead of a test echo.
- Long structured answers can be sent as Feishu interactive cards.
- Tool execution confirmations are sent as interactive cards with Approve/Deny buttons.
- Local fallback confirmation commands are supported:

```text
/approve <request_id>
/deny <request_id>
```

Built-in slash commands:

```text
/help
/status
/clear
/retry
/search <query>
/skills
```

## Feishu App Setup

In the Feishu/Lark developer console:

1. Create or open a bot app.
2. Enable bot capability.
3. Choose an event receiving mode:

```text
Long connection: recommended for local development.
Callback URL: recommended for production.
```

4. For callback mode, configure event subscription callback URL:

```text
https://<your-public-host>/integrations/feishu/events
```

5. Subscribe to `im.message.receive_v1`.
6. Add bot permissions needed for your deployment, typically:

```text
im:message
im:message.group_at_msg
im:message.p2p_msg
im:message:send_as_bot
im:chat
im:chat.members:write_only
```

7. Publish or reinstall the app to the target tenant after changing permissions.

## Runtime Configuration

Prefer environment variables for secrets:

```powershell
$env:FEISHU_APP_ID="cli_xxx"
$env:FEISHU_APP_SECRET="xxx"
$env:FEISHU_VERIFICATION_TOKEN="xxx"
$env:FEISHU_ENCRYPT_KEY="xxx" # optional; only when callback encryption/signature is enabled
$env:FEISHU_REPLY_ENABLED="1"
$env:FEISHU_LANG="zh"
$env:FEISHU_AGENT_REPLY_TIMEOUT_SEC="120" # optional; long-connection bridge timeout
```

Full OpenMegatron replies require a configured LLM provider key in `pysrc/model.toml`
or the matching environment variable, for example `OPENAI_API_KEY` or
`MEGATRON_<PROVIDER>_API_KEY`.

Equivalent `config.toml` shape:

```toml
[integrations.feishu]
app_id = ""
app_secret = ""
verification_token = ""
encrypt_key = ""
api_base = "https://open.feishu.cn"
request_timeout_sec = 20
reply_enabled = false
confirmation_watch_sec = 65
max_retries = 3
retry_delay_sec = 1.0
enable_typing_indicator = true
enable_slash_commands = true
proactive_receive_id_type = "chat_id"
lang = "zh"
```

## Run Gateway

For local long-connection development:

```powershell
python scripts\feishu_long_connection_echo.py
```

The filename is historical. The script now behaves as a bridge:

- It logs Feishu events to `.runtime/feishu_events.jsonl`.
- It cleans Feishu mention markers such as `@_user_1`.
- It calls `YuanGeAgent.chat()` when an LLM key is configured.
- It returns a local capability/configuration response when the LLM key is missing.

For production HTTP callback mode:

```powershell
venv\Scripts\python.exe pysrc\agent.py --serve --host 0.0.0.0 --port 8080
```

For local dry-run testing without sending replies to Feishu:

```powershell
$env:FEISHU_REPLY_ENABLED="0"
venv\Scripts\python.exe pysrc\agent.py --serve --host 127.0.0.1 --port 8080
venv\Scripts\python.exe scripts\simulate_feishu_event.py "帮我查一下2024年以来人机协同的论文"
```

Print the simulated payload without sending it:

```powershell
venv\Scripts\python.exe scripts\simulate_feishu_event.py "hello" --print-only
```

## Production Checklist

- The callback URL is public and HTTPS.
- Feishu URL verification succeeds.
- `FEISHU_VERIFICATION_TOKEN` matches the app console.
- `FEISHU_ENCRYPT_KEY` is set if encryption/signature is enabled.
- Bot has message receive/send permissions and is installed in the tenant.
- `FEISHU_REPLY_ENABLED=1` is set only after dry-run succeeds.
- Send a direct message to the bot and verify the reply.
- Mention the bot in a group chat and verify the reply.
- Trigger a tool that requires confirmation and verify the Approve/Deny card.
