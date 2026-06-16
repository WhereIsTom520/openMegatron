import argparse
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path

import tomli_w

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


PROVIDERS = {
    "1": {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o4-mini"],
    },
    "2": {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "3": {
        "id": "qwen",
        "label": "Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long"],
    },
    "4": {
        "id": "moonshot",
        "label": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    },
    "5": {
        "id": "zhipu",
        "label": "Zhipu",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "models": ["glm-4-flash", "glm-4-plus", "glm-4-air"],
    },
    "6": {
        "id": "minimax",
        "label": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "model": "MiniMax-Text-01",
        "models": ["MiniMax-Text-01", "MiniMax-M1"],
    },
    "7": {
        "id": "stepfun",
        "label": "Stepfun",
        "base_url": "https://api.stepfun.com/v1",
        "model": "step-2-mini",
        "models": ["step-2-mini", "step-2-16k", "step-1-8k"],
    },
    "8": {
        "id": "siliconflow",
        "label": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1"],
    },
    "9": {
        "id": "openrouter",
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "models": ["openai/gpt-4o-mini", "qwen/qwen3-235b-a22b", "google/gemini-2.0-flash-001"],
    },
}


ZH_PROVIDER_NAMES = {
    "1": "OpenAI",
    "2": "DeepSeek",
    "3": "通义千问 / Qwen",
    "4": "Moonshot",
    "5": "智谱 / Zhipu",
    "6": "MiniMax",
    "7": "阶跃星辰 / Stepfun",
    "8": "硅基流动 / SiliconFlow",
    "9": "OpenRouter",
}


def read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as file:
        return tomllib.load(file)


def env_name(provider_id: str, suffix: str) -> str:
    return f"MEGATRON_{provider_id.upper()}_{suffix}"


def configured_value(llm: dict, provider_id: str, key: str, default: str = "") -> str:
    provider_cfg = llm.get(provider_id, {})
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
    return str(provider_cfg.get(key) or llm.get(key) or default)


