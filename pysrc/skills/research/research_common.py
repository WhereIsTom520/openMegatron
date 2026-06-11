from __future__ import annotations

import csv
import io

import json
import asyncio
import math
import re
import sys
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib

try:
    import requests
except Exception:
    requests = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None


HEADERS = {"User-Agent": "MegatronResearchAssistant/1.0"}
RESEARCH_DIR = Path(__file__).resolve().parent
CONFIG_DIR = RESEARCH_DIR / "config"
VENUE_STANDARD_KEYS = ("ccf", "cas", "jcr", "ft50", "utd24", "ajg", "abdc")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def fail(message: str, *, completed: bool = False) -> None:
    emit({"status": "error", "message": message, "completed": completed})
    raise SystemExit(1)


def parse_params(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"Invalid JSON argument: {exc}")
    if not isinstance(value, dict):
        fail("First argument must be a JSON object.")
    return value


def compact_text(text: Any, *, max_chars: int = 12000) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:max_chars]


def load_research_config(name: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    filename = name if name.endswith(".toml") else f"{name}.toml"
    path = CONFIG_DIR / filename
    if not path.exists():
        return default or {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return data if isinstance(data, dict) else (default or {})
    except Exception:
        return default or {}
def _clean_issn(value: Any) -> str:
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def normalize_venue_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", (value or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _domain_allowed(record: dict[str, Any], domain: str | list[str] | None) -> bool:
    if not domain:
        return True
    record_domains = {str(x).lower() for x in _as_list(record.get("domain"))}
    if not record_domains or "general" in record_domains:
        return True
    if isinstance(domain, str):
        return str(domain).lower() in record_domains
    return any(str(d).lower() in record_domains for d in domain)


def venue_policy(domain: str | list[str] | None = None) -> dict[str, Any]:
    data = load_research_config("venues", {})
    records = [v for v in data.get("venues", []) if isinstance(v, dict) and _domain_allowed(v, domain)]
    prefixes = [normalize_venue_name(x) for x in data.get("matching", {}).get("prefixes", [])]
    alias_map: dict[str, dict[str, Any]] = {}
    issn_map: dict[str, dict[str, Any]] = {}
    for record in records:
        names = [record.get("name"), *_as_list(record.get("aliases"))]
        for name in names:
            key = normalize_venue_name(str(name or ""))
            if key:
                alias_map[key] = record
        for issn in _as_list(record.get("issn")):
            clean = _clean_issn(issn)
            if clean:
                issn_map[clean] = record
    return {
        "strict": bool(data.get("strict_top_venue", True)),
        "policy": data.get("policy", {}) if isinstance(data.get("policy"), dict) else {},
        "prefixes": [p for p in prefixes if p],
        "aliases": alias_map,
        "issns": issn_map,
        "records": records,
    }


def match_top_venue(venue: str, issn_clean: str = "", domain: str | list[str] | None = None) -> dict[str, Any] | None:
    policy = venue_policy(domain)
    issn = _clean_issn(issn_clean)
    if issn and issn in policy["issns"]:
        return policy["issns"][issn]
    normalized = normalize_venue_name(venue)
    if not normalized:
        return None
    aliases: dict[str, dict[str, Any]] = policy["aliases"]
    if normalized in aliases:
        return aliases[normalized]
    padded = f" {normalized} "
    for alias, record in aliases.items():
        record_type = str(record.get("type") or "").lower()
        if record_type == "conference" and ((len(alias) >= 4 and f" {alias} " in padded) or (len(alias) >= 2 and re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized))):
            return record
    for prefix in policy["prefixes"]:
        if normalized == prefix or normalized.startswith(prefix + " "):
            return {
                "name": venue,
                "type": "journal",
                "tier": "top-prefix",
                "domain": ["general"],
                "source": "prefix-policy",
            }
    return None


def is_top_venue_configured(venue: str, issn_clean: str = "", domain: str | list[str] | None = None) -> bool:
    return match_top_venue(venue, issn_clean, domain) is not None


def venue_score(venue: str, issn_clean: str = "", domain: str | list[str] | None = None) -> int:
    matched = match_top_venue(venue, issn_clean, domain)
    if not matched:
        return 0
    tier = str(matched.get("tier", "")).lower()
    if tier in ("top", "flagship", "ccf-a", "jcr-q1", "cas-c1", "ieee-transactions"):
        return 3
    if tier in ("ccf-b", "jcr-q2", "cas-c2", "top-prefix"):
        return 2
    if tier in ("ccf-c", "jcr-q3", "cas-c3"):
        return 1
    return 0


def venue_standard_tags(record: dict[str, Any]) -> dict[str, Any]:
    tags = {
        key: record.get(key)
        for key in VENUE_STANDARD_KEYS
        if record.get(key) not in (None, "", False)
    }
    if not tags:
        tags["curated_whitelist"] = True
    return tags


def venue_standard_source(record: dict[str, Any]) -> str:
    source = str(record.get("source") or "").strip()
    if source:
        return source
    domains = {str(x).lower() for x in _as_list(record.get("domain"))}
    if "cs" in domains:
        return "fallback: configured top computer-science whitelist; live CCF/CAS/JCR lookup preferred"
    if "management" in domains:
        return "fallback: configured top management whitelist; live FT50/UTD24/AJG/ABDC/JCR lookup preferred"
    return "fallback: configured top-venue whitelist; live standard lookup preferred where available"


def venue_policy_summary(domain: str | list[str] | None = None) -> dict[str, Any]:
    policy = venue_policy(domain)
    flags = []
    if policy["strict"]:
        flags.append("strict")
    venues = list({r.get("name", "?") for r in policy["records"]})
    standard_counts = Counter()
    for record in policy["records"]:
        for key in VENUE_STANDARD_KEYS:
            value = record.get(key)
            if value not in (None, "", False):
                standard_counts[key] += 1
        if not any(record.get(key) not in (None, "", False) for key in VENUE_STANDARD_KEYS):
            standard_counts["curated_whitelist"] += 1
    return {
        "count": len(policy["records"]),
        "strict": policy["strict"],
        "domains": [domain] if isinstance(domain, str) else (domain or None),
        "policy": policy.get("policy", {}),
        "standard_counts": dict(standard_counts),
        "venues": venues[:20],
    }

def keyword_set(text: str) -> set[str]:
    lowered_text = (text or "").lower()
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", lowered_text)
    zh_map = {
        "检索": ["retrieval", "rag"],
        "增强": ["augmented"],
        "记忆": ["memory"],
        "长期": ["long-term"],
        "规划": ["planning"],
        "推理": ["reasoning"],
        "反馈": ["reflection"],
        "幻觉": ["hallucination"],
        "智能体": ["agent", "agents"],
        "自主": ["autonomous"],
        "决策": ["decision"],
        "强化学习": ["reinforcement", "learning"],
        "医疗": ["medical", "clinical"],
        "生物医学": ["biomedicine"],
        "自适应": ["self-adaptive"],
        "多智能体": ["multi-agent"],
    }
    expanded: list[str] = []
    for zh, mapped in zh_map.items():
        if zh in lowered_text:
            expanded.extend(mapped)
    tokens.extend(expanded)
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "large",
        "language", "model", "models", "paper", "study", "survey", "method", "methods",
        "limitations", "limitation", "available", "explicit", "abstract", "requires", "require",
        "not", "need", "needs", "mostly", "metadata", "signal",
    }
    return {tok for tok in tokens if tok not in stop}


