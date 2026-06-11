from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import (  # noqa: E402
    build_paper_links,
    build_reference_verification,
    build_verification_matrix,
    compact_text,
    format_reference_list,
    format_verification_section,
    is_top_venue_configured,
    match_top_venue,
    reconstruct_openalex_abstract,
    venue_policy_summary,
    venue_score,
    venue_standard_source,
    venue_standard_tags,
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MegatronResearchAssistant/1.0"
}

ACTION_ALIASES = {
    "search": "fetch",
}

DOMAIN_ALIASES = {
    "information_systems": "management",
    "information systems": "management",
    "information-system": "management",
    "mis": "management",
    "business": "management",
    "biz": "management",
    "xin guan": "management",
    "信管": "management",
    "信息管理": "management",
    "information_management": "management",
    "information management": "management",
    "信息系统": "management",
    "human_computer_interaction": "hci",
    "human-computer-interaction": "hci",
    "human computer interaction": "hci",
}

MANAGEMENT_MARKERS = (
    "信管",
    "信息管理",
    "信息系统",
    "管理信息系统",
    "information management",
    "information systems",
    "management information systems",
    "mis quarterly",
    "digital platform",
    "digital transformation",
    "knowledge management",
)

HCI_MARKERS = (
    "人机协同",
    "人机合作",
    "人机交互",
    "human-ai collaboration",
    "human ai collaboration",
    "human-machine collaboration",
    "human machine collaboration",
    "human-ai teaming",
    "human ai teaming",
    "human-computer interaction",
    "human computer interaction",
    "hci",
)

QUERY_REPLACEMENTS = {
    "人机协同": "human AI collaboration",
    "人机合作": "human AI collaboration",
    "人机交互": "human computer interaction",
    "人机": "human AI",
    "信息系统": "information systems",
    "信息管理": "information management",
    "管理信息系统": "management information systems",
    "信管": "information systems",
    "智能体记忆": "agent memory",
    "智能体": "agent",
    "论文": "paper",
    "文献": "paper",
    "综述": "review",
}

QUERY_FILLER_PATTERN = re.compile(
    r"\b20[2-9]\d\b|[?]{2,}|以来|之后|以后|给出链接|给我|帮我|查一下|找一下|相关|研究成果"
)

RELEVANCE_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "paper", "papers",
    "study", "studies", "research", "review", "survey", "using", "based", "large", "model", "models",
    "2024", "2025", "2026", "信息", "系统", "论文", "文献", "研究", "综述",
}

CHINESE_FILLER_TERMS = (
    "\u5e74\u4ee5\u6765",
    "\u4ee5\u6765",
    "\u4ee5\u540e",
    "\u4e4b\u540e",
    "\u9886\u57df",
    "\u7814\u7a76\u6210\u679c",
    "\u8bba\u6587",
    "\u6587\u732e",
    "\u7ed9\u51fa\u94fe\u63a5",
    "\u7ed9\u6211",
    "\u5e2e\u6211",
    "\u67e5\u4e00\u4e0b",
    "\u627e\u4e00\u4e0b",
    "\u76f8\u5173",
)

MEDICAL_MARKERS = (
    "biomedical",
    "cancer",
    "clinic",
    "clinical",
    "diagnosis",
    "diagnostic",
    "disease",
    "healthcare",
    "hospital",
    "medical",
    "medicine",
    "oncology",
    "patient",
    "pathology",
    "radiology",
)


def normalize_action(value: Any) -> str:
    action = str(value or "fetch").strip().lower()
    return ACTION_ALIASES.get(action, action)


def _normalized_domain_value(value: Any) -> str | None:
    if value in (None, ""):
        return None
    raw = str(value).strip().lower()
    if raw in {"auto", "default", "all", "any", "none", "null"}:
        return None
    canonical = raw.replace("-", "_")
    return DOMAIN_ALIASES.get(canonical) or DOMAIN_ALIASES.get(raw) or canonical


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def infer_effective_domain(query: str, requested_domain: Any = None) -> str | list[str] | None:
    if isinstance(requested_domain, list) and len(requested_domain) > 0:
        normalized = []
        for d in requested_domain:
            nd = _normalized_domain_value(d)
            if nd:
                normalized.append(nd)
        return normalized if normalized else None
    domain = _normalized_domain_value(requested_domain)
    if _contains_any(query, MANAGEMENT_MARKERS):
        return "management"
    if _contains_any(query, HCI_MARKERS) and domain in (None, "ai", "cs"):
        return "hci"
    return domain