def write_env_cmd(path: Path, api_key: str, base_url: str, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "@echo off",
        f'set "OPENAI_API_KEY={api_key}"',
        f'set "OPENAI_BASE_URL={base_url}"',
        f'set "OPENAI_MODEL={model}"',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def i18n(lang: str, zh: str, en: str) -> str:
    return zh if lang == "zh" else en


def normalize_base_url(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    if value.endswith("/chat/completions"):
        value = value[: -len("/chat/completions")]
    if value.endswith("/models"):
        value = value[: -len("/models")]
    return value


def parse_timeout() -> float:
    raw = os.environ.get("MEGATRON_LLM_CHECK_TIMEOUT", "15").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 15.0


def read_error_body(error: urllib.error.HTTPError) -> str:
    try:
        return error.read(2048).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def redact_secret(text: str, secret: str) -> str:
    if not text or not secret:
        return text
    redacted = text.replace(secret, "***")
    if len(secret) > 12:
        redacted = redacted.replace(f"{secret[:8]}...{secret[-4:]}", "***")
        redacted = redacted.replace(f"{secret[:8]}*****{secret[-4:]}", "***")
    return redacted


def extract_model_ids(payload: bytes) -> set[str]:
    try:
        data = json.loads(payload.decode("utf-8"))
    except Exception:
        return set()
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return set()
    model_ids = set()
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            model_ids.add(str(item["id"]))
        elif isinstance(item, str):
            model_ids.add(item)
    return model_ids


def validate_llm_api(provider_label: str, base_url: str, api_key: str, model: str, lang: str) -> bool:
    if os.environ.get("MEGATRON_SKIP_LLM_CHECK") == "1":
        print(i18n(lang, "[WARN] 已跳过大模型 API 连通性测试。", "[WARN] Skipped LLM API connectivity check."))
        return True

    base_url = normalize_base_url(base_url)
    if not base_url:
        print(i18n(lang, "[ERROR] 缺少 Base URL。", "[ERROR] Missing Base URL."))
        return False
    if not api_key:
        print(i18n(lang, "[ERROR] 缺少 API Key。", "[ERROR] Missing API key."))
        return False

    models_url = f"{base_url}/models"
    timeout = parse_timeout()
    print(i18n(lang, f"[INFO] 正在测试 {provider_label} API：GET {models_url}", f"[INFO] Testing {provider_label} API: GET {models_url}"))

    request = urllib.request.Request(
        models_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "openMegatron-startup-check/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(1024 * 1024)
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as error:
        body = read_error_body(error)
        if error.code in {401, 403}:
            message = i18n(lang, "API Key 无效、无权限或厂商不接受该密钥。", "API key is invalid, unauthorized, or rejected by the provider.")
        elif error.code == 404:
            message = i18n(lang, "Base URL 可能不正确，/models 端点不存在。", "Base URL may be wrong; /models endpoint was not found.")
        elif error.code == 429:
            message = i18n(lang, "接口限流或额度不足。", "Rate limit or quota error.")
        else:
            message = i18n(lang, "接口返回异常状态。", "API returned an unexpected status.")
        print(f"[ERROR] {message} HTTP {error.code}")
        if body:
            print(f"[ERROR] {redact_secret(body, api_key)[:500]}")
        print(i18n(lang, "可设置 MEGATRON_SKIP_LLM_CHECK=1 临时跳过启动校验。", "Set MEGATRON_SKIP_LLM_CHECK=1 to temporarily skip startup validation."))
        return False
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
        print(i18n(lang, f"[ERROR] 无法连接到大模型接口：{error}", f"[ERROR] Could not connect to LLM API: {error}"))
        print(i18n(lang, "请检查网络、Base URL、代理或厂商服务状态。", "Check network, Base URL, proxy settings, or provider service status."))
        print(i18n(lang, "可设置 MEGATRON_SKIP_LLM_CHECK=1 临时跳过启动校验。", "Set MEGATRON_SKIP_LLM_CHECK=1 to temporarily skip startup validation."))
        return False

    if not (200 <= int(status) < 300):
        print(i18n(lang, f"[ERROR] 大模型接口返回异常状态：HTTP {status}", f"[ERROR] LLM API returned unexpected status: HTTP {status}"))
        return False

    model_ids = extract_model_ids(body)
    if model_ids and model and model not in model_ids:
        warning = i18n(
            lang,
            f"[WARN] API 可连通，但 /models 返回结果中没有找到当前默认模型：{model}",
            f"[WARN] API is reachable, but /models did not list the current default model: {model}",
        )
        print(warning)
        if os.environ.get("MEGATRON_STRICT_MODEL_CHECK") == "1":
            print(i18n(lang, "[ERROR] 严格模型校验已开启，启动中止。", "[ERROR] Strict model validation is enabled; startup aborted."))
            return False

    print(i18n(lang, "[OK] 大模型 API 校验通过。", "[OK] LLM API validation passed."))
    return True


def prompt_provider(lang: str) -> dict:
    if lang == "zh":
        print("请选择模型厂商：")
        for choice, name in ZH_PROVIDER_NAMES.items():
            print(f"  {choice}. {name}")
        choice = ask("输入选项（1-9，默认 1）：") or "1"
    else:
        print("Select model provider:")
        for choice, provider in PROVIDERS.items():
            print(f"  {choice}. {provider['label']}")
        choice = ask("Enter option (1-9, default 1): ") or "1"
    return PROVIDERS.get(choice)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--toml", required=True)
    parser.add_argument("--lang", choices=["zh", "en"], default="en")
    parser.add_argument("--env-cmd", required=True)
    args = parser.parse_args()

    lang = args.lang
    toml_path = Path(args.toml)
    env_cmd_path = Path(args.env_cmd)
    data = read_toml(toml_path)
    llm = data.setdefault("llm", {})
    active_provider = str(llm.get("active_provider") or "openai")
    force_setup = os.environ.get("MEGATRON_FORCE_LLM_SETUP") == "1"

    active_api_key = (
        os.environ.get(env_name(active_provider, "API_KEY"))
        or os.environ.get("OPENAI_API_KEY")
        or configured_value(llm, active_provider, "api_key")
    )
    active_base_url = (
        os.environ.get(env_name(active_provider, "BASE_URL"))
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or configured_value(llm, active_provider, "base_url")
    )
    active_model = (
        os.environ.get(env_name(active_provider, "MODEL"))
        or os.environ.get("OPENAI_MODEL")
        or configured_value(llm, active_provider, "model", "gpt-4o-mini")
    )

    if active_api_key and not force_setup:
        provider_cfg = llm.get(active_provider, {})
        provider_label = provider_cfg.get("label") if isinstance(provider_cfg, dict) else active_provider
        provider_label = str(provider_label or active_provider)
        if not validate_llm_api(provider_label, active_base_url, active_api_key, active_model, lang):
            return 1
        write_env_cmd(env_cmd_path, active_api_key, active_base_url, active_model)
        if lang == "zh":
            print(f"[OK] 已加载大模型配置。默认模型：{active_model}")
        else:
            print(f"[OK] LLM config loaded. Model: {active_model}")
        return 0

    provider = prompt_provider(lang)
    if not provider:
        print("[ERROR] Invalid provider option." if lang != "zh" else "[错误] 无效的厂商选项。")
        return 1

    if lang == "zh":
        print(f"当前厂商：{provider['label']}")
        api_key = ask(f"请输入 {provider['label']} API 密钥：")
    else:
        print(f"Provider: {provider['label']}")
        api_key = ask(f"Enter {provider['label']} API key: ")
    if not api_key:
        print("[ERROR] Missing API key." if lang != "zh" else "[错误] 缺少 API 密钥。")
        return 1

    if lang == "zh":
        base_url = ask(f"接口地址（默认 {provider['base_url']}）：") or provider["base_url"]
        model = ask(f"默认模型（默认 {provider['model']}，聊天页可继续搜索切换）：") or provider["model"]
    else:
        base_url = ask(f"Base URL (default {provider['base_url']}): ") or provider["base_url"]
        model = ask(f"Default model (default {provider['model']}, searchable in chat): ") or provider["model"]

    if not validate_llm_api(provider["label"], base_url, api_key, model, lang):
        return 1

    provider_cfg = llm.setdefault(provider["id"], {})
    llm["active_provider"] = provider["id"]
    provider_cfg["label"] = provider["label"]
    provider_cfg["api_key"] = api_key
    provider_cfg["base_url"] = base_url
    provider_cfg["model"] = model
    provider_cfg["models"] = provider["models"]
    provider_cfg.setdefault("extra_params", {})

    toml_path.parent.mkdir(parents=True, exist_ok=True)
    with toml_path.open("wb") as file:
        tomli_w.dump(data, file)
    write_env_cmd(env_cmd_path, api_key, base_url, model)

    if lang == "zh":
        print(f"[OK] 已保存大模型配置。厂商：{provider['label']} 模型：{model}")
    else:
        print(f"[OK] LLM config saved. Provider: {provider['label']} Model: {model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