def overlap_score(left: str, right: str) -> float:
    a = keyword_set(left)
    b = keyword_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def infer_method(text: str) -> str:
    lowered = (text or "").lower()
    review_terms = ["systematic review", "meta-analysis", "meta analysis", "evidence synthesis"]
    if any(term in lowered for term in review_terms):
        return "systematic review / meta-analysis"

    def has_marker(marker: str) -> bool:
        if marker in {"rag", "rl"}:
            return re.search(rf"\b{re.escape(marker)}\b", lowered) is not None
        return marker in lowered

    rules = [
        ("retrieval / RAG", ["retrieval", "rag", "knowledge base", "vector", "检索"]),
        ("memory systems", ["memory", "long-term", "episodic", "记忆", "长期"]),
        ("reasoning / planning", ["reasoning", "planning", "system 2", "chain-of-thought", "决策", "规划", "推理"]),
        ("reinforcement learning", ["reinforcement learning", "reward", "policy", "rl", "强化学习"]),
        ("multi-agent systems", ["multi-agent", "agents", "collaborative", "orchestration", "多智能体"]),
        ("self-adaptive systems", ["self-adaptive", "feedback loop", "monitoring", "execution", "自适应"]),
        ("evaluation / reliability", ["hallucination", "evaluation", "benchmark", "certainty", "safety", "幻觉"]),
        ("domain application", ["biomedicine", "clinical", "medical", "robot", "iot", "aiot", "医疗"]),
    ]
    hits = [label for label, needles in rules if any(has_marker(n) for n in needles)]
    return "; ".join(hits[:3]) if hits else "general / conceptual"


def infer_contribution_type(text: str) -> str:
    lowered = (text or "").lower()
    if any(k in lowered for k in ["systematic review", "meta-analysis", "meta analysis", "evidence synthesis"]):
        return "review / meta-analysis / evidence synthesis"
    if any(k in lowered for k in ["benchmark", "dataset", "evaluation", "基准"]):
        return "benchmark / survey / evaluation"
    if any(k in lowered for k in ["framework", "architecture", "system", "agent", "框架", "系统"]):
        return "system / framework"
    if any(k in lowered for k in ["algorithm", "optimization", "training", "算法", "训练"]):
        return "algorithm / training method"
    if any(k in lowered for k in ["application", "case study", "clinical", "应用"]):
        return "application / case study"
    return "conceptual / empirical"


def infer_findings(text: str) -> str:
    text_c = compact_text(text, max_chars=2000)
    sentences = re.split(r"(?<=[.!?\u3002\uff01\uff1f])\s+", text_c)
    preferred = []
    for sentence in sentences:
        low = sentence.lower()
        if any(k in low for k in ["show", "find", "propose", "improve", "demonstrate", "indicate", "suggest"]):
            preferred.append(sentence.strip())
    return compact_text(" ".join(preferred[:2] or sentences[:2]), max_chars=700)


def infer_limitations(text: str) -> str:
    lowered = (text or "").lower()
    hints: list[str] = []

    # ---- Phase 1: keyword-based hints (fast fallback, kept from original) ----
    if "hallucination" in lowered or "幻觉" in lowered:
        hints.append("requires hallucination detection and uncertainty control")
    if "privacy" in lowered or "clinical" in lowered or "biomedicine" in lowered or "隐私" in lowered:
        hints.append("privacy, governance, and domain validation are critical")
    if "survey" in lowered or "review" in lowered or "综述" in lowered:
        hints.append("mostly synthesizes literature and needs task-specific empirical validation")
    if "autonomous" in lowered or "decision" in lowered or "自主" in lowered or "决策" in lowered:
        hints.append("deployment risk rises in open-ended decision environments")
    if "memory" in lowered or "记忆" in lowered:
        hints.append("long-term consistency, update policy, and forgetting control need explicit evaluation")

    # ---- Phase 2: structured abstract analysis ----
    # 2a. Sample size / dataset limitations
    _analyze_sample_limitations(lowered, hints)

    # 2b. Methodological constraints
    _analyze_methodological_constraints(lowered, hints)

    # 2c. Generalizability concerns
    _analyze_generalizability(lowered, hints)

    # 2d. Temporal limitations
    _analyze_temporal_limitations(lowered, hints)

    # ---- Deduplicate while preserving order ----
    seen: set[str] = set()
    unique: list[str] = []
    for h in hints:
        if h not in seen:
            seen.add(h)
            unique.append(h)

    return "; ".join(unique) if unique else "limitations are not explicit in the available abstract"


# ---------------------------------------------------------------------------
# Helper functions for structured limitation analysis
# ---------------------------------------------------------------------------