def refine_query_for_domain(query: str, domain: str | list[str] | None) -> str:
    query = str(query or "").strip()
    lowered = query.lower()
    additions: list[str] = []
    if domain == "management":
        if "information systems" not in lowered and "mis" not in lowered:
            additions.extend(["information systems", "management information systems"])
        if "digital" not in lowered:
            additions.append("digital")
    elif domain == "hci":
        if not any(marker in lowered for marker in ["human ai", "human-ai", "human machine", "human-machine"]):
            additions.append("human AI collaboration")
    for item in additions:
        if item not in lowered:
            query = f"{query} {item}".strip()
            lowered = query.lower()
    return query


def compact_repeated_token_sequences(value: str) -> str:
    tokens = value.split()
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        for size in range(len(tokens) // 2, 0, -1):
            for start in range(0, len(tokens) - (size * 2) + 1):
                left = [token.lower() for token in tokens[start:start + size]]
                right = [token.lower() for token in tokens[start + size:start + (size * 2)]]
                if left == right:
                    tokens = tokens[:start + size] + tokens[start + (size * 2):]
                    changed = True
                    break
            if changed:
                break
    return " ".join(tokens)


def clean_search_query(query: str) -> str:
    value = str(query or "")
    for old, new in QUERY_REPLACEMENTS.items():
        value = value.replace(old, f" {new} ")
    for filler in CHINESE_FILLER_TERMS:
        value = value.replace(filler, " ")
    value = QUERY_FILLER_PATTERN.sub(" ", value)
    value = re.sub(r"20[2-9]\d", " ", value)
    value = re.sub(r"[-_/]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = compact_repeated_token_sequences(value)
    return value


def build_query_variants(query: str, domain: str | list[str] | None) -> list[str]:
    cleaned = clean_search_query(query)
    variants: list[str] = []
    for item in [cleaned, refine_query_for_domain(cleaned, domain)]:
        if item and item.lower() not in {v.lower() for v in variants}:
            variants.append(item)
    lowered = cleaned.lower()
    if "human" in lowered and any(x in lowered for x in ["collaboration", "cooperation", "teaming", "machine", "ai"]):
        for item in [
            "human AI collaboration information systems",
            "human AI collaboration systematic review meta analysis",
            "human machine collaboration human AI teaming",
        ]:
            if item.lower() not in {v.lower() for v in variants}:
                variants.append(item)
    if domain == "management" and "information systems" in lowered:
        for item in [
            "human AI collaboration information systems",
            "human generative AI collaboration information systems",
        ]:
            if item.lower() not in {v.lower() for v in variants}:
                variants.append(item)
    return variants[:5]


def _tokenize_relevance(text: str) -> set[str]:
    tokens = re.findall(r"[a-z][a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", (text or "").lower())
    return {tok for tok in tokens if tok not in RELEVANCE_STOPWORDS}


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in markers)


def paper_relevance_score(query: str, paper: dict[str, Any]) -> float:
    cleaned_query = clean_search_query(query)
    query_lower = cleaned_query.lower()
    doc = " ".join([
        str(paper.get("title") or ""),
        str(paper.get("abstract") or ""),
        str(paper.get("venue") or ""),
    ]).lower()
    query_tokens = _tokenize_relevance(cleaned_query)
    doc_tokens = _tokenize_relevance(doc)
    overlap = len(query_tokens & doc_tokens)
    score = float(overlap)

    query_human = _has_any(query_lower, ("human", "人机"))
    query_collab = _has_any(query_lower, ("collaboration", "cooperation", "teaming", "collaborative", "协同", "合作"))
    query_is = _has_any(query_lower, ("information systems", "information system", "信息系统", "信管"))
    query_agent_memory = _has_any(query_lower, ("agent memory", "智能体记忆"))

    doc_human = _has_any(doc, ("human", "humans", "human-ai", "human ai", "human-machine", "human machine", "人机"))
    doc_collab = _has_any(doc, ("collaboration", "collaborative", "cooperation", "teaming", "combination", "combinations", "协同", "合作"))
    doc_is = _has_any(doc, ("information systems", "information system", "association for information systems", "mis quarterly", "信息系统"))
    doc_agent_memory = _has_any(doc, ("agent memory", "memory mechanism", "long-term memory", "智能体记忆"))

    doc_agent_memory = doc_agent_memory or _has_any(doc, (
        "memory module",
        "memory modules",
        "memory and action",
        "profile, memory",
    ))

    if query_human and doc_human:
        score += 2
    if query_collab and doc_collab:
        score += 2
    if query_is and doc_is:
        score += 2
    if query_agent_memory and doc_agent_memory:
        score += 3
    elif query_agent_memory:
        score -= 2
    if not _has_any(query_lower, MEDICAL_MARKERS) and _has_any(doc, MEDICAL_MARKERS):
        score -= 3
    return score


def relevance_threshold(query: str) -> float:
    cleaned_query = clean_search_query(query).lower()
    focused = 0
    if _has_any(cleaned_query, ("human", "人机")):
        focused += 1
    if _has_any(cleaned_query, ("collaboration", "cooperation", "teaming", "协同", "合作")):
        focused += 1
    if _has_any(cleaned_query, ("information systems", "information system", "信息系统", "信管")):
        focused += 1
    if _has_any(cleaned_query, ("agent memory", "智能体记忆")):
        focused += 1
    if _has_any(cleaned_query, ("agent memory",)):
        return 3.0
    if focused >= 3:
        return 4.0
    if focused >= 2:
        return 3.0
    return 1.0


def filter_by_relevance(papers: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    threshold = relevance_threshold(query)
    scored = []
    for paper in papers:
        score = paper_relevance_score(query, paper)
        if score >= threshold:
            paper["relevance_score"] = round(score, 3)
            scored.append(paper)
    scored.sort(key=lambda item: float(item.get("relevance_score") or 0), reverse=True)
    return scored


async def search_papers_openalex_variants(
    session: aiohttp.ClientSession,
    queries: list[str],
    limit: int = 100,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    combined: list[dict[str, Any]] = []
    per_query_limit = min(max(limit, 50), 200)
    for query in queries:
        for paper in await search_papers_openalex(session, query, limit=per_query_limit):
            key = str(paper.get("doi") or paper.get("url") or paper.get("title") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            paper["search_source_query"] = query
            combined.append(paper)
    return combined


def optional_int(value: Any, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_llm_config_from_project() -> dict[str, str]:
    for parent in Path(__file__).resolve().parents:
        config_path = parent / "model.toml"
        if not config_path.exists():
            continue
        try:
            with config_path.open("rb") as fh:
                data = tomllib.load(fh)
            llm = data.get("llm", {})
            provider = llm.get("active_provider", "openai")
            cfg = llm.get(provider, {})
            if isinstance(cfg, dict):
                return {
                    "api_key": str(cfg.get("api_key") or ""),
                    "base_url": str(cfg.get("base_url") or ""),
                    "model": str(cfg.get("model") or ""),
                }
        except Exception:
            return {}
    return {}


def normalize_doi(value: str) -> str:
    value = (value or "").strip()
    return value.replace("https://doi.org/", "").replace("http://doi.org/", "")


def reconstruct_abstract(inverted_index: Any) -> str:
    return reconstruct_openalex_abstract(inverted_index)


def is_plausible_paper(paper: dict[str, Any]) -> bool:
    title = str(paper.get("title") or "").strip()
    if not title:
        return False
    year = paper.get("publication_year")
    if year is None:
        return False
    try:
        year_int = int(year)
    except (TypeError, ValueError):
        return False
    return 1900 <= year_int <= datetime.now().year + 1


def openalex_item_to_candidate(item: dict[str, Any]) -> dict[str, Any]:
    primary_loc = item.get("primary_location") or {}
    source = primary_loc.get("source") or {}
    issn_list = source.get("issn", []) or []
    issn_val = issn_list[0] if isinstance(issn_list, list) and issn_list else ""
    abstract = item.get("abstract") or reconstruct_abstract(item.get("abstract_inverted_index"))
    authors = []
    for auth in item.get("authorships", []) or []:
        author = auth.get("author") or {}
        if author.get("display_name"):
            authors.append(author["display_name"])
    return {
        "title": item.get("title") or "",
        "publication_year": item.get("publication_year"),
        "authors": authors,
        "venue": source.get("display_name") or "",
        "cited_by_count": item.get("cited_by_count") or 0,
        "doi": normalize_doi(item.get("doi") or ""),
        "openalex_id": item.get("id") or "",
        "url": item.get("doi") or item.get("id") or "",
        "abstract": abstract or "",
        "source_id": source.get("id") or "",
        "issn_clean": re.sub(r"[^0-9Xx]", "", str(issn_val)).upper(),
        "primary_location": primary_loc,
        "search_source_api": "OpenAlex",
    }


async def search_papers_openalex(session: aiohttp.ClientSession, query: str, limit: int = 100) -> list[dict[str, Any]]:
    url = "https://api.openalex.org/works"
    params = {"search": query, "per-page": min(max(limit, 1), 200)}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=25)) as response:
            response.raise_for_status()
            data = await response.json()
            results = data.get("results", [])
            return [openalex_item_to_candidate(item) for item in results if isinstance(item, dict)]
    except Exception:
        return []


def filter_papers(papers: list[dict[str, Any]], *, year_start: int | None = None,
                  domain: str | list[str] | None = None, venues: list[str] | None = None,
                  venue_mode: str = "strict") -> list[dict[str, Any]]:
    """Filter papers by year and venue policy.

    venue_mode:
      - "strict":     Only top-tier venues (citation/verification use). Default.
      - "inclusive":  ALL venues, but tag tier (gap/discovery use).
      - "unfiltered": No venue filter at all (broad exploration).
    """
    valid = []
    for paper in papers:
        if not is_plausible_paper(paper):
            continue
        pub_year = int(paper["publication_year"])
        if year_start is not None and pub_year < year_start:
            continue
        venue = str(paper.get("venue") or "")
        issn_clean = str(paper.get("issn_clean") or "")
        match = match_top_venue(venue, issn_clean, domain)
        is_top = is_top_venue_configured(venue, issn_clean, domain)

        if venue_mode == "strict":
            # Only top venues — for citation verification
            if not match or not is_top:
                continue
        elif venue_mode == "inclusive":
            # All venues, but tag the tier — for gap discovery
            pass  # Keep the paper, tier info added below
        # else "unfiltered" — keep everything

        venue_tier = match.get("tier") if match else (
            "unranked" if venue else "unknown"
        )
        valid.append({
            "title": paper.get("title", ""),
            "year": str(pub_year),
            "authors": ", ".join(paper.get("authors") or []),
            "venue": venue,
            "venue_policy_match": match.get("name") or venue if match else venue,
            "venue_tier": venue_tier,
            "venue_policy_source": venue_standard_source(match) if match else "unranked",
            "venue_policy_tags": venue_standard_tags(match) if match else [],
            "citations": paper.get("cited_by_count", 0),
            "url": paper.get("url", ""),
            "abstract": paper.get("abstract", "") or "",
            "doi": paper.get("doi", ""),
            "openalex_id": paper.get("openalex_id", ""),
            "source_id": paper.get("source_id", ""),
            "issn_clean": issn_clean,
            "search_source_api": paper.get("search_source_api", ""),
        })
    return valid


async def fetch_sources_scores(session: aiohttp.ClientSession, source_ids: list[str]) -> dict[str, int]:
    score_map: dict[str, int] = {}
    source_ids = [sid for sid in dict.fromkeys(source_ids) if sid]
    if not source_ids:
        return score_map
    url = "https://api.openalex.org/sources"
    params = {
        "filter": "openalex_id:" + "|".join(source_ids[:200]),
        "per-page": 200,
        "select": "id,cited_by_count",
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as response:
            response.raise_for_status()
            data = await response.json()
            for source in data.get("results", []):
                sid = source.get("id")
                if sid:
                    score_map[sid] = int(source.get("cited_by_count") or 0)
    except Exception:
        pass
    return score_map


async def fetch_abstract_from_semantic_scholar(session: aiohttp.ClientSession, doi: str, title: str) -> str:
    try:
        if doi:
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
            async with session.get(url, params={"fields": "abstract"}, timeout=aiohttp.ClientTimeout(total=12)) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("abstract"):
                        return str(data["abstract"]).strip()
        if title:
            url = "https://api.semanticscholar.org/graph/v1/paper/search"
            params = {"query": title, "limit": 1, "fields": "abstract"}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=12)) as response:
                if response.status == 200:
                    data = await response.json()
                    papers = data.get("data", [])
                    if papers and papers[0].get("abstract"):
                        return str(papers[0]["abstract"]).strip()
    except Exception:
        pass
    return ""


async def fetch_abstract_from_crossref(session: aiohttp.ClientSession, doi: str) -> str:
    if not doi:
        return ""
    try:
        url = f"https://api.crossref.org/works/{doi}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as response:
            if response.status != 200:
                return ""
            data = await response.json()
            abstract = data.get("message", {}).get("abstract", "")
            if abstract and BeautifulSoup:
                return BeautifulSoup(abstract, "html.parser").get_text(" ", strip=True)
            return re.sub(r"<[^>]+>", " ", str(abstract)).strip()
    except Exception:
        return ""


async def fetch_abstract_from_openalex(session: aiohttp.ClientSession, doi: str) -> str:
    if not doi:
        return ""
    try:
        url = "https://api.openalex.org/works/" + urllib.parse.quote(f"https://doi.org/{doi}", safe="")
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as response:
            if response.status != 200:
                return ""
            data = await response.json()
            return data.get("abstract") or reconstruct_abstract(data.get("abstract_inverted_index"))
    except Exception:
        return ""


async def fetch_abstract_from_europepmc(session: aiohttp.ClientSession, doi: str) -> str:
    if not doi:
        return ""
    try:
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {"query": f"DOI:{doi}", "format": "json", "resultType": "core"}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=12)) as response:
            if response.status != 200:
                return ""
            data = await response.json()
            results = data.get("resultList", {}).get("result", [])
            if results and results[0].get("abstractText"):
                return str(results[0]["abstractText"]).strip()
    except Exception:
        pass
    return ""


