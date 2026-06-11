from __future__ import annotations

import json
import sys
from pathlib import Path

PYSRC = str(Path(__file__).resolve().parents[3])
if PYSRC not in sys.path:
    sys.path.insert(0, PYSRC)

from cross_category_learner import CrossCategoryLearner


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


def main() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    if not action:
        print(json.dumps({"status": "error", "error": "Missing action."}, ensure_ascii=False))
        return 2

    learner = CrossCategoryLearner()

    try:
        if action == "stats":
            failures = getattr(learner, "_failures", [])
            patterns = learner.get_patterns() if hasattr(learner, "get_patterns") else []
            category_counts = {}
            for f in failures:
                cat = f.get("category", "unknown") if isinstance(f, dict) else "unknown"
                category_counts[cat] = category_counts.get(cat, 0) + 1
            print(json.dumps({
                "status": "success",
                "action": "stats",
                "total_failures": len(failures),
                "categories": category_counts,
                "patterns_count": len(patterns),
                "patterns": [{"id": p.pattern_id, "source": p.source_category,
                              "applicable": p.applicable_categories,
                              "issue": p.issue_category} for p in patterns],
            }, ensure_ascii=False, indent=2))
            return 0

        if action == "patterns":
            patterns = learner.get_patterns() if hasattr(learner, "get_patterns") else []
            print(json.dumps({
                "status": "success",
                "action": "patterns",
                "count": len(patterns),
                "patterns": [{"id": p.pattern_id, "source": p.source_category,
                              "applicable": p.applicable_categories,
                              "issue": p.issue_category} for p in patterns],
            }, ensure_ascii=False, indent=2))
            return 0

        if action == "aggregate":
            if hasattr(learner, "aggregate"):
                learner.aggregate()
            print(json.dumps({"status": "success", "action": "aggregate"}, ensure_ascii=False))
            return 0

        print(json.dumps({"status": "error", "error": f"Unknown action: {action}"}, ensure_ascii=False))
        return 2

    except Exception as e:
        print(json.dumps({"status": "error", "action": action, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