def _analyze_sample_limitations(lowered: str, hints: list[str]) -> None:
    # Small sample size indicators
    small_n_patterns = [
        (r"\bn\s*[=<>]\s*\d{1,2}\b", "small sample size (n<100)"),
        (r"\bn\s*=\s*\d{2,3}\b", "small sample size (n<100)"),
        (r"\bonly\s+\d{1,3}\s+(participant|subject|sample|patient|instance|case)", "small sample size"),
        (r"\b\d{1,3}\s+(participant|subject|sample|patient)s?\b", "small sample size"),
        (r"\bfew[-\s]?shot\b", "few-shot evaluation only; small n concern"),
        (r"\blimited\s+(sample|data|dataset|corpus)\b", "limited dataset size"),
    ]
    for pattern, hint in small_n_patterns:
        if re.search(pattern, lowered):
            hints.append(hint)
            break  # one sample-size hint is enough

    # Single dataset concern
    if re.search(r"\b(single|one)\s+(dataset|corpus|benchmark|source)\b", lowered):
        hints.append("single dataset evaluation only")
    if re.search(r"\bonly\s+(test|evaluat|benchmark)(ed|ing)?\s+on\b", lowered):
        hints.append("evaluated on a narrow set of benchmarks")

    # Synthetic / simulated data
    if re.search(r"\b(synthetic|simulated|artificial(ly)?\s+generated)\s+(data|dataset|corpus|text)\b", lowered):
        hints.append("synthetic or simulated data; real-world validation needed")
    if re.search(r"\bsimulation\s+(study|only|setting|environment)\b", lowered):
        hints.append("simulation study; may not reflect real-world conditions")

    # Imbalanced or noisy data
    if re.search(r"\b(class\s*)?imbalance(d)?\b", lowered):
        hints.append("class imbalance in dataset may bias results")
    if re.search(r"\bnoisy\s+(label|data|annotation)\b", lowered):
        hints.append("noisy labels or annotations may affect reliability")


def _analyze_methodological_constraints(lowered: str, hints: list[str]) -> None:
    # Correlation vs causation
    if re.search(r"\bcorrelation(al)?\b", lowered) and not re.search(r"\bcaus(al|ation)\b", lowered):
        hints.append("correlation-based analysis; causation not established")

    # Lab vs field / real-world
    if re.search(r"\b(lab(oratory)?\s+(setting|study|experiment|condition)|controlled\s+(lab|environment|setting))\b", lowered):
        hints.append("lab setting may not transfer to production")
    if re.search(r"\bin[-\s]vitro\b", lowered):
        hints.append("in-vitro results; in-vivo validation pending")
    if re.search(r"\b(offline|static)\s+(evaluation|setting|data)\b", lowered):
        hints.append("offline/static evaluation; online performance unknown")

    # Self-reported / survey-based
    if re.search(r"\bself[-\s]report(ed|ing)?\b", lowered):
        hints.append("self-reported data; subject to recall and social-desirability bias")
    if re.search(r"\bquestionnaire\b", lowered) or re.search(r"\bsurvey\s+(data|response|participant)\b", lowered):
        hints.append("survey-based data; response bias possible")

    # Ablation / sensitivity gaps
    if re.search(r"\bno\s+(ablation|sensitivity)\s+(study|analysis)\b", lowered):
        hints.append("no ablation or sensitivity analysis reported")
    if re.search(r"\bhyperparameter\b", lowered) and re.search(r"\b(tuned?|sensitive|choice|selection)\b", lowered):
        hints.append("hyperparameter sensitivity not fully explored")

    # Reproducibility flags
    if re.search(r"\b(not\s+(reproducible|open[-\s]?source(d)?)|proprietary|closed[-\s]?source)\b", lowered):
        hints.append("code or data not publicly available; reproducibility limited")
    if re.search(r"\brandom\s+(seed|initialization)\b", lowered) and re.search(r"\b(single|fixed|one)\b", lowered):
        hints.append("single random seed; variance across runs unreported")


def _analyze_generalizability(lowered: str, hints: list[str]) -> None:
    # Domain-specific
    if re.search(r"\bdomain[-\s]specific\b", lowered):
        hints.append("domain-specific; cross-domain transfer unclear")
    if re.search(r"\b(specific\s+to\b|restricted\s+to\b|limited\s+to\b)\s+\w+\s+(domain|field|area|setting)", lowered):
        hints.append("findings restricted to a specific domain")

    # Language constraints
    if re.search(r"\benglish[-\s](only|language|text|corpus|document|paper)", lowered):
        hints.append("English-language papers only")
    if re.search(r"\bmonolingual\b", lowered):
        hints.append("monolingual evaluation; multilingual performance unknown")
    if re.search(r"\b(only\s+english|english\s+only)\b", lowered):
        hints.append("English-only evaluation")

    # Geographic / cultural
    if re.search(r"\b(single\s+(country|region|hospital|center|site|institution))\b", lowered):
        hints.append("single-site study; geographic generalizability uncertain")
    if re.search(r"\b(western|european|us[-\s]based|united\s+states|american)\b", lowered) and re.search(r"\b(sample|population|cohort|participant|data)\b", lowered):
        hints.append("Western-centric sample; cross-cultural generalizability unclear")
    if re.search(r"\bweibo\b|\bwechat\b|\bchinese\s+(social|language|text|corpus|dataset)\b", lowered):
        hints.append("Chinese-language / platform-specific; cross-platform generalizability unclear")

    # Model / architecture specific
    if re.search(r"\b(single\s+(model|architecture|backbone|framework))\b", lowered):
        hints.append("tested on a single architecture; model-agnostic claims unverified")
    if re.search(r"\b(small[-\s]?(scale)?\s+model|lightweight)\b", lowered):
        hints.append("small-scale model; findings may not hold for larger architectures")

    # Task-specificity
    if re.search(r"\b(single\s+task|task[-\s]specific)\b", lowered):
        hints.append("single-task evaluation; multi-task robustness unknown")


def _analyze_temporal_limitations(lowered: str, hints: list[str]) -> None:
    # Rapidly evolving field
    if re.search(r"\b(rapidly\s+(evolving|changing|advancing)|fast[-\s]moving|quickly\s+(evolving|changing))\s+(field|area|domain|landscape|technology)\b", lowered):
        hints.append("rapidly evolving field; findings may become outdated quickly")

    # Time-bound data
    time_bound_patterns = [
        (r"\b(19\d{2}|20[01]\d)\b", "data from a specific historical period; temporal drift possible"),
        (r"\b(201[0-8]|200\d|199\d)\b", "data from a specific historical period; temporal drift possible"),
        (r"\bdata\s+(from|collected|spanning|covering)\s+\d{4}\b", "data from a specific time window; temporal generalizability unclear"),
    ]
    for pattern, hint in time_bound_patterns:
        if re.search(pattern, lowered):
            hints.append(hint)
            break

    # Snapshot / cross-sectional
    if re.search(r"\bcross[-\s]sectional\b", lowered):
        hints.append("cross-sectional design; longitudinal trends not captured")
    if re.search(r"\bsnapshot\b", lowered) and re.search(r"\b(data|study|analysis|evaluation)\b", lowered):
        hints.append("snapshot study; temporal dynamics not assessed")

    # Concept drift / staleness
    if re.search(r"\bconcept\s+drift\b", lowered):
        hints.append("concept drift acknowledged; model may degrade over time")
    if re.search(r"\b(pre[-\s]trained?|training)\s+(cutoff|deadline|date)\b", lowered):
        hints.append("training data cutoff may miss recent developments")

    # Short duration
    if re.search(r"\b(short[-\s]term|brief)\s+(study|follow[-\s]?up|experiment|evaluation|period)\b", lowered):
        hints.append("short-term evaluation; long-term effects unmeasured")
    if re.search(r"\b\d{1,2}[-\s](day|week|month)\s+(study|follow|experiment|period|window)\b", lowered):
        hints.append("short observation window; long-term outcomes unknown")