async def fetch_abstract_from_arxiv(session: aiohttp.ClientSession, title: str) -> str:
    if not title:
        return ""
    try:
        url = "https://export.arxiv.org/api/query"
        params = {"search_query": f'ti:"{title}"', "max_results": 1, "sortBy": "relevance"}
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=12)) as response:
            if response.status != 200:
                return ""
            text = await response.text()
            root = ET.fromstring(text)
            entries = root.findall("{http://www.w3.org/2005/Atom}entry")
            if not entries:
                return ""
            summary = entries[0].find("{http://www.w3.org/2005/Atom}summary")
            return summary.text.strip() if summary is not None and summary.text else ""
    except Exception:
        return ""


async def fill_one_abstract(session: aiohttp.ClientSession, paper: dict[str, Any]) -> None:
    if str(paper.get("abstract") or "").strip():
        return
    doi = str(paper.get("doi") or "")
    title = str(paper.get("title") or "")
    fetchers = [
        ("Semantic Scholar", lambda: fetch_abstract_from_semantic_scholar(session, doi, title)),
        ("Crossref", lambda: fetch_abstract_from_crossref(session, doi)),
        ("OpenAlex", lambda: fetch_abstract_from_openalex(session, doi)),
        ("Europe PMC", lambda: fetch_abstract_from_europepmc(session, doi)),
        ("arXiv", lambda: fetch_abstract_from_arxiv(session, title)),
    ]
    for source, factory in fetchers:
        abstract = await factory()
        if abstract:
            paper["abstract"] = compact_text(f"{abstract} [Source: {source}]", max_chars=5000)
            return


