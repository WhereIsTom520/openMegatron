# Feishu Quickstart

本文档整理 OpenMegatron 接入飞书机器人的实际操作路径，并做脱敏处理。所有真实
`App ID`、`App Secret`、`tenant_key`、`open_id`、`union_id`、`chat_id` 都不要写入仓库，
统一使用本地 `.env.feishu.local` 或环境变量保存。

## 当前接入状态

- 已创建飞书开放平台应用，应用类型使用智能体/机器人模板。
- 已启用机器人能力，并完成应用版本发布。
- 已走通长连接事件接收：用户私聊机器人发送 `ping` 后，本机可以收到事件。
- 已走通机器人回复：本机长连接 bridge 可调用飞书消息回复接口。
- 长连接 bridge 已支持优先转发到 `YuanGeAgent.chat()`；如果本地 LLM API Key 为空，则返回本地能力说明和配置提示。
- 已通过 OpenAPI 创建测试群，并将用户加入群聊。
- 已将本地密钥写入 `.env.feishu.local` 和 `pysrc/model.toml`，这两个文件已被 `.gitignore` 忽略。

## 涉及文件

```text
docs/feishu_integration.md              # 正式 Feishu HTTP 回调集成说明
docs/feishu_quickstart.md               # 本快速配置文档
pysrc/integrations/feishu_bot.py        # OpenMegatron Feishu 适配器
scripts/feishu_long_connection_echo.py  # 长连接 bridge；文件名保留 echo 是历史原因
scripts/feishu_echo_gateway.py          # HTTP 回调本地验证脚本
.env.feishu.local                       # 本地飞书密钥，脱敏且不入库
pysrc/model.toml                        # 本地运行配置，脱敏且不入库
```

## 1. 创建飞书应用

1. 打开飞书开放平台：

```text
https://open.feishu.cn/app
```

2. 新建应用，建议使用智能体/机器人模板。
3. 应用名称建议使用 ASCII 或简单中英文，避免控制台或脚本编码问题。
4. 在应用基础信息页获取：

```text
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxx
```

5. 不要把真实 secret 写入文档、提交信息、Issue 或 PR。

## 2. 配置机器人与权限

在飞书开放平台应用后台完成：

1. 启用机器人能力。
2. 发布应用版本，安装到当前企业/租户。
3. 添加并发布权限。建议最小权限如下：

```text
im:message
im:message.p2p_msg
im:message.group_at_msg
im:message:send_as_bot
im:chat
im:chat.members:write_only
```

说明：

- `im.message.receive_v1` 是事件订阅项，用于接收用户消息。
- `im:message:send_as_bot` 用于机器人主动发消息或回复消息。
- `im:chat`、`im:chat.members:write_only` 用于创建群、查询群、拉用户入群。
- 每次改权限后都要重新发布应用版本，否则 OpenAPI 可能返回无权限。

## 3. 选择接入方式

飞书智能体模板默认更适合长连接。本地开发阶段建议先用长连接，因为不需要公网 HTTPS
回调地址。

推荐顺序：

1. 本地开发：使用 `scripts/feishu_long_connection_echo.py` 长连接 bridge。
2. 完整智能体：配置 LLM API Key 后，bridge 会把消息转给 `YuanGeAgent.chat()`。
3. 生产部署：使用公网 HTTPS 回调，并配置 `POST /integrations/feishu/events`。

HTTP 回调地址格式：

```text
https://<your-public-host>/integrations/feishu/events
```

## 4. 本地脱敏配置

在项目根目录创建 `.env.feishu.local`：

```powershell
@'
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=replace_with_real_secret
FEISHU_REPLY_ENABLED=1
FEISHU_LANG=zh
FEISHU_PROACTIVE_RECEIVE_ID_TYPE=chat_id
FEISHU_AGENT_REPLY_TIMEOUT_SEC=120
FEISHU_TEST_CHAT_ID=oc_xxxxxxxxxxxxxxxx
FEISHU_USER_OPEN_ID=ou_xxxxxxxxxxxxxxxx
'@ | Set-Content -Path .env.feishu.local -Encoding utf8
```

可选：在 `pysrc/model.toml` 写入本地运行配置：

```toml
[integrations.feishu]
app_id = "cli_xxxxxxxxxxxxxxxx"
app_secret = "replace_with_real_secret"
api_base = "https://open.feishu.cn"
reply_enabled = true
proactive_receive_id_type = "chat_id"
lang = "zh"
```

完整智能体回复还需要配置当前 LLM provider 的 API Key。示例：

```toml
[llm]
active_provider = "openai"

[llm.openai]
api_key = "replace_with_real_llm_key"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
```

也可以使用环境变量，例如：

```powershell
$env:OPENAI_API_KEY="replace_with_real_llm_key"
```