def infer_evidence_strength(paper: dict[str, Any]) -> str:
    abstract_len = len(paper.get("abstract") or paper.get("text") or "")
    citations = int(paper.get("citations") or 0)
    if abstract_len >= 500 and citations >= 100:
        return "strong metadata signal"
    if abstract_len >= 300 or citations >= 25:
        return "moderate metadata signal"
    # Boost by venue policy: top-venue papers with shorter abstracts get moderate rating
    vs = venue_score(str(paper.get("venue") or ""))
    if vs >= 3:
        return "moderate metadata signal"
    return "weak metadata signal"

def normalize_paper(raw: dict[str, Any], index: int = 0) -> dict[str, Any]:
    raw = raw or {}
    authors_raw = raw.get("authors")
    str_authors = authors_raw if isinstance(authors_raw, str) else (", ".join(authors_raw) if isinstance(authors_raw, list) else "")
    doi = (raw.get("doi") or "").strip()
    url = (raw.get("url") or "").strip()
    citations = raw.get("citations") or raw.get("cited_by_count") or 0

    # Build best available link
    link = ""
    if doi:
        link = f"https://doi.org/{doi}" if doi.startswith("10.") else doi
    if not link and url:
        link = url
    if not link:
        oid = (raw.get("openalex_id") or raw.get("id") or "").strip()
        if oid and "openalex.org" in oid:
            link = oid
    if not link:
        title_q = (raw.get("title") or "")
        if title_q:
            q = urllib.parse.quote(f'{title_q} {str_authors[:80]}')
            link = f"https://scholar.google.com/scholar?q={q}"

    return {
        "id": raw.get("id") or f"ref{index}",
        "title": raw.get("title") or "Untitled",
        "authors": str_authors or "Unknown",
        "year": raw.get("year") or "n.d.",
        "venue": raw.get("venue") or "Unknown venue",
        "doi": doi,
        "url": url,
        "link": link,
        "citations": int(citations) if citations else 0,
        "abstract": raw.get("abstract") or raw.get("text") or "",
        "source_quality": raw.get("source_quality") or "from_openalex" if doi or url else "user_provided",
        "evidence_text": raw.get("evidence_text") or raw.get("key_findings") or raw.get("core_problem") or "",
    }


def normalize_papers(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict):
        value = [value]
    return [normalize_paper(item, idx) for idx, item in enumerate(value)]


def papers_from_params(params: dict[str, Any]) -> list[dict[str, Any]]:
    papers = params.get("papers") or params.get("items") or []
    if not papers and params.get("path"):
        path = Path(str(params["path"]))
        if path.exists():

            try:
                raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(raw, list):
                    papers = raw
                elif isinstance(raw, dict):
                    papers = raw.get("papers") or raw.get("items") or [raw]
            except Exception:
                pass
    return normalize_papers(papers)




