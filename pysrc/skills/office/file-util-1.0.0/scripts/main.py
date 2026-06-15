from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tarfile
import zipfile
from datetime import datetime
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


def file_hash(file_path: str, algorithm: str = "sha256") -> str:
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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
    dry_run = args.get("dry_run", False)
    recursive = args.get("recursive", False)

    try:
        if action == "rename":
            pattern = args.get("pattern", "")
            prefix = args.get("prefix", "")
            suffix = args.get("suffix", "")
            if not pattern and not prefix and not suffix:
                print(json.dumps({"status": "error", "error": "Provide pattern, prefix, or suffix for rename."}, ensure_ascii=False))
                return 2
            root = Path(input_path)
            files = list(root.iterdir())
            if recursive:
                files = list(root.rglob("*"))
            renamed = []
            counter = 1
            for fp in sorted(files):
                if not fp.is_file():
                    continue
                ext = fp.suffix
                date_str = datetime.fromtimestamp(fp.stat().st_mtime).strftime("%Y%m%d")
                new_name = pattern.format(n=counter, date=date_str, ext=ext)
                new_name = prefix + new_name + suffix
                if not new_name.endswith(ext):
                    new_name += ext
                new_path = fp.parent / new_name
                if dry_run:
                    renamed.append({"from": str(fp), "to": str(new_path)})
                else:
                    if new_path.exists() and not overwrite:
                        continue
                    fp.rename(new_path)
                    renamed.append({"from": str(fp), "to": str(new_path)})
                counter += 1
            print(json.dumps({"status": "success", "action": "rename", "dry_run": dry_run,
                              "renamed": len(renamed), "files": renamed}, ensure_ascii=False, indent=2))
            return 0

        if action == "organize":
            root = Path(input_path)
            out_root = Path(output) if output else root
            out_root.mkdir(parents=True, exist_ok=True)
            files = list(root.iterdir())
            if recursive:
                files = list(root.rglob("*"))
            organized = []
            for fp in sorted(files):
                if not fp.is_file():
                    continue
                ext = fp.suffix.lstrip(".").lower() or "no_extension"
                ext_dir = out_root / ext.upper()
                ext_dir.mkdir(exist_ok=True)
                target = ext_dir / fp.name
                if dry_run:
                    organized.append({"from": str(fp), "to": str(target)})
                else:
                    if target.exists() and not overwrite:
                        continue
                    shutil.move(str(fp), str(target))
                    organized.append({"from": str(fp), "to": str(target)})
            print(json.dumps({"status": "success", "action": "organize", "dry_run": dry_run,
                              "organized": len(organized), "files": organized}, ensure_ascii=False, indent=2))
            return 0

        if action == "deduplicate":
            root = Path(input_path)
            files = list(root.iterdir())
            if recursive:
                files = list(root.rglob("*"))
            mode = args.get("dedup_mode", "content")
            seen = set()
            duplicates = []
            for fp in sorted(files):
                if not fp.is_file():
                    continue
                if mode == "content":
                    key = file_hash(str(fp))
                else:
                    key = fp.name.lower()
                if key in seen:
                    duplicates.append({"file": str(fp), "duplicate_of": f"hash:{key}"})
                    if not dry_run:
                        fp.unlink()
                else:
                    seen.add(key)
            print(json.dumps({"status": "success", "action": "deduplicate", "dry_run": dry_run,
                              "total_scanned": len(files),
                              "duplicates_found": len(duplicates),
                              "duplicates": duplicates}, ensure_ascii=False, indent=2))
            return 0

        if action == "tree":
            root = Path(input_path)
            max_depth = args.get("max_depth", 4)
            path_only = args.get("path_only", False)
            lines = []
            def walk(dir_path, depth=0):
                if depth > max_depth:
                    return
                prefix = "  " * depth + ("└── " if depth > 0 else "")
                for entry in sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
                    if entry.name.startswith("."):
                        continue
                    if path_only:
                        lines.append(str(entry))
                    else:
                        size = entry.stat().st_size if entry.is_file() else 0
                        size_str = f"({size:,} B)" if entry.is_file() else "(dir)"
                        lines.append(f"{prefix}{entry.name} {size_str}")
                    if entry.is_dir():
                        walk(entry, depth + 1)
            walk(root)
            output_text = "\n".join(lines)
            if output:
                Path(output).write_text(output_text, encoding="utf-8")
                print(json.dumps({"status": "success", "action": "tree", "output": str(Path(output).resolve()),
                                  "entries": len(lines)}, ensure_ascii=False, indent=2))
            else:
                print(json.dumps({"status": "success", "action": "tree",
                                  "entries": len(lines), "tree": output_text}, ensure_ascii=False, indent=2))
            return 0

        if action == "archive":
            root = Path(input_path)
            fmt = args.get("archive_format", "zip")
            if not output:
                output = str(root.parent / f"{root.name}.{fmt.replace('tar.gz', 'tar.gz')}")
            out_path = Path(output)
            if out_path.exists() and not overwrite:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            if fmt == "zip":
                with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as zf:
                    for fp in root.rglob("*") if recursive else root.iterdir():
                        if fp.is_file():
                            zf.write(str(fp), str(fp.relative_to(root.parent)))
            elif fmt == "tar.gz":
                with tarfile.open(str(out_path), "w:gz") as tf:
                    for fp in root.rglob("*") if recursive else root.iterdir():
                        if fp.is_file():
                            tf.add(str(fp), str(fp.relative_to(root.parent)))
            print(json.dumps({"status": "success", "action": "archive",
                              "output": str(out_path.resolve()), "format": fmt}, ensure_ascii=False, indent=2))
            return 0

        if action == "unarchive":
            src = Path(input_path)
            out_dir = Path(output) if output else src.parent / src.stem
            if out_dir.exists() and not overwrite:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_dir}"}, ensure_ascii=False))
                return 2
            out_dir.mkdir(parents=True, exist_ok=True)
            if src.suffix == ".zip" or input_path.endswith(".zip"):
                with zipfile.ZipFile(str(src), "r") as zf:
                    zf.extractall(str(out_dir))
            elif input_path.endswith((".tar.gz", ".tgz")):
                with tarfile.open(str(src), "r:gz") as tf:
                    tf.extractall(str(out_dir))
            elif input_path.endswith(".tar"):
                with tarfile.open(str(src), "r:") as tf:
                    tf.extractall(str(out_dir))
            else:
                print(json.dumps({"status": "error", "error": f"Unsupported archive format: {src.suffix}"}, ensure_ascii=False))
                return 2
            print(json.dumps({"status": "success", "action": "unarchive",
                              "output": str(out_dir.resolve())}, ensure_ascii=False, indent=2))
            return 0

        print(json.dumps({"status": "error", "error": f"Unknown action: {action}"}, ensure_ascii=False))
        return 2

    except Exception as e:
        print(json.dumps({"status": "error", "action": action, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
