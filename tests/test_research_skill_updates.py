import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SKILLS = ROOT / "pysrc" / "skills" / "research"
PREFLIGHT = RESEARCH_SKILLS / "preflight-check-1.0.0" / "scripts" / "main.py"
CHART = ROOT / "pysrc" / "skills" / "office" / "research-chart-1.0.0" / "scripts" / "main.py"

sys.path.insert(0, str(RESEARCH_SKILLS))
from research_common import annotate_entities_with_citations, lookup_entity_citation  # noqa: E402


class TestEntityCitationMatching(unittest.TestCase):
    def test_does_not_match_entity_inside_common_words(self):
        result = annotate_entities_with_citations(
            "This paper studies mathematical reasoning and vocabulary coverage."
        )
        self.assertEqual([], result["entities_found"])
        self.assertEqual(0.0, result["coverage"])

    def test_alias_match_is_deduplicated_by_canonical_entity(self):
        result = annotate_entities_with_citations("We evaluate the model on SuperGLUE.")
        self.assertEqual(1, result["entity_count"])
        self.assertEqual("glue", result["entities_found"][0]["canonical_name"])
        self.assertEqual("alias", result["entities_found"][0]["match_type"])

    def test_lookup_is_exact_alias_aware_without_fuzzy_substrings(self):
        self.assertIsNone(lookup_entity_citation("mathematical reasoning"))
        self.assertIsNotNone(lookup_entity_citation("C4"))


class TestResearchSkillScripts(unittest.TestCase):
    def test_preflight_stdout_is_json_object(self):
        payload = {
            "action": "check",
            "draft": (
                "\\title{Short Test Paper}"
                "\\begin{abstract}We describe a method and report a result.\\end{abstract}"
                "\\section{Introduction}TODO"
            ),
        }
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, str(PREFLIGHT), json.dumps(payload)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            env=env,
            check=True,
        )
        parsed = json.loads(proc.stdout)
        self.assertIsInstance(parsed, dict)
        self.assertEqual("success", parsed["status"])

    def test_research_chart_generates_svg(self):
        output = Path(tempfile.gettempdir()) / "openmegatron_research_chart_test.svg"
        if output.exists():
            output.unlink()
        payload = {
            "action": "compare",
            "data": [{"method": "A", "metric": 1.0}, {"method": "B", "metric": 2.0}],
            "output": str(output),
        }
        proc = subprocess.run(
            [sys.executable, str(CHART), json.dumps(payload)],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        parsed = json.loads(proc.stdout)
        self.assertEqual("success", parsed["status"])
        self.assertTrue(output.exists())
        self.assertGreater(output.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
