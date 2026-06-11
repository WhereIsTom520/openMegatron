import sys
from pathlib import Path
import unittest


RESEARCH_DIR = Path(__file__).resolve().parents[1] / "pysrc" / "skills" / "research"
sys.path.insert(0, str(RESEARCH_DIR))

from research_common import (  # noqa: E402
    build_reference_verification,
    format_verification_section,
    infer_contribution_type,
    infer_method,
    match_top_venue,
    venue_policy_summary,
    venue_score,
    venue_standard_source,
    venue_standard_tags,
)


class ResearchCommonInferenceTests(unittest.TestCase):
    def test_systematic_review_is_not_tagged_as_rag(self):
        text = "A systematic review and meta-analysis of human-AI collaboration."
        self.assertEqual(infer_method(text), "systematic review / meta-analysis")

    def test_rag_marker_uses_word_boundary(self):
        text = "The average improvement is evaluated in a user study."
        self.assertNotIn("retrieval / RAG", infer_method(text))

    def test_meta_analysis_contribution_type(self):
        text = "This meta-analysis synthesizes experimental evidence."
        self.assertEqual(infer_contribution_type(text), "review / meta-analysis / evidence synthesis")

    def test_journal_alias_does_not_match_book_series_substring(self):
        venue = "Advances in logistics, operations, and management science book series"
        self.assertEqual(venue_score(venue, domain="management"), 0)

    def test_expanded_management_whitelist_matches_major_journals(self):
        cases = [
            ("Journal of Management Information Systems", "is"),
            ("Administrative Science Quarterly", "organization"),
            ("Organization Science", "organization"),
            ("Production and Operations Management", "operations"),
            ("Marketing Science", "marketing"),
        ]
        for venue, expected_domain in cases:
            with self.subTest(venue=venue):
                match = match_top_venue(venue, domain="management")
                self.assertIsNotNone(match)
                self.assertIn(expected_domain, match["domain"])
                self.assertEqual(venue_score(venue, domain="management"), 3)

    def test_expanded_cs_whitelist_matches_major_journals_and_conferences(self):
        cases = [
            ("ACM Transactions on Graphics", "ccf-a"),
            ("IEEE Transactions on Software Engineering", "ccf-a"),
            ("ACM Transactions on Computer-Human Interaction", "ccf-a"),
            ("Proceedings of the ACM on Interactive, Mobile, Wearable and Ubiquitous Technologies", "ccf-a"),
            ("International Symposium on Computer Architecture", "ccf-a"),
            ("International Conference on Software Engineering", "ccf-a"),
            ("ACM Joint European Software Engineering Conference and Symposium on the Foundations of Software Engineering", "ccf-a"),
            ("International Conference on Automated Software Engineering", "ccf-a"),
        ]
        for venue, expected_tier in cases:
            with self.subTest(venue=venue):
                match = match_top_venue(venue, domain="cs")
                self.assertIsNotNone(match)
                self.assertEqual(match["tier"], expected_tier)
                self.assertEqual(venue_score(venue, domain="cs"), 3)

    def test_venue_policy_summary_exposes_versioned_standards(self):
        summary = venue_policy_summary("cs")
        self.assertIn("policy", summary)
        self.assertEqual(summary["policy"]["mode"], "realtime-first-with-versioned-fallback")
        self.assertGreater(summary["standard_counts"]["ccf"], 10)
        self.assertIn("JCR", summary["policy"]["journal_partition_primary"])

    def test_legacy_whitelist_records_have_explainable_fallback_source(self):
        match = match_top_venue("IEEE Transactions on Pattern Analysis and Machine Intelligence", domain="cs")
        self.assertIsNotNone(match)
        self.assertEqual(venue_standard_tags(match), {"curated_whitelist": True})
        self.assertIn("live CCF/CAS/JCR lookup preferred", venue_standard_source(match))

    def test_doi_link_is_traceable_not_live_reachable_without_http_check(self):
        paper = {
            "title": "A Survey on the Memory Mechanism of Large Language Model-based Agents",
            "doi": "10.1145/3748302",
            "venue": "ACM Transactions on Information Systems",
            "year": 2025,
        }
        entry = build_reference_verification(paper, 1)
        links_info = entry["links_info"]
        self.assertTrue(links_info["traceable"])
        self.assertIsNone(links_info["reachable"])
        self.assertIn("not_checked", links_info["reachability_note"])
        self.assertEqual(entry["hallucination_risk"], "medium")
        self.assertEqual(entry["verdict"], "traceable")

    def test_openalex_structured_metadata_is_low_risk(self):
        paper = {
            "title": "Memory Matters: The Need to Improve Long-Term Memory in LLM-Agents",
            "doi": "10.1609/aaaiss.v2i1.27688",
            "search_source_api": "OpenAlex",
        }
        entry = build_reference_verification(paper, 1)
        self.assertEqual(entry["hallucination_risk"], "low")
        self.assertEqual(entry["metadata_source"], "OpenAlex Works API")

    def test_verification_section_does_not_claim_unchecked_links_are_reachable(self):
        section = format_verification_section([
            {
                "title": "A Survey on the Memory Mechanism of Large Language Model-based Agents",
                "doi": "10.1145/3748302",
                "venue": "ACM Transactions on Information Systems",
                "year": 2025,
            }
        ])
        self.assertIn("可追踪，未实时验证", section)
        self.assertIn("中风险条目", section)
        self.assertIn("| medium |", section)
        self.assertNotIn("| 实时可达 |", section)
        self.assertNotIn("✅ 可达", section)
        self.assertNotIn("404", section)

    def test_missing_identifier_requires_confirmation(self):
        entry = build_reference_verification({"title": "Unverified Local Draft"}, 1)
        self.assertFalse(entry["links_info"]["traceable"])
        self.assertIsNone(entry["links_info"]["reachable"])
        self.assertEqual(entry["verdict"], "needs_confirmation")
        self.assertEqual(entry["hallucination_risk"], "high")


if __name__ == "__main__":
    unittest.main()
