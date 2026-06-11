import importlib.util
from pathlib import Path
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "pysrc"
    / "skills"
    / "research"
    / "top_paper_search-1.0.0"
    / "scripts"
    / "top_paper_search.py"
)


def load_top_paper_search_module():
    spec = importlib.util.spec_from_file_location("top_paper_search_under_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TopPaperSearchRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_top_paper_search_module()

    def test_search_action_is_fetch_alias(self):
        self.assertEqual(self.module.normalize_action("search"), "fetch")

    def test_information_management_query_overrides_ai_domain(self):
        domain = self.module.infer_effective_domain("information management research", "ai")
        self.assertEqual(domain, "management")

    def test_information_management_domain_alias(self):
        domain = self.module.infer_effective_domain("research", "information management")
        self.assertEqual(domain, "management")

    def test_human_ai_query_defaults_to_hci_from_ai_domain(self):
        domain = self.module.infer_effective_domain("human-AI collaboration", "ai")
        self.assertEqual(domain, "hci")

    def test_management_query_is_refined_for_information_systems(self):
        query = self.module.refine_query_for_domain("information management research", "management")
        self.assertIn("information systems", query.lower())

    def test_query_cleaning_removes_year_and_keeps_domain_terms(self):
        query = self.module.clean_search_query("human-AI collaboration information systems 2024 人机协同 信息系统")
        self.assertNotIn("2024", query)
        self.assertIn("human", query.lower())
        self.assertIn("information systems", query.lower())

    def test_query_cleaning_removes_year_attached_to_chinese(self):
        query = self.module.clean_search_query("2024\u5e74\u4ee5\u6765 human-AI collaboration")
        self.assertNotIn("2024", query)
        self.assertNotIn("\u5e74\u4ee5\u6765", query)
        self.assertIn("human", query.lower())

    def test_query_cleaning_removes_chinese_filler_terms(self):
        query = self.module.clean_search_query("\u5e2e\u6211\u67e5\u4e00\u4e0b\u4fe1\u7ba1\u9886\u57df\u4eba\u673a\u534f\u540c\u8bba\u6587")
        self.assertNotIn("\u9886\u57df", query)
        self.assertNotIn("\u8bba\u6587", query)
        self.assertIn("information systems", query.lower())
        self.assertIn("human ai collaboration", query.lower())

    def test_query_cleaning_compacts_repeated_translated_terms(self):
        query = self.module.clean_search_query("human-AI collaboration information systems 2024 浜烘満鍗忓悓 淇℃伅绯荤粺")
        self.assertEqual(query.lower().count("human ai collaboration"), 1)
        self.assertEqual(query.lower().count("information systems"), 1)

    def test_relevance_filter_removes_unrelated_top_journal_result(self):
        papers = [
            {
                "title": "A pathology foundation model for cancer diagnosis",
                "venue": "Nature",
                "abstract": "Artificial intelligence improves pathology diagnosis.",
            },
            {
                "title": "When combinations of humans and AI are useful",
                "venue": "Nature Human Behaviour",
                "abstract": "This meta-analysis studies human-AI collaboration and combinations of humans and AI.",
            },
        ]
        kept = self.module.filter_by_relevance(papers, "human-AI collaboration information systems 2024 人机协同 信息系统")
        self.assertEqual(len(kept), 1)
        self.assertIn("humans and AI", kept[0]["title"])

    def test_relevance_filter_penalizes_unrequested_medical_topic(self):
        score = self.module.paper_relevance_score(
            "human-AI collaboration information systems",
            {
                "title": "Toward expert-level medical question answering with large language models",
                "venue": "Nature Medicine",
                "abstract": "A diagnostic AI system for clinical medicine and patient care.",
            },
        )
        self.assertLess(score, self.module.relevance_threshold("human-AI collaboration information systems"))

    def test_openalex_traceability_survives_top_venue_filtering(self):
        papers = [
            {
                "title": "Memory Matters: The Need to Improve Long-Term Memory in LLM-Agents",
                "publication_year": 2024,
                "authors": ["A. Researcher"],
                "venue": "AAAI Symposium Series",
                "cited_by_count": 30,
                "doi": "10.1609/aaaiss.v2i1.27688",
                "openalex_id": "https://openalex.org/W4400000000",
                "url": "https://doi.org/10.1609/aaaiss.v2i1.27688",
                "abstract": "LLM agents need long-term memory.",
                "source_id": "https://openalex.org/S123",
                "issn_clean": "",
                "search_source_api": "OpenAlex",
            }
        ]
        filtered = self.module.filter_papers(papers, year_start=2020, domain="ai")
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["openalex_id"], "https://openalex.org/W4400000000")
        self.assertEqual(filtered[0]["search_source_api"], "OpenAlex")


if __name__ == "__main__":
    unittest.main()