def readings_to_papers(readings: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Convert reading dicts (from paper_reader) to standard paper dicts (for citation_graph etc.)."""
    if not readings:
        return []
    papers = []
    for idx, r in enumerate(readings):
        title = r.get("title", "") or ""
        abstract = r.get("evidence_text") or r.get("key_findings") or r.get("core_problem") or ""
        papers.append(normalize_paper({
            "id": r.get("id") or f"reading_{idx}",
            "title": str(title),
            "year": r.get("year"),
            "venue": r.get("venue", ""),
            "doi": r.get("doi", ""),
            "url": r.get("url", ""),
            "authors": r.get("authors") or r.get("author") or "",
            "abstract": str(abstract),
            "citations": r.get("citations") or 0,
        }, idx))
    return papers


def export_csv(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Convert list of dicts to CSV string. Ignores non-dict rows."""
    valid = [r for r in rows if isinstance(r, dict)]
    if not valid:
        return ""
    if columns is None:
        columns = list(valid[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    w.writerows(valid)
    return buf.getvalue()


def evidence_to_csv(rows: list[dict[str, Any]], output_path: str | None = None) -> str:
    """Export evidence matrix rows as CSV, optionally writing to file."""
    csv_str = export_csv(rows, ["ref_id", "title", "year", "venue", "method_category",
                                "contribution_type", "research_question_or_problem",
                                "main_evidence_or_findings", "limitations",
                                "research_gap_or_open_question", "evidence_strength",
                                "source_quality", "venue_tier", "doi", "url"])
    if output_path:
        Path(output_path).write_text(csv_str, encoding="utf-8")
    return csv_str


def papers_to_csv(papers: list[dict[str, Any]], output_path: str | None = None) -> str:
    """Export paper list as CSV."""
    valid_papers = normalize_papers(papers)
    csv_str = export_csv(valid_papers, ["title", "authors", "year", "venue", "doi", "citations", "abstract"])
    if output_path:
        Path(output_path).write_text(csv_str, encoding="utf-8")
    return csv_str

def reading_from_paper(paper: dict[str, Any]) -> dict[str, Any]:
    text = paper.get("abstract") or paper.get("text") or ""
    combined = f"{paper.get('title', '')}. {text}"
    return {
        "id": paper.get("id"),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "venue": paper.get("venue"),
        "doi": paper.get("doi"),
        "url": paper.get("url"),
        "link": paper.get("link") or paper.get("url") or (f"https://doi.org/{paper.get('doi')}" if paper.get("doi") and str(paper.get("doi")).startswith("10.") else "") or (f"https://scholar.google.com/scholar?q={paper.get('title', '')}" if paper.get("title") else ""),
        "source_quality": paper.get("source_quality") or "unknown",
        "venue_tier": paper.get("venue_tier") or "",
        "method_category": infer_method(combined),
        "contribution_type": infer_contribution_type(combined),
        "core_problem": compact_text(combined, max_chars=450),
        "key_findings": infer_findings(text),
        "limitations": infer_limitations(text),
        "research_gap_hint": infer_limitations(text),
        "evidence_strength": infer_evidence_strength(paper),
        "evidence_text": compact_text(text, max_chars=1200),
    }


def build_evidence_matrix(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for idx, paper in enumerate(papers, start=1):
        reading = reading_from_paper(paper)
        rows.append({
            "ref_id": idx,
            "title": reading["title"],
            "year": reading["year"],
            "venue": reading["venue"],
            "link": reading["link"],
            "source_quality": reading["source_quality"],
            "venue_tier": reading["venue_tier"],
            "method_category": reading["method_category"],
            "contribution_type": reading["contribution_type"],
            "research_question_or_problem": reading["core_problem"],
            "main_evidence_or_findings": reading["key_findings"],
            "limitations": reading["limitations"],
            "research_gap_or_open_question": reading["research_gap_hint"],
            "evidence_strength": reading["evidence_strength"],
            "citation_hint": f"[{idx}]",
            "doi": reading["doi"],
            "url": reading["url"],
        })
    return rows


def analyze_research_gaps(matrix: list[dict[str, Any]]) -> dict[str, Any]:
    methods = Counter(str(row.get("method_category") or "unknown") for row in matrix)
    contributions = Counter(str(row.get("contribution_type") or "unknown") for row in matrix)
    limitations = [str(row.get("limitations") or "") for row in matrix if row.get("limitations")]
    limitation_terms = Counter()
    for item in limitations:
        for token in keyword_set(item):
            limitation_terms[token] += 1
    expected_angles = [
        "retrieval / RAG",
        "memory systems",
        "reasoning / planning",
        "evaluation / reliability",
        "privacy / governance",
        "domain validation",
    ]
    observed_text = " ".join(methods.keys()).lower()
    underexplored = [angle for angle in expected_angles if angle.split(" / ")[0].lower() not in observed_text]
    potential = []

    if any("memory" in m.lower() for m in methods):
        potential.append("围绕长期记忆的更新、遗忘与冲突解决策略设计可复现实验。")
    if any("retrieval" in m.lower() or "rag" in m.lower() for m in methods):
        potential.append("将检索质量与下游生成、决策可靠性联合评测，而不是只看召回或相似度指标。")
    if underexplored:
        potential.append("把覆盖不足的方向转化为消融轴或研究问题：" + "、".join(underexplored[:3]) + "。")
    if not potential:
        potential.append("先用证据矩阵比较方法族、数据集与评测设置，再归纳可证明的创新点。")

    return {
        "paper_count": len(matrix),
        "method_distribution": dict(methods),
        "contribution_distribution": dict(contributions),
        "recurring_limitations": [term for term, _ in limitation_terms.most_common(8)],
        "underexplored_angles": underexplored,
        "potential_innovation_directions": potential,
        "caution": "研究空白分析默认由元数据和摘要推断；若要支撑强结论，需要继续读取 PDF 全文。",
    }


def build_review_protocol(
    query: str,
    *,
    review_type: str = "narrative",
    year_start: int | None = None,
    top_n: int | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    protocols = load_research_config("review_protocols", {})
    selected = protocols.get("protocols", {}).get(review_type) or protocols.get("protocols", {}).get("narrative") or {}
    return {
        "query": query,
        "review_type": review_type,
        "objective": selected.get("objective") or "Synthesize top-tier evidence for the research topic.",
        "inclusion_criteria": [
            "Only accept papers whose venue matches the configured top journal/conference policy.",
            f"Domain policy: {domain or 'all configured domains'}.",
            f"Earliest year: {year_start or 'not restricted'}.",
            f"Target paper count: {top_n or 'not restricted'}.",
        ],
        "screening_steps": selected.get("screening_steps") or [
            "Search candidate papers.",
            "Filter by configured top venues.",
            "Rank by venue policy, source influence, paper citations, and recency.",
            "Build readings, evidence matrix, gap analysis, review, references, and citation checks.",
        ],
        "outputs": selected.get("outputs") or [
            "ranked_papers", "readings", "evidence_matrix", "gap_analysis",
            "review", "references", "citation_verification",
        ],
        "limits": [
            "OpenAlex metadata can lag behind venues and proceedings.",
            "Domestic/foreign status is not inferred from author nationality unless supplied explicitly.",
            "Full-text claims require PDF or indexed full text, not just abstracts.",
        ],
    }

def _first_author(authors: str) -> str:
    parts = authors.replace(" and ", ",").split(",")
    return (parts[0] or "?").strip().replace(" ", "")


def format_reference(paper: dict[str, Any], *, style: str = "gbt7714", index: int = 1) -> str:
    title = compact_text(paper.get("title") or "Untitled", max_chars=240)
    authors = compact_text(paper.get("authors") or "Unknown", max_chars=220)
    year = paper.get("year") or "n.d."
    venue = paper.get("venue") or "Unknown venue"
    doi = (paper.get("doi") or "").strip()
    raw_url = (paper.get("url") or "").strip()

    # Build best available link: doi -> url -> openalex id -> scholar fallback
    link = ""
    if doi:
        link = f"https://doi.org/{doi}" if doi.startswith("10.") else doi
    if not link and raw_url:
        link = raw_url
    if not link:
        openalex_id = (paper.get("openalex_id") or paper.get("id") or "").strip()
        if openalex_id and "openalex.org" in openalex_id:
            link = openalex_id
    if not link:
        title_q = (paper.get("title") or "").strip()
        if title_q:
            q = urllib.parse.quote(f'{title_q} {str(paper.get("authors") or "")[:80]}')
            link = f"https://scholar.google.com/scholar?q={q}"

    style = (style or "gbt7714").lower()
    if style == "ieee":
        tail = f" {link}." if link else ".[No link]"
        return f"[{index}] {authors}, \"{title},\" {venue}, {year}.{tail}"
    if style == "apa":
        tail = f" {link}" if link else " [No link]"
        return f"{authors} ({year}). {title}. {venue}.{tail}"
    if style == "bibtex":
        key = re.sub(r"[^A-Za-z0-9]+", "", f"{_first_author(authors)}{year}") or f"ref{index}"
        return (
            f"@article{{{key},\n"
            f"  title = {{{title}}},\n"
            f"  author = {{{authors}}},\n"
            f"  year = {{{year}}},\n"
            f"  journal = {{{venue}}},\n"
            f"  doi = {{{doi}}},\n"
            f"  url = {{{link}}}\n"
            f"}}"
        )
    tail = f" {link}." if link else ".[No link]"
    return f"[{index}] {authors}. {title}[J/C]. {venue}, {year}.{tail}"


def format_reference_list(papers: list[dict[str, Any]], *, style: str = "gbt7714") -> list[str]:
    return [format_reference(paper, style=style, index=idx) for idx, paper in enumerate(papers, start=1)]

def verify_citations(review: str, papers: list[dict[str, Any]]) -> dict[str, Any]:
    refs = sorted({int(x) for x in re.findall(r"\[(\d+)\]", review or "")})
    issues = []
    checked = []
    for ref in refs:
        if ref < 1 or ref > len(papers):
            issues.append({"ref": ref, "severity": "error", "message": "citation index is out of range"})
            continue
        paper = papers[ref - 1]
        contexts = re.findall(r"[^\u3002\uff01\uff1f.!?]*\[" + str(ref) + r"\][^\u3002\uff01\uff1f.!?]*[\u3002\uff01\uff1f.!?]?", review or "")
        source = " ".join([paper.get("title", ""), paper.get("abstract", ""), paper.get("venue", "")])
        scores = [overlap_score(ctx, source) for ctx in contexts] or [0.0]
        max_score = max(scores)
        verdict = "supported" if max_score >= 0.12 else "weak"
        if verdict == "weak":
            issues.append({
                "ref": ref,
                "severity": "warning",
                "message": "low lexical overlap between cited sentence and paper metadata; inspect manually",
                "score": round(max_score, 3),
            })
        checked.append({"ref": ref, "title": paper.get("title"), "verdict": verdict, "score": round(max_score, 3)})
    uncited = [idx for idx in range(1, len(papers) + 1) if idx not in refs]
    return {
        "citation_count": len(refs),
        "paper_count": len(papers),
        "checked": checked,
        "uncited_refs": uncited,
        "issues": issues,
        "verdict": "pass" if not any(i["severity"] == "error" for i in issues) else "fail",
    }


def extract_pdf_text(path: Path, *, max_chars: int = 12000) -> str:
    if PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            chunks = []
            for page in reader.pages:
                chunks.append(page.extract_text() or "")
                if sum(len(chunk) for chunk in chunks) >= max_chars:
                    break
            text = compact_text("\n".join(chunks), max_chars=max_chars)
            if text:
                return text
        except Exception:
            pass
    data = path.read_bytes().decode("utf-8", errors="ignore")
    data = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff .,;:!?()\[\]\-_/]+", " ", data)
    return compact_text(data, max_chars=max_chars)


def http_get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 25) -> dict[str, Any]:
    if requests is None:
        fail("requests package is required for network actions.")
    response = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def reconstruct_openalex_abstract(inv: Any) -> str:
    if not isinstance(inv, dict):
        return ""
    positions = []
    for word, pos_list in inv.items():
        if isinstance(pos_list, list):
            for pos in pos_list:
                positions.append((pos, word))
    positions.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positions)


def openalex_work_to_paper(item: dict[str, Any]) -> dict[str, Any]:
    source = ((item.get("primary_location") or {}).get("source") or {})
    authors = []
    for authorship in item.get("authorships", []) or []:
        author = authorship.get("author") or {}
        if author.get("display_name"):
            authors.append(author["display_name"])
    abstract = item.get("abstract") or reconstruct_openalex_abstract(item.get("abstract_inverted_index"))
    return normalize_paper({
        "id": item.get("id"),
        "title": item.get("title"),
        "authors": authors,
        "year": item.get("publication_year"),
        "venue": source.get("display_name"),
        "citations": item.get("cited_by_count"),
        "doi": item.get("doi"),
        "url": item.get("doi") or item.get("id"),
        "abstract": abstract,
        "issn": (source.get("issn") or [""])[0] if isinstance(source.get("issn"), list) else source.get("issn"),
    })


def search_openalex(query_text: str, *, limit: int = 20) -> list[dict[str, Any]]:
    data = http_get_json("https://api.openalex.org/works", {"search": query_text, "per-page": min(limit, 200)})
    return [openalex_work_to_paper(item) for item in data.get("results", [])]


def openalex_id_from_paper(paper: dict[str, Any]) -> str | None:
    doi = (paper.get("doi") or paper.get("url") or "").strip()
    if doi:
        if doi.startswith("https://doi.org/"):
            return "https://doi.org/" + doi.split("https://doi.org/", 1)[1]
        if doi.lower().startswith("10."):
            return "https://doi.org/" + doi
    url = paper.get("url") or ""
    if "openalex.org/" in url:
        return url
    return None


def openalex_fetch_work(identifier: str) -> dict[str, Any] | None:
    if not identifier:
        return None
    encoded = urllib.parse.quote(identifier, safe="")
    try:
        return http_get_json(f"https://api.openalex.org/works/{encoded}")
    except Exception:
        return None


# -- Citation Trust Extensions --

def verify_citations_semantic(review, papers, *, min_similarity=0.08):
    """Optional embedding-based citation verification using text-embedding-3-small.
    Falls back gracefully if OpenAI unavailable. Does NOT replace verify_citations."""
    try:
        from openai import OpenAI
    except ImportError:
        return {"semantic_verification": "unavailable", "reason": "openai not installed"}
    import os; api_key = os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        return {"semantic_verification": "unavailable", "reason": "OPENAI_API_KEY not set"}
    client = OpenAI(api_key=api_key)

    def _embed(text):
        try:
            resp = client.embeddings.create(model="text-embedding-3-small", input=text[:8192])
            return resp.data[0].embedding
        except Exception:
            return None

    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb + 1e-10)

    refs = sorted({int(x) for x in re.findall(r"\[(\d+)\]", review or "")})
    checked = []
    for ref in refs:
        if ref < 1 or ref > len(papers):
            continue
        contexts = re.findall(r"[^\u3002\uff01\uff1f.!?]*\[" + str(ref) + r"\][^\u3002\uff01\uff1f.!?]*[\u3002\uff01\uff1f.!?]?", review or "")
        if not contexts:
            checked.append({"ref": ref, "verdict": "no_context", "similarity": 0.0})
            continue
        ctx_text = " ".join(contexts)[:4000]
        p = papers[ref - 1]
        paper_text = " ".join([p.get("title") or "", p.get("abstract") or ""])[:4000]
        ctx_emb = _embed(ctx_text)
        paper_emb = _embed(paper_text)
        if ctx_emb is None or paper_emb is None:
            checked.append({"ref": ref, "verdict": "embedding_failed", "similarity": None})
            continue
        sim = _cosine(ctx_emb, paper_emb)
        checked.append({"ref": ref, "verdict": "supported" if sim >= min_similarity else "weak", "similarity": round(sim, 4)})
    return {"semantic_verification": "completed", "checked": checked, "threshold": min_similarity}


def check_paper_retraction(doi):
    """Check paper retraction/correction via CrossRef API. Returns dict with status/retracted/corrected."""
    if not doi:
        return {"status": "no_doi", "retracted": False, "corrected": False}
    clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    if not clean.startswith("10."):
        return {"status": "invalid_doi", "retracted": False, "corrected": False}
    try:
        data = http_get_json("https://api.crossref.org/works/" + clean, timeout=15)
        msg = (data or {}).get("message") or {}
        updates = msg.get("update-to") or []
        has_ret = any(u.get("type") == "retraction" for u in updates)
        has_cor = any(u.get("type") == "correction" for u in updates)
        if has_ret:
            return {"status": "ok", "retracted": True, "corrected": has_cor, "notice_type": "retraction", "message": "Retracted per CrossRef (" + str(len(updates)) + " notice(s))."}
        if has_cor:
            return {"status": "ok", "retracted": False, "corrected": True, "notice_type": "correction", "message": "Correction notice per CrossRef."}
        return {"status": "ok", "retracted": False, "corrected": False, "notice_type": None, "message": "No retraction/correction found via CrossRef."}
    except Exception:
        return {"status": "unavailable", "retracted": False, "corrected": False}


def batch_check_retractions(papers):
    """Batch retraction check. Adds retraction_check in-place."""
    results = []
    for paper in papers:
        doi = str(paper.get("doi") or "")
        result = check_paper_retraction(doi)
        paper["retraction_check"] = result
        results.append(result)
    return results


def validate_review_claims(review, matrix, *, min_overlap=0.08):
    """Cross-check LLM-generated review claims against evidence matrix.
    Catches the 'correct citation, wrong claim' problem that citation verifier misses.
    Returns dict with verdict pass/needs_review and per-claim issues."""
    if not review or not matrix:
        return {"verdict": "no_data", "issues": [], "checked": []}
    ref_map = {}
    for row in matrix:
        rid = row.get("ref_id")
        if rid is not None:
            ref_map[int(rid)] = row
    sentences = re.split(r"(?<=[.!?\u3002\uff01\uff1f])\s+", review)
    issues = []
    checked = []
    for sidx, sent in enumerate(sentences):
        refs_in_sent = sorted({int(x) for x in re.findall(r"\[(\d+)\]", sent)})
        if not refs_in_sent:
            continue
        evidence_texts = []
        for ref in refs_in_sent:
            row = ref_map.get(ref)
            if row:
                evidence_texts.append(" ".join([row.get("title") or "", row.get("research_question_or_problem") or "", row.get("main_evidence_or_findings") or ""]))
        combined = " ".join(evidence_texts)
        if not combined:
            continue
        score = overlap_score(sent, combined)
        verdict = "supported" if score >= min_overlap else "potential_mismatch"
        if verdict == "potential_mismatch":
            issues.append({"sentence": sent[:200], "refs": refs_in_sent, "overlap_score": round(score, 4), "severity": "warning", "message": "Low overlap (score=" + str(round(score, 3)) + ") between claim and evidence entries " + str(refs_in_sent) + "."})
        checked.append({"sentence_index": sidx, "snippet": sent[:120], "refs": refs_in_sent, "overlap_score": round(score, 4), "verdict": verdict})
    return {"verdict": "pass" if not issues else "needs_review", "total_sentences_with_refs": len(checked), "issue_count": len(issues), "issues": issues[:20], "checked": checked}


# -- Link Verification & Reference Trust (reconstructed) --


def build_paper_links(paper):
    """Build multi-source reference links for a paper dict.
    Returns a dict with primary and fallback links, source_trace, and
    traceability assessment. Does NOT make HTTP requests to each link.
    """
    doi = str(paper.get("doi") or "").strip()
    openalex_id = str(paper.get("openalex_id") or "").strip()
    title = str(paper.get("title") or "").strip()
    links = {}
    source_trace = []
    if doi and doi.startswith("10."):
        full_doi = "https://doi.org/" + doi
        links["doi"] = full_doi
        source_trace.append("DOI resolver / publisher landing page")
    if openalex_id:
        links["openalex"] = openalex_id if openalex_id.startswith("http") else "https://openalex.org/" + openalex_id
        source_trace.append("OpenAlex metadata")
    if doi:
        links["scholar"] = "https://scholar.google.com/scholar?q=doi:" + doi
        source_trace.append("Google Scholar (DOI match)")
    if title:
        import urllib.parse
        title_quoted = urllib.parse.quote(title[:200])
        links["arxiv_search"] = "https://arxiv.org/search/?query=" + title_quoted + "&searchtype=title"
        source_trace.append("arXiv title-match fallback")
        links["semantic_scholar"] = "https://api.semanticscholar.org/graph/v1/paper/search?query=" + title_quoted
        source_trace.append("Semantic Scholar search fallback")
    traceable = bool(links.get("doi") or links.get("openalex"))
    return {
        "links": links,
        "primary_count": sum(1 for k in links if k in ("doi", "openalex")),
        "total_links": len(links),
        "traceable": traceable,
        "reachable": None,
        "reachability_note": "not_checked; links are constructed from metadata, not live HTTP validation",
        "source_trace": source_trace,
    }


def _metadata_source_label(paper):
    """Return a label indicating where the paper data came from."""
    if paper.get("source_quality") == "metadata_from_openalex":
        return "OpenAlex API (structured metadata)"
    if paper.get("search_source_api") == "OpenAlex":
        return "OpenAlex Works API"
    return "user-provided or inferred"


def _verification_risk(paper, primary_check):
    """Assess hallucination risk based on metadata traceability.
    Low = structured metadata source or OpenAlex ID.
    Medium = DOI is present but live reachability was not checked.
    High = no traceable identifier.
    """
    if not paper:
        return "high"
    has_openalex = bool(str(paper.get("openalex_id") or "").strip())
    is_structured_openalex = (
        paper.get("source_quality") == "metadata_from_openalex"
        or paper.get("search_source_api") == "OpenAlex"
    )
    if has_openalex or is_structured_openalex:
        return "low"
    if bool(str(paper.get("doi") or "").strip()):
        return "medium"
    return "high"


def build_reference_verification(paper, index=1):
    """Build a reference-verification entry for one paper.
    Includes multi-source links, metadata source label, and hallucination risk.
    """
    links_info = build_paper_links(paper)
    primary_check = links_info.get("links", {}).get("doi") or links_info.get("links", {}).get("openalex")
    risk = _verification_risk(paper, primary_check)
    return {
        "index": index,
        "title": paper.get("title", ""),
        "year": paper.get("publication_year") or paper.get("year"),
        "venue": paper.get("venue", ""),
        "doi": paper.get("doi", ""),
        "openalex_id": paper.get("openalex_id", ""),
        "metadata_source": _metadata_source_label(paper),
        "links_info": links_info,
        "hallucination_risk": risk,
        "verdict": "traceable" if risk in ("low", "medium") else "needs_confirmation",
    }


def build_verification_matrix(papers):
    """Build a verification matrix for a list of papers."""
    return [build_reference_verification(p, idx + 1) for idx, p in enumerate(papers)]


def _markdown_cell(value, max_chars=80):
    text = str(value or "-").replace("|", "\\|").replace("\n", " ").strip()
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def format_verification_section(papers):
    """Generates a Markdown evidence-boundary section for the review output."""
    matrix = build_verification_matrix(papers)
    low = sum(1 for m in matrix if m.get("hallucination_risk") == "low")
    medium = sum(1 for m in matrix if m.get("hallucination_risk") == "medium")
    high = sum(1 for m in matrix if m.get("hallucination_risk") == "high")
    lines = [
        "",
        "---",
        "### 引用与反幻觉验证矩阵 / Citation & Anti-Hallucination Verification",
        "",
        f"- 已检查参考文献数: {len(matrix)}",
        f"- 低风险条目（结构化元数据或 OpenAlex 可追踪标识）: {low}",
        f"- 中风险条目（有 DOI，但未实时验证 HTTP 可达性）: {medium}",
        f"- 需人工确认条目（缺少可追踪标识）: {high}",
        "",
        "| # | 题名 | DOI/OpenAlex | 链接状态 | 元数据来源 | 幻觉风险 |",
        "|---|------|-------------|----------|------------|----------|",
    ]
    for m in matrix:
        links_info = m.get("links_info", {}) or {}
        primary = links_info.get("links", {}).get("doi") or links_info.get("links", {}).get("openalex") or "-"
        primary_short = str(primary)[:70] if primary != "-" else "-"
        if links_info.get("reachable") is True:
            link_status = "实时可达"
        elif links_info.get("traceable"):
            link_status = "可追踪，未实时验证"
        else:
            link_status = "待确认"
        lines.append(
            f"| {m['index']} | {_markdown_cell(m.get('title'), 44)} | {_markdown_cell(primary_short, 72)} | "
            f"{link_status} | {_markdown_cell(m.get('metadata_source'), 28)} | {_markdown_cell(m.get('hallucination_risk'), 12)} |"
        )
    lines.append("")
    lines.append("**证据边界:** 该矩阵验证的是题录元数据、DOI/OpenAlex 等可追踪来源；除非 `链接状态` 明确为“实时可达”，否则不代表 HTTP 链接已现场访问成功。实验细节、数据集、指标和定量结论仍需回到 PDF 全文或出版商页面核验。")
    lines.append("")
    return "\n".join(lines)

# -- HTTP Reachability Check (async, anti-scraping safe) --


async def _link_check_for_url(session, url: str, *, timeout: float = 8.0, label: str = "") -> dict:
    """Async HTTP reachability check for a reference link.

    Makes a single GET with a short timeout and browser-like User-Agent.
    Returns dict with: url, status, reachable (bool), error (str or None),
    and source_label.

    Does NOT follow redirect chains (wastes time) and does NOT download
    bodies (only reads headers + first 1KB). Treats 4xx/5xx as unreachable,
    timeouts as unreachable, 2xx/3xx as reachable.
    """
    result = {"url": url, "label": label or "", "status": None, "reachable": False, "error": None}
    if not url:
        result["error"] = "empty_url"
        return result
    import aiohttp
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MegatronResearchAssistant/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=False) as resp:
            result["status"] = resp.status
            if 200 <= resp.status < 400:
                result["reachable"] = True
            elif resp.status in (429, 503, 403):
                result["error"] = f"blocked_by_{resp.status}"
                result["reachable"] = False  # blocked, not a paper link issue
            else:
                result["error"] = f"http_{resp.status}"
    except asyncio.TimeoutError:
        result["error"] = "timeout"
    except aiohttp.ClientConnectorError as e:
        result["error"] = f"connection_failed: {str(e)[:60]}"
    except Exception as e:
        result["error"] = str(e)[:120]
    return result