确认这两个文件不会入库：

```powershell
git check-ignore .env.feishu.local pysrc/model.toml
```

## 5. 安装长连接依赖

```powershell
python -m pip install lark-oapi
```

如首次 import 较慢，属于正常现象。

## 6. 启动长连接 bridge

前台启动，便于观察日志：

```powershell
python scripts\feishu_long_connection_echo.py
```

后台启动：

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
Start-Process -FilePath python `
  -ArgumentList @('scripts\feishu_long_connection_echo.py') `
  -WorkingDirectory (Get-Location).Path `
  -WindowStyle Hidden `
  -RedirectStandardOutput '.runtime\feishu_long.out.log' `
  -RedirectStandardError '.runtime\feishu_long.err.log'
```

查看日志：

```powershell
Get-Content .runtime\feishu_long.out.log -Tail 80
Get-Content .runtime\feishu_long.err.log -Tail 120
Get-Content .runtime\feishu_events.jsonl -Tail 50
```

用户在飞书私聊机器人发送 `ping` 后，`.runtime/feishu_events.jsonl` 应出现类似脱敏记录：

```json
{
  "event_id": "<event_id>",
  "tenant_key": "<tenant_key>",
  "chat_id": "<chat_id>",
  "message_id": "<message_id>",
  "message_type": "text",
  "text": "ping",
  "open_id": "<user_open_id>",
  "union_id": "<user_union_id>"
}
```

如果已配置 LLM API Key，机器人会调用 OpenMegatron 正式 agent 回复。
如果未配置 LLM API Key，机器人会返回本地能力说明和配置提示，避免继续发送测试 echo 文案。

## 7. 创建测试群

先获取 `tenant_access_token`，再调用创建群接口。下面示例只展示结构，不包含真实密钥：

```powershell
$env:PYTHONIOENCODING='utf-8'
@'
import json, os, urllib.request
from pathlib import Path

for line in Path(".env.feishu.local").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k] = v

def req(method, url, payload=None, token=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

base = "https://open.feishu.cn"
token_data = req("POST", f"{base}/open-apis/auth/v3/tenant_access_token/internal", {
    "app_id": os.environ["FEISHU_APP_ID"],
    "app_secret": os.environ["FEISHU_APP_SECRET"],
})
token = token_data["tenant_access_token"]

chat = req("POST", f"{base}/open-apis/im/v1/chats", {
    "name": "OpenMegatron AI Test Group",
    "description": "OpenMegatron Feishu integration test group",
    "chat_mode": "group",
    "chat_type": "private",
}, token)

print(json.dumps(chat, ensure_ascii=False, indent=2))
'@ | python -
```

把返回的 `chat_id` 写入 `.env.feishu.local`：

```text
FEISHU_TEST_CHAT_ID=oc_xxxxxxxxxxxxxxxx
```

## 8. 获取用户 open_id

最简单方式：

1. 启动 `scripts/feishu_long_connection_echo.py`。
2. 用户私聊机器人发送任意文本，例如 `ping`。
3. 从 `.runtime/feishu_events.jsonl` 读取 `open_id`。
4. 写入 `.env.feishu.local`：

```text
FEISHU_USER_OPEN_ID=ou_xxxxxxxxxxxxxxxx
```

## 9. 将用户加入测试群

```powershell
$env:PYTHONIOENCODING='utf-8'
@'
import json, os, urllib.request
from pathlib import Path

for line in Path(".env.feishu.local").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k] = v

def req(method, url, payload=None, token=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

base = "https://open.feishu.cn"
token_data = req("POST", f"{base}/open-apis/auth/v3/tenant_access_token/internal", {
    "app_id": os.environ["FEISHU_APP_ID"],
    "app_secret": os.environ["FEISHU_APP_SECRET"],
})
token = token_data["tenant_access_token"]

result = req(
    "POST",
    f"{base}/open-apis/im/v1/chats/{os.environ['FEISHU_TEST_CHAT_ID']}/members?member_id_type=open_id",
    {"id_list": [os.environ["FEISHU_USER_OPEN_ID"]]},
    token,
)

print(json.dumps(result, ensure_ascii=False, indent=2))
'@ | python -
```

成功时返回 `code: 0`。如果飞书客户端暂时看不到群聊，刷新飞书或搜索群名：

```text
OpenMegatron AI Test Group
```

## 10. 主动发送群消息