async def fill_missing_abstracts(session: aiohttp.ClientSession, papers: list[dict[str, Any]], *, max_count: int = 8) -> None:
    targets = [paper for paper in papers if not str(paper.get("abstract") or "").strip()][:max_count]
    if not targets:
        return
    await asyncio.gather(*(fill_one_abstract(session, paper) for paper in targets))


def generate_review_sync(papers: list[dict[str, Any]], query: str) -> str:
    if not OpenAI:
        raise RuntimeError("openai package not installed")
    file_cfg = load_llm_config_from_project()
    api_key = os.environ.get("OPENAI_API_KEY") or file_cfg.get("api_key", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable not set")
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or file_cfg.get("base_url", "")
    model = os.environ.get("OPENAI_MODEL") or file_cfg.get("model") or "gpt-4o"
    client_kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    def extracted_fact_notes(paper: dict[str, Any]) -> str:
        text = " ".join([
            str(paper.get("title") or ""),
            str(paper.get("abstract") or ""),
        ]).lower()
        notes: list[str] = []
        if "performed significantly worse than the best of humans or ai alone" in text:
            notes.append(
                "Overall comparison: human-AI combinations performed significantly worse than the best of humans or AI alone."
            )
        if "hedges" in text and "-0.23" in text:
            notes.append("Reported overall effect includes Hedges' g = -0.23.")
        if "performance losses" in text and "making decisions" in text:
            notes.append("Decision-making tasks showed performance losses.")
        if "greater gains" in text and "creat" in text:
            notes.append("Creative/content-generation tasks showed greater gains.")
        return " ".join(notes) if notes else "No deterministic factual notes extracted."

    papers_text = "\n\n".join(
        f"[{idx}] {paper['title']} ({paper.get('year') or 'n.d.'})\n"
        f"Venue: {paper.get('venue') or 'unknown'}; Citations: {paper.get('citations') or 0}\n"
        f"Extracted factual notes: {extracted_fact_notes(paper)}\n"
        f"Abstract: {compact_text(paper.get('abstract') or '', max_chars=900)}"
        for idx, paper in enumerate(papers, start=1)
    )
    prompt = f"""你是一名严谨的科研助手，正在围绕“{query}”撰写中文文献综述。

硬性要求：
1. 只能使用下列已通过顶刊/顶会白名单过滤的论文，不要编造额外论文。
2. 所有具体论文判断都必须用 [1]、[2] 这样的编号引用。
3. 国内外研究现状可以写，但如果给定论文不足以支撑“国内”或“国外”的具体判断，要明确写出证据边界。
4. 重点输出：研究现状、方法脉络、代表性证据、局限、研究空白、未来方向。

论文材料：
{papers_text}

请按以下结构输出：
一、研究背景与问题界定
二、国外研究现状
三、国内研究现状与证据边界
四、文献综述与方法分类
五、关键趋势、挑战与研究空白
六、结论
"""
    prompt = f"""You are a rigorous research assistant writing a Simplified Chinese literature review about "{query}".

Hard requirements:
1. Use only the papers listed below. Do not invent papers, venues, statistics, or citations.
2. Every concrete claim about a paper must cite its paper number, for example [1] or [2].
3. Preserve the polarity of empirical findings. If an abstract says a human-AI combination performed worse than the best individual, do not describe it as an overall improvement.
4. If the supplied papers are insufficient to support a claim about domestic or Chinese research status, explicitly state the evidence boundary.
5. Separate evidence-backed findings from inferred research gaps and future directions.
6. Use the user's time scope, such as "2024年以来", without inventing a stale cutoff date beyond the supplied metadata.

Paper materials:
{papers_text}

Output in Simplified Chinese with these sections:
一、研究背景与问题界定
二、研究现状
三、代表论文与主要发现
四、方法脉络与证据矩阵摘要
五、局限、研究空白与未来方向
六、结论"""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=4000,
    )
    return response.choices[0].message.content.strip()


