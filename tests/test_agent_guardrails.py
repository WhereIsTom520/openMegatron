import unittest
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pysrc"))
from pysrc.agent import YuanGeAgent


class FakeToolManager:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


class AgentGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.agent = YuanGeAgent.__new__(YuanGeAgent)

    def test_repairs_utf8_as_latin1_mojibake(self):
        mojibake = "人机协同 信息系统".encode("utf-8").decode("latin1")
        repaired = self.agent._repair_mojibake_text(mojibake)
        self.assertIn("人机协同", repaired)
        self.assertIn("信息系统", repaired)

    def test_repairs_gbk_as_latin1_mojibake(self):
        mojibake = "人机协同 信息系统".encode("gbk").decode("latin1")
        repaired = self.agent._repair_mojibake_text(mojibake)
        self.assertIn("人机协同", repaired)
        self.assertIn("信息系统", repaired)

    def test_simple_research_lookup_detects_repaired_text(self):
        mojibake = "人机协同 信息系统 论文 给出链接".encode("gbk").decode("latin1")
        self.assertTrue(self.agent._is_simple_research_lookup(mojibake))

    def test_simple_research_lookup_excludes_review_requests(self):
        self.assertFalse(self.agent._is_simple_research_lookup("人机协同论文综述"))

    def test_lookup_with_verification_matrix_uses_deterministic_answer(self):
        payload = {"papers": [{"title": "x"}], "verification_matrix": []}
        prompt = "检索 agent memory 的顶会论文，只返回白名单命中的结果，并给出引用与反幻觉验证矩阵"
        self.assertTrue(self.agent._should_use_deterministic_research_answer(payload, prompt))

    def test_review_request_does_not_use_lookup_formatter(self):
        payload = {"papers": [{"title": "x"}], "verification_matrix": []}
        prompt = "围绕智能体长期记忆，帮我生成一份研究综述草稿"
        self.assertFalse(self.agent._should_use_deterministic_research_answer(payload, prompt))

    def test_abstract_request_uses_deterministic_abstract_answer(self):
        payload = {
            "papers": [
                {
                    "title": "Memory Matters",
                    "year": 2024,
                    "venue": "AAAI Symposium Series",
                    "doi": "10.1609/aaaiss.v2i1.27688",
                    "abstract": "This paper studies long-term memory for LLM agents.",
                }
            ]
        }
        prompt = "读取这些顶会论文的详细摘要"
        self.assertTrue(self.agent._should_use_deterministic_abstract_answer(payload, prompt))
        answer = self.agent._format_paper_abstract_answer(payload, prompt)
        self.assertIn("This paper studies long-term memory", answer)
        self.assertIn("证据边界", answer)

    def test_missing_abstract_is_not_filled_with_inferred_claims(self):
        payload = {
            "papers": [
                {
                    "title": "Missing Abstract Paper",
                    "year": 2025,
                    "venue": "ACL",
                    "doi": "10.0000/example",
                }
            ]
        }
        answer = self.agent._format_paper_abstract_answer(payload, "读取这些顶会论文的详细摘要")
        self.assertIn("未在当前题录元数据中提供摘要", answer)
        self.assertNotIn("首次系统性", answer)

    def test_zotero_skill_requires_explicit_zotero_request(self):
        prompt = "针对企业知识库智能体，帮我设计一个可发表论文的研究问题，并给出相关顶会顶刊证据"
        self.assertFalse(self.agent._skill_allowed_for_request("zotero_manager", prompt))
        self.assertTrue(self.agent._skill_allowed_for_request("zotero_manager", "请从 Zotero 导出 BibTeX"))

    def test_multi_agent_memory_followup_routes_as_research_not_media(self):
        prompt = "多智能体共享记忆从哪下手合理？"
        self.assertEqual(self.agent._detect_skill_category(prompt), "research")
        self.assertTrue(self.agent._looks_like_research_discussion(prompt))
        self.assertFalse(self.agent._skill_allowed_for_request("bilibili_search", prompt))
        self.assertFalse(self.agent._skill_allowed_for_request("download-video", prompt))
        self.assertFalse(self.agent._skill_allowed_for_request("code_assistant", prompt))

    def test_research_followup_skill_selection_excludes_media_and_code(self):
        prompt = "多智能体共享记忆从哪下手合理？"
        self.agent.loaded_skills = {
            "paper_fetch_review": {
                "category": "research",
                "description": "Fetch top papers and evidence for research topics.",
                "keywords": ["论文", "文献", "研究", "智能体", "记忆"],
            },
            "review_pipeline": {
                "category": "research",
                "description": "Build literature review and research gaps.",
                "keywords": ["综述", "研究空白", "研究方向"],
            },
            "bilibili_search": {
                "category": "media",
                "description": "Search bilibili videos.",
                "keywords": ["搜索", "bilibili", "视频"],
            },
            "download-video": {
                "category": "media",
                "description": "Download videos.",
                "keywords": ["下载", "视频"],
            },
            "code_assistant": {
                "category": "code",
                "description": "Implement and debug code.",
                "keywords": ["实现", "代码", "debug"],
            },
        }
        self.agent.skill_embeddings = {}
        self.agent.skill_docs = {}
        self.agent.memory_engine = None
        self.agent.skill_budget_mode = "auto"
        self.agent.top_k_skills = 5
        self.agent.max_prompt_skills = 5
        selected = self.agent._select_skills_for_prompt(prompt)
        self.assertIn("review_pipeline", selected)
        self.assertNotIn("bilibili_search", selected)
        self.assertNotIn("download-video", selected)
        self.assertNotIn("code_assistant", selected)

    def test_trace_detects_successful_wrapped_paper_fetch_output(self):
        arguments = json.dumps({
            "skill_name": "paper_fetch_review",
            "args_string": json.dumps({"query": "human-AI collaboration"}),
        })
        trace = {
            "tool_calls": [
                {
                    "tool": "run_skill_script",
                    "arguments": arguments,
                    "parsed_output": {
                        "status": "success",
                        "output": json.dumps({"status": "success", "valid_count": 1, "papers": [{"title": "x"}]}),
                    },
                }
            ]
        }
        self.assertTrue(self.agent._trace_has_successful_paper_fetch(trace))

    def test_research_lookup_answer_uses_structured_link_status(self):
        payload = {
            "status": "success",
            "valid_count": 1,
            "papers": [
                {
                    "title": "A Survey on the Memory Mechanism of Large Language Model-based Agents",
                    "year": "2025",
                    "venue": "ACM Transactions on Information Systems",
                    "citations": 68,
                    "doi": "10.1145/3748302",
                }
            ],
            "verification_matrix": [
                {
                    "index": 1,
                    "metadata_source": "OpenAlex Works API",
                    "doi": "10.1145/3748302",
                    "hallucination_risk": "low",
                    "links_info": {
                        "traceable": True,
                        "reachable": None,
                        "links": {"doi": "https://doi.org/10.1145/3748302"},
                    },
                }
            ],
            "reference_verification": {
                "boundary": "Metadata traceability only; publisher pages and PDF-level claims require verification."
            },
        }
        answer = self.agent._format_research_lookup_answer(payload, "检索 agent memory 的顶会论文")
        self.assertIn("可追踪，未实时验证", answer)
        self.assertIn("OpenAlex Works API", answer)
        self.assertNotIn("✅ 可达", answer)
        self.assertNotIn("DOI 解析 / Publisher", answer)
        self.assertNotIn("404", answer)

    def test_reference_link_status_requires_explicit_reachable_true(self):
        self.assertEqual(
            self.agent._reference_link_status({"links_info": {"traceable": True, "reachable": True}}),
            "实时可达",
        )
        self.assertEqual(
            self.agent._reference_link_status({"links_info": {"traceable": True, "reachable": False}}),
            "不可达",
        )
        self.assertEqual(
            self.agent._reference_link_status({"links_info": {"traceable": True, "reachable": None}}),
            "可追踪，未实时验证",
        )
        self.assertEqual(
            self.agent._reference_link_status({"links_info": {"traceable": False, "reachable": None}}),
            "待确认",
        )

    def test_sanitizes_interrupted_tool_call_sequence(self):
        messages = [
            {"role": "user", "content": "run"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_a", "type": "function", "function": {"name": "x", "arguments": "{}"}},
                    {"id": "call_b", "type": "function", "function": {"name": "y", "arguments": "{}"}},
                ],
            },
            {"role": "system", "content": "warning inserted too early"},
            {"role": "tool", "tool_call_id": "call_a", "content": "{}"},
        ]
        sanitized = self.agent._sanitize_openai_tool_message_sequence(messages)
        assistant_index = next(i for i, msg in enumerate(sanitized) if msg.get("role") == "assistant")
        self.assertEqual(sanitized[assistant_index + 1]["role"], "tool")
        self.assertEqual(sanitized[assistant_index + 1]["tool_call_id"], "call_a")
        self.assertEqual(sanitized[assistant_index + 2]["role"], "tool")
        self.assertEqual(sanitized[assistant_index + 2]["tool_call_id"], "call_b")
        self.assertEqual(sanitized[assistant_index + 3]["role"], "system")

    def test_sanitizer_drops_orphan_tool_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "tool", "tool_call_id": "orphan", "content": "{}"},
            {"role": "assistant", "content": "done"},
        ]
        sanitized = self.agent._sanitize_openai_tool_message_sequence(messages)
        self.assertEqual([msg["role"] for msg in sanitized], ["user", "assistant"])


class SkillLoaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_skill_loader_accepts_bom_and_crlf_front_matter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "research" / "bom-skill"
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir(parents=True)
            (scripts_dir / "main.py").write_text("def main(**kwargs):\n    return {'status': 'success'}\n", encoding="utf-8")
            (skill_dir / "SKILL.md").write_text(
                "\ufeff---\r\n"
                "name: bom_skill\r\n"
                "description: Test skill with BOM and CRLF front matter.\r\n"
                "entry_function: main\r\n"
                "parameters:\r\n"
                "  type: object\r\n"
                "  properties: {}\r\n"
                "---\r\n"
                "\r\n"
                "# Body\r\n",
                encoding="utf-8",
            )

            agent = YuanGeAgent.__new__(YuanGeAgent)
            agent.skills_dir = root
            agent.tool_manager = FakeToolManager()
            await agent._load_skills()

            self.assertIn("bom_skill", agent.loaded_skills)


if __name__ == "__main__":
    unittest.main()
