"""Create a redacted copy of a TOML configuration file.

The tool is intentionally conservative: it does not modify the source file and
redacts common secret fields recursively before writing a publishable copy.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

try:
    import tomli_w
except ImportError:  # pragma: no cover
    tomli_w = None


SECRET_KEYS = {
    "api_key",
    "app_secret",
    "authorization",
    "bot_token",
    "client_secret",
    "corp_secret",
    "encoding_aes_key",
    "encrypt_key",
    "password",
    "private_key",
    "secret",
    "shared_secret",
    "signing_secret",
    "token",
    "verification_token",
    "webhook_secret",
}

SENSITIVE_KEY_PARTS = ("api_key", "secret", "password", "private_key", "aes_key")
REDACTED = "<redacted>"
URI_CREDENTIAL_RE = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<auth>[^/@\s]+)@")


def is_sensitive_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    if lowered in SECRET_KEYS:
        return True
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return True
    return lowered == "token" or lowered.endswith("_token") or lowered.endswith("-token")


def scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: (REDACTED if is_sensitive_key(key) else scrub_value(val)) for key, val in value.items()}
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, str):
        return URI_CREDENTIAL_RE.sub(r"\g<scheme><redacted>@", value)
    return value


def _quote_toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_scalar(item) for item in value) + "]"
    return _quote_toml_string(str(value))


def _dump_toml_fallback(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def emit_table(prefix: list[str], table: dict[str, Any]) -> None:
        scalar_items = {k: v for k, v in table.items() if not isinstance(v, dict)}
        child_items = {k: v for k, v in table.items() if isinstance(v, dict)}
        if prefix:
            lines.append(f"[{'.'.join(prefix)}]")
        for key, value in scalar_items.items():
            lines.append(f"{key} = {_format_scalar(value)}")
        if scalar_items:
            lines.append("")
        for key, value in child_items.items():
            emit_table([*prefix, str(key)], value)

    emit_table([], data)
    return "\n".join(lines).rstrip() + "\n"


def dump_toml(data: dict[str, Any]) -> str:
    if tomli_w is not None:
        return tomli_w.dumps(data)
    return _dump_toml_fallback(data)


def scrub_toml_file(source: Path, target: Path) -> dict[str, Any]:
    with source.open("rb") as handle:
        data = tomllib.load(handle)
    scrubbed = scrub_value(copy.deepcopy(data))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_toml(scrubbed), encoding="utf-8")
    return {"source": str(source), "target": str(target), "status": "success"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a redacted TOML copy for release notes, examples, or support logs.")
    parser.add_argument("--input", "-i", default="pysrc/model.toml", help="Source TOML file.")
    parser.add_argument("--output", "-o", default=".runtime/model.redacted.toml", help="Redacted output TOML file.")
    args = parser.parse_args(argv)

    source = Path(args.input)
    if not source.is_file():
        print(f"error: source TOML not found: {source}", file=sys.stderr)
        return 2
    result = scrub_toml_file(source, Path(args.output))
    print(f"redacted TOML written: {result['target']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