async def fetch_top_papers_and_review(
    query: str,
    year: int | None = None,
    limit: int = 100,
    top_n: int | None = None,
    generate_review: bool = False,
    domain: str | None = None,
    domains: list[str] | None = None,
    venues: list[str] | None = None,
    fill_abstracts: bool = True,
    abstract_limit: int = 8,
) -> str:
    effective_domain = infer_effective_domain(query, domain)
    query_variants = build_query_variants(query, effective_domain)
    effective_query = query_variants[0] if query_variants else clean_search_query(query)
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        papers_list = await search_papers_openalex_variants(session, query_variants or [effective_query], limit=limit)
        venue_mode = str(params.get("venue_mode") or "strict")
        filtered = filter_papers(papers_list, year_start=year, domain=effective_domain, venue_mode=venue_mode)
        filtered = filter_by_relevance(filtered, query)
        source_scores = await fetch_sources_scores(session, [p.get("source_id", "") for p in filtered])

        for paper in filtered:
            policy_score = venue_score(paper.get("venue", ""), paper.get("issn_clean", ""), domain=effective_domain)
            source_score = source_scores.get(paper.get("source_id", ""), 0)
            paper["venue_policy_score"] = policy_score
            paper["source_score"] = source_score

        filtered.sort(
            key=lambda item: (
                float(item.get("relevance_score") or 0),
                int(item.get("venue_policy_score") or 0),
                int(item.get("source_score") or 0),
                int(item.get("citations") or 0),
                int(item.get("year") or 0),
            ),
            reverse=True,
        )
        if top_n and top_n > 0:
            filtered = filtered[:top_n]
        if fill_abstracts:
            await fill_missing_abstracts(session, filtered, max_count=max(0, abstract_limit))

    for idx, paper in enumerate(filtered, start=1):
        verification_entry = build_reference_verification(paper, idx)
        links_info = verification_entry.get("links_info", {})
        paper["reference_verification"] = verification_entry
        paper["links"] = links_info.get("links", {})
        paper["hallucination_risk"] = verification_entry.get("hallucination_risk")
        paper.pop("source_id", None)
        paper.pop("issn_clean", None)

    verification_matrix = build_verification_matrix(filtered)
    reference_verification = {
        "verdict": "metadata_verified" if filtered else "no_papers",
        "paper_count": len(filtered),
        "traceable_count": sum(1 for item in verification_matrix if item.get("verdict") == "traceable"),
        "low_risk_count": sum(1 for item in verification_matrix if item.get("hallucination_risk") == "low"),
        "medium_risk_count": sum(1 for item in verification_matrix if item.get("hallucination_risk") == "medium"),
        "high_risk_count": sum(1 for item in verification_matrix if item.get("hallucination_risk") == "high"),
        "needs_confirmation_count": sum(1 for item in verification_matrix if item.get("hallucination_risk") == "high"),
        "boundary": "Metadata, DOI/OpenAlex, and fallback search links are provided. Publisher pages and PDF-level claims still require manual/full-text verification.",
    }

    result: dict[str, Any] = {
        "status": "success",
        "query": query,
        "effective_query": effective_query,
        "query_variants": query_variants,
        "requested_domain": domain,
        "effective_domain": effective_domain,
        "relevance_threshold": relevance_threshold(query),
        "filter_mode": "top_venue",
        "venue_policy": venue_policy_summary(effective_domain),
        "total_fetched": len(papers_list),
        "valid_count": len(filtered),
        "papers": filtered,
        "references": format_reference_list(filtered),
        "verification_matrix": verification_matrix,
        "reference_verification": reference_verification,
    }
    if generate_review:
        if not filtered:
            result["review"] = "No valid top-venue papers found to generate a review."
        else:
            try:
                review = await asyncio.to_thread(generate_review_sync, filtered, query)
                if "引用与反幻觉验证矩阵" not in review and "Citation & Anti-Hallucination Verification" not in review:
                    review = review.rstrip() + "\n" + format_verification_section(filtered)
                result["review"] = review
            except Exception as exc:
                result["review"] = f"Review generation failed: {exc}"
    return json.dumps(result, ensure_ascii=False, indent=2)


