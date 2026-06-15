from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path


def parse_cli_args() -> dict:
    if len(sys.argv) <= 1:
        return {}
    raw = sys.argv[1]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def detect_encoding(path: str) -> str:
    """Detect text file encoding using BOM markers or common heuristics."""
    with open(path, "rb") as f:
        raw = f.read(1024 * 64)
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    # Try utf-8 first
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    # Try gbk (common Chinese encoding)
    try:
        raw.decode("gbk")
        return "gbk"
    except UnicodeDecodeError:
        pass
    # Fallback to latin-1 (never fails)
    return "latin-1"


def main() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    input_path = args.get("input", "")
    if not action or not input_path:
        print(json.dumps({"status": "error", "error": "Missing required 'action' or 'input'."}, ensure_ascii=False))
        return 2

    input_path = str(Path(input_path).expanduser())
    output = str(Path(args["output"]).expanduser()) if args.get("output") else ""
    overwrite = args.get("overwrite", False)
    ignore_case = args.get("ignore_case", False)
    context = args.get("context", 0)

    try:
        if action == "diff":
            input2 = args.get("input2", "")
            if not input2:
                print(json.dumps({"status": "error", "error": "Missing 'input2' for diff."}, ensure_ascii=False))
                return 2
            input2 = str(Path(input2).expanduser())
            enc1 = detect_encoding(input_path)
            enc2 = detect_encoding(input2)
            text1 = Path(input_path).read_text(encoding=enc1).splitlines(keepends=True)
            text2 = Path(input2).read_text(encoding=enc2).splitlines(keepends=True)
            diff_lines = list(difflib.unified_diff(
                text1, text2,
                fromfile=input_path, tofile=input2,
                n=context or 3,
            ))
            diff_text = "".join(diff_lines)
            if output:
                Path(output).write_text(diff_text, encoding="utf-8")
                print(json.dumps({"status": "success", "action": "diff", "output": str(Path(output).resolve()),
                                  "lines": len(diff_lines)}, ensure_ascii=False, indent=2))
            else:
                print(json.dumps({"status": "success", "action": "diff", "diff": diff_text,
                                  "lines": len(diff_lines)}, ensure_ascii=False, indent=2))
            return 0

        if action == "grep":
            pattern = args.get("pattern", "")
            if not pattern:
                print(json.dumps({"status": "error", "error": "Missing 'pattern' for grep."}, ensure_ascii=False))
                return 2
            enc = detect_encoding(input_path)
            text = Path(input_path).read_text(encoding=enc).splitlines()
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
            matches = []
            for i, line in enumerate(text, 1):
                if regex.search(line):
                    matches.append({"line": i, "text": line})
            result = {"status": "success", "action": "grep", "match_count": len(matches), "matches": matches}
            if not output:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                out_data = "\n".join(f"{m['line']}:{m['text']}" for m in matches)
                Path(output).write_text(out_data, encoding="utf-8")
                result["output"] = str(Path(output).resolve())
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if action == "replace":
            pattern = args.get("pattern", "")
            replacement = args.get("replacement", "")
            if not pattern:
                print(json.dumps({"status": "error", "error": "Missing 'pattern' for replace."}, ensure_ascii=False))
                return 2
            enc = detect_encoding(input_path)
            text = Path(input_path).read_text(encoding=enc)
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
            new_text, count = regex.subn(replacement, text)
            out_path = Path(output) if output else Path(input_path)
            # If in-place and not overwrite, check
            if str(out_path) == input_path and not overwrite:
                print(json.dumps({"status": "error", "error": "In-place replace requires overwrite=True."}, ensure_ascii=False))
                return 2
            out_path.write_text(new_text, encoding="utf-8")
            print(json.dumps({"status": "success", "action": "replace", "output": str(out_path.resolve()),
                              "replacements": count}, ensure_ascii=False, indent=2))
            return 0

        if action == "encoding":
            detected = detect_encoding(input_path)
            from_enc = args.get("from_encoding", detected)
            to_enc = args.get("to_encoding", "utf-8")
            with open(input_path, encoding=from_enc) as f:
                text = f.read()
            out_path = Path(output) if output else Path(input_path)
            out_path.write_text(text, encoding=to_enc)
            print(json.dumps({"status": "success", "action": "encoding", "output": str(out_path.resolve()),
                              "from": from_enc, "to": to_enc}, ensure_ascii=False, indent=2))
            return 0

        if action == "stats":
            enc = detect_encoding(input_path)
            text = Path(input_path).read_text(encoding=enc)
            lines = text.splitlines()
            words = len(text.split())
            chars = len(text)
            non_empty_lines = sum(1 for l in lines if l.strip())
            max_line_len = max((len(l) for l in lines), default=0)
            print(json.dumps({"status": "success", "action": "stats",
                              "file": input_path,
                              "lines": len(lines),
                              "non_empty_lines": non_empty_lines,
                              "words": words,
                              "characters": chars,
                              "max_line_length": max_line_len,
                              "encoding": enc}, ensure_ascii=False, indent=2))
            return 0

        if action == "split":
            enc = detect_encoding(input_path)
            text = Path(input_path).read_text(encoding=enc)
            lines = text.splitlines(keepends=True)
            chunk_lines = args.get("chunk_lines", 1000)
            base = Path(output) if output else Path(input_path).parent / (Path(input_path).stem + "_part")
            parts = []
            for i in range(0, len(lines), chunk_lines):
                chunk = lines[i:i + chunk_lines]
                part_path = Path(f"{base}_{i // chunk_lines + 1:03d}{Path(input_path).suffix or '.txt'}")
                part_path.write_text("".join(chunk), encoding="utf-8")
                parts.append(str(part_path))
            print(json.dumps({"status": "success", "action": "split",
                              "parts": len(parts), "files": parts}, ensure_ascii=False, indent=2))
            return 0

        if action == "merge":
            input2 = args.get("input2", "")
            if not input2:
                print(json.dumps({"status": "error", "error": "Missing 'input2' for merge."}, ensure_ascii=False))
                return 2
            enc1 = detect_encoding(input_path)
            enc2 = detect_encoding(input2)
            text1 = Path(input_path).read_text(encoding=enc1)
            text2 = Path(input2).read_text(encoding=enc2)
            merged = text1 + "\n" + text2
            out_path = Path(output) if output else Path(input_path).with_suffix(".merged.txt")
            if out_path.exists() and not overwrite:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            out_path.write_text(merged, encoding="utf-8")
            print(json.dumps({"status": "success", "action": "merge", "output": str(out_path.resolve())},
                             ensure_ascii=False, indent=2))
            return 0

        print(json.dumps({"status": "error", "error": f"Unknown action: {action}"}, ensure_ascii=False))
        return 2

    except Exception as e:
        print(json.dumps({"status": "error", "action": action, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