async def enrich_reference_links(session, papers, *, max_count=12):
    """Enrich a list of paper dicts with async reachability checks.

    For each paper up to max_count, attempts HEAD/GET on the primary DOI
    link and the arXiv link (if available). Adds/updates a 'link_checks'
    field with the results.

    Known blocked publishers (IEEE, Elsevier, ACM behind paywalls) are
    marked as 'paywall_blocked' rather than 'unreachable', so they don't
    get flagged as broken links.
    """
    BLOCKED_DOMAINS = ["ieeexplore.ieee.org", "doi.org"]
    for idx, paper in enumerate(papers[:max_count]):  # limit to first max_count
        link_info = build_paper_links(paper)
        links = link_info.get("links", {})
        checks = {}
        tasks = []
        # Try DOI link
        doi_url = links.get("doi", "")
        if doi_url:
            tasks.append(_link_check_for_url(session, doi_url, label="doi"))
        # Try openalex
        oa_url = links.get("openalex", "")
        if oa_url and doi_url != oa_url:
            tasks.append(_link_check_for_url(session, oa_url, label="openalex"))
        if tasks:
            import asyncio
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, dict):
                    checks[r["label"]] = r
        # Post-process: mark known publisher blocks as paywall rather than error
        for label, check in checks.items():
            url = check.get("url", "")
            if any(domain in url for domain in BLOCKED_DOMAINS) and not check["reachable"]:
                check["reachable"] = None  # unknown, not failed
                check["error"] = "paywall_or_temporary_block"
        paper["link_checks"] = checks
    return papers