def _read_args() -> dict[str, Any]:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        raise ValueError("Missing JSON argument.")
    value = json.loads(sys.argv[1])
    if not isinstance(value, dict):
        raise ValueError("Argument must be a JSON object.")
    return value


async def main() -> None:
    try:
        args = _read_args()
        action = normalize_action(args.get("action", "fetch"))
        if action not in {"fetch", "review", "policy"}:
            raise ValueError("action must be 'fetch', 'search', 'review', or 'policy'.")
        if action == "policy":
            query = str(args.get("query") or "")
            effective_domain = infer_effective_domain(query, args.get("domain"))
            print(json.dumps({
                "status": "success",
                "completed": True,
                "requested_domain": args.get("domain"),
                "effective_domain": effective_domain,
                "venue_policy": venue_policy_summary(effective_domain),
            }, ensure_ascii=False, indent=2))
            return
        query = args.get("query")
        if not query:
            raise ValueError("Missing required parameter: query.")
        result = await fetch_top_papers_and_review(
            query=str(query),
            year=optional_int(args.get("year") or args.get("year_start")),
            limit=optional_int(args.get("limit"), 100) or 100,
            top_n=optional_int(args.get("top_n")),
            generate_review=bool(args.get("generate_review", action == "review")),
            domain=args.get("domain"),
            fill_abstracts=bool(args.get("fill_abstracts", True)),
            abstract_limit=optional_int(args.get("abstract_limit"), 8) or 8,
        )
        parsed = json.loads(result)
        parsed["completed"] = True
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc), "completed": False}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())