```powershell
$env:PYTHONIOENCODING='utf-8'
@'
import json, os, urllib.request
from pathlib import Path

for line in Path(".env.feishu.local").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ[k] = v

def req(method, url, payload=None, token=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))

base = "https://open.feishu.cn"
token_data = req("POST", f"{base}/open-apis/auth/v3/tenant_access_token/internal", {
    "app_id": os.environ["FEISHU_APP_ID"],
    "app_secret": os.environ["FEISHU_APP_SECRET"],
})
token = token_data["tenant_access_token"]

message = req(
    "POST",
    f"{base}/open-apis/im/v1/messages?receive_id_type=chat_id",
    {
        "receive_id": os.environ["FEISHU_TEST_CHAT_ID"],
        "msg_type": "text",
        "content": json.dumps({"text": "OpenMegatron Feishu bot is connected."}, ensure_ascii=False),
    },
    token,
)

print(json.dumps(message, ensure_ascii=False, indent=2))
'@ | python -
```

成功时返回 `code: 0`，飞书群里会出现机器人消息。

## 11. 启用 OpenMegatron 正式回复

当前长连接脚本已经是 bridge，不再只是 echo 验证。它的行为是：

1. 读取 `.env.feishu.local` 中的飞书应用配置。
2. 接收飞书私聊或群聊消息。
3. 清理飞书 mention 标记，例如 `@_user_1`。
4. 读取 `pysrc/model.toml` 中的 LLM 配置。
5. 如果当前 provider 有 API Key，则调用 `YuanGeAgent.chat()`。
6. 如果当前 provider 没有 API Key，则返回本地能力说明和配置提示。

要启用完整智能体回复，请配置 `pysrc/model.toml`：

```toml
[llm]
active_provider = "openai"

[llm.openai]
api_key = "replace_with_real_llm_key"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
```

配置完成后重启长连接 bridge：

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
  Where-Object { $_.CommandLine -like '*feishu_long_connection_echo.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId }

Start-Process -FilePath python `
  -ArgumentList @('scripts\feishu_long_connection_echo.py') `
  -WorkingDirectory (Get-Location).Path `
  -WindowStyle Hidden `
  -RedirectStandardOutput '.runtime\feishu_long.out.log' `
  -RedirectStandardError '.runtime\feishu_long.err.log'
```

如果选择 HTTP 回调模式，则部署 OpenMegatron API，配置飞书事件订阅到
`POST /integrations/feishu/events`，由 `pysrc/integrations/feishu_bot.py` 处理事件。

启动 OpenMegatron HTTP 服务：

```powershell
python pysrc\agent.py --serve --host 0.0.0.0 --port 8080
```

本地 HTTP 验证可参考：

```powershell
python scripts\feishu_echo_gateway.py
```

## 常见问题

### 机器人私聊不回复

- 确认应用版本已发布并安装到当前企业。
- 确认长连接 bridge 正在运行。
- 查看 `.runtime/feishu_long.err.log` 是否有鉴权错误。
- 查看 `.runtime/feishu_events.jsonl` 是否有新事件。
- 确认已订阅 `im.message.receive_v1`。

### 一直回复测试文案

旧版验证脚本会回复类似：

```text
OpenMegatron is connected through Feishu long connection.
Next step: switch this bridge to the full OpenMegatron agent.
```

如果还看到这段话，说明旧进程没有停掉。停止旧进程并重启 bridge：

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
  Where-Object { $_.CommandLine -like '*feishu_long_connection_echo.py*' } |
  Select-Object ProcessId,CommandLine
```

确认后停止对应进程，再重新启动 `scripts\feishu_long_connection_echo.py`。

### 回复提示缺少 LLM API Key

- 飞书链路已通。
- 需要在 `pysrc/model.toml` 配置当前 provider 的 `api_key`。
- 或设置 `OPENAI_API_KEY` / `MEGATRON_<PROVIDER>_API_KEY` 后重启 bridge。

### 创建群或拉人返回无权限

- 添加 `im:chat` 和 `im:chat.members:write_only`。
- 重新发布应用版本。
- 重新获取 `tenant_access_token`。

### 群聊创建成功但用户看不到

- 群刚创建时可能只有机器人在群里。
- 需要先获取用户 `open_id`，再调用加群接口。
- 刷新飞书客户端，或搜索群名。

### 中文乱码

- PowerShell 管道脚本建议设置：

```powershell
$env:PYTHONIOENCODING='utf-8'
```

- 临时验证消息可先使用英文或 ASCII，待链路稳定后再恢复中文。

## 安全要求

- 不提交 `.env.feishu.local`、`pysrc/model.toml`、`.runtime/`。
- 不在文档、聊天、PR、Issue 中粘贴真实 `App Secret`。
- 如果 secret 已经在外部暴露，立即在飞书开放平台重置。
- 生产环境建议使用系统环境变量或密钥管理服务。
- 发布前检查：

```powershell
git status --short
git diff -- . ':!*.local' ':!pysrc/model.toml'
git check-ignore .env.feishu.local pysrc/model.toml .runtime/feishu_events.jsonl
```
