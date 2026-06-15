from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


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


def load_csv(file_path: str, delimiter: str = ",") -> list[dict]:
    with open(file_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        return [row for row in reader]


def write_csv(file_path: str, rows: list[dict], delimiter: str = ",") -> None:
    if not rows:
        return
    with open(file_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def evaluate_filter(row: dict, expr: str) -> bool:
    """Simple expression parser for filters like 'age>30' or 'name==John'."""
    expr = expr.strip()
    for op in (">=", "<=", "!=", "==", ">", "<"):
        if op in expr:
            col, val = expr.split(op, 1)
            col = col.strip()
            val = val.strip().strip("\"'")
            cell = row.get(col, "")
            # Try numeric comparison
            try:
                cell_num = float(cell)
                val_num = float(val)
                if op == ">=": return cell_num >= val_num
                if op == "<=": return cell_num <= val_num
                if op == "!=": return cell_num != val_num
                if op == "==": return cell_num == val_num
                if op == ">": return cell_num > val_num
                if op == "<": return cell_num < val_num
            except (ValueError, TypeError):
                pass
            # String comparison
            if op == ">=": return cell >= val
            if op == "<=": return cell <= val
            if op == "!=": return cell != val
            if op == "==": return cell == val
            if op == ">": return cell > val
            if op == "<": return cell < val
    return True


def main() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    input_path = args.get("input", "")
    if not action or not input_path:
        print(json.dumps({"status": "error", "error": "Missing required 'action' or 'input'."}, ensure_ascii=False))
        return 2

    input_path = str(Path(input_path).expanduser())
    output = str(Path(args["output"]).expanduser()) if args.get("output") else ""
    delimiter = args.get("delimiter", ",")
    overwrite = args.get("overwrite", False)

    try:
        if action == "csv2json":
            rows = load_csv(input_path, delimiter)
            out_path = output or str(Path(input_path).with_suffix(".json"))
            if Path(out_path).exists() and not overwrite:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            Path(out_path).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"status": "success", "action": "csv2json", "output": str(Path(out_path).resolve()),
                              "rows": len(rows)}, ensure_ascii=False, indent=2))
            return 0

        if action == "json2csv":
            data = json.loads(Path(input_path).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = [data]
            if not data:
                print(json.dumps({"status": "error", "error": "Empty JSON data"}, ensure_ascii=False))
                return 2
            out_path = output or str(Path(input_path).with_suffix(".csv"))
            if Path(out_path).exists() and not overwrite:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            write_csv(out_path, data, delimiter)
            print(json.dumps({"status": "success", "action": "json2csv", "output": str(Path(out_path).resolve()),
                              "rows": len(data)}, ensure_ascii=False, indent=2))
            return 0

        if action == "csv_transform":
            rows = load_csv(input_path, delimiter)
            # Filter
            filter_expr = args.get("filter", "")
            if filter_expr:
                rows = [r for r in rows if evaluate_filter(r, filter_expr)]
            # Select columns
            cols = args.get("columns", "")
            if cols:
                keep = [c.strip() for c in cols.split(",")]
                rows = [{k: r[k] for k in keep if k in r} for r in rows]
            # Sort
            sort_by = args.get("sort_by", "")
            if sort_by:
                reverse = args.get("sort_desc", False)
                rows.sort(key=lambda r: r.get(sort_by, ""), reverse=reverse)
            # Limit
            limit = args.get("limit", 0)
            if limit > 0:
                rows = rows[:limit]
            out_path = output or str(Path(input_path).with_suffix(".transformed.csv"))
            if Path(out_path).exists() and not overwrite:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            write_csv(out_path, rows, delimiter)
            print(json.dumps({"status": "success", "action": "csv_transform", "output": str(Path(out_path).resolve()),
                              "rows": len(rows)}, ensure_ascii=False, indent=2))
            return 0

        if action == "csv_merge":
            input2 = args.get("input2", "")
            if not input2:
                print(json.dumps({"status": "error", "error": "Missing 'input2' for merge."}, ensure_ascii=False))
                return 2
            rows1 = load_csv(input_path, delimiter)
            rows2 = load_csv(str(Path(input2).expanduser()), delimiter)
            merged = rows1 + rows2
            out_path = output or "merged.csv"
            if Path(out_path).exists() and not overwrite:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            write_csv(out_path, merged, delimiter)
            print(json.dumps({"status": "success", "action": "csv_merge", "output": str(Path(out_path).resolve()),
                              "rows": len(merged)}, ensure_ascii=False, indent=2))
            return 0

        if action in ("yaml2json", "json2yaml"):
            if not yaml:
                print(json.dumps({"status": "error", "error": "PyYAML not found. Install with: pip install pyyaml"}, ensure_ascii=False))
                return 2
            if action == "yaml2json":
                with open(input_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                out_path = output or str(Path(input_path).with_suffix(".json"))
                if Path(out_path).exists() and not overwrite:
                    print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                    return 2
                Path(out_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({"status": "success", "action": "yaml2json", "output": str(Path(out_path).resolve())},
                                 ensure_ascii=False, indent=2))
                return 0
            else:  # json2yaml
                data = json.loads(Path(input_path).read_text(encoding="utf-8"))
                out_path = output or str(Path(input_path).with_suffix(".yaml"))
                if Path(out_path).exists() and not overwrite:
                    print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                    return 2
                with open(out_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
                print(json.dumps({"status": "success", "action": "json2yaml", "output": str(Path(out_path).resolve())},
                                 ensure_ascii=False, indent=2))
                return 0

        if action == "validate_json":
            try:
                data = json.loads(Path(input_path).read_text(encoding="utf-8"))
                data_type = type(data).__name__
                size = len(data) if isinstance(data, (list, dict)) else 1
                print(json.dumps({"status": "success", "action": "validate_json", "valid": True,
                                  "type": data_type, "size": size}, ensure_ascii=False, indent=2))
                return 0
            except json.JSONDecodeError as e:
                print(json.dumps({"status": "success", "action": "validate_json", "valid": False,
                                  "error": str(e)}, ensure_ascii=False, indent=2))
                return 0

        print(json.dumps({"status": "error", "error": f"Unknown action: {action}"}, ensure_ascii=False))
        return 2

    except Exception as e:
        print(json.dumps({"status": "error", "action": action, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
