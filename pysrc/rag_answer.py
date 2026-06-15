"""Tri-Store Hybrid RAG — Answer Generation.

Assembles retrieved context (chunks + entities + communities) into a
well-structured prompt, generates the answer via LLM, formats citations,
and caches the result in Redis for future semantic matching.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CITATION_PATTERN = re.compile(r"【source:(.+?), chunk:(\d+)】")

DEFAULT_SYSTEM_PROMPT = """You are a knowledgeable research assistant answering questions based on provided context.
Follow these rules:
1. Answer ONLY based on the provided context. If the context doesn't contain enough information, say so.
2. Cite sources inline using the format 【source:doc_name, chunk:N】.
3. When entity relationships are provided, use them to connect information across sources.
4. When community context is provided, use it for broader understanding of the topic landscape.
5. Structure your answer clearly: direct answer first, then supporting details with citations.
6. For multi-hop questions, explain the reasoning chain explicitly."""


@dataclass
class GeneratedAnswer:
    text: str
    citations: List[Dict[str, Any]] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    model: str = ""
    elapsed_ms: float = 0.0


def assemble_context(retrieval_result: dict) -> str:
    """Build a structured context string from retrieval results.

    Order: chunks (with source info) → entity relationships → community summaries.
    """
    parts = []

    # 1. Chunks with source metadata
    chunks = retrieval_result.get("chunks", [])
    if chunks:
        parts.append("## Retrieved Document Chunks\n")
        for i, ch in enumerate(chunks):
            doc_name = ch.get("doc_id", "unknown")
            text = ch.get("text", "")
            score = ch.get("score", 0)
            parts.append(
                f"### Chunk {i + 1} 【source:{doc_name}, chunk:{i + 1}】"
                f" (relevance: {score:.2f})\n{text}\n"
            )

    # 2. Entity relationships from Neo4j
    entities = retrieval_result.get("entities", [])
    if entities:
        parts.append("## Entity Relationships\n")
        for ent in entities:
            name = ent.get("name", "")
            etype = ent.get("type", "")
            rels = ent.get("relationships", [])
            if rels:
                rel_str = "; ".join(
                    f"{r.get('relation', 'related')} → {r.get('target', '?')}"
                    for r in rels[:10]
                )
                parts.append(f"- **{name}** ({etype}): {rel_str}\n")
            else:
                parts.append(f"- **{name}** ({etype})\n")

    # 3. Community summaries
    communities = retrieval_result.get("communities", [])
    if communities:
        parts.append("## Topic Landscape (Community Summaries)\n")
        for comm in communities:
            parts.append(
                f"### Community {comm.get('community_id', '?')}"
                f" ({comm.get('entity_count', 0)} entities)\n"
                f"{comm.get('summary', '')}\n"
            )

    return "\n".join(parts)


def build_prompt(query: str, context: str, system_prompt: str = None) -> tuple:
    """Build system + user prompts for the LLM."""
    system = system_prompt or DEFAULT_SYSTEM_PROMPT
    user = f"""## Question

{query}

## Context

{context}

## Instructions

Answer the question using ONLY the provided context above. Include inline citations in the format 【source:doc_name, chunk:N】. If the context is insufficient, state what's missing clearly."""
    return system, user


def extract_citations(answer_text: str) -> List[Dict[str, Any]]:
    """Parse citation markers from the generated answer."""
    citations = []
    seen: set = set()
    for match in CITATION_PATTERN.finditer(answer_text):
        doc = match.group(1)
        chunk = int(match.group(2))
        key = (doc, chunk)
        if key not in seen:
            seen.add(key)
            citations.append({"doc_id": doc, "chunk_index": chunk})
    return citations


async def generate_answer(query: str, retrieval_result: dict,
                          client, model: str = "gpt-4o-mini",
                          extra_params: dict = None,
                          system_prompt: str = None,
                          redis_cache: object = None,
                          embedder=None, config: dict = None) -> GeneratedAnswer:
    """Generate an answer from retrieved context.

    Args:
        query: The user's question.
        retrieval_result: Output from hybrid_search() — {chunks, entities, communities}.
        client: AsyncOpenAI-compatible client.
        model: Model to use for generation.
        extra_params: Additional LLM params.
        system_prompt: Custom system prompt.
        redis_cache: Optional SemanticCache for caching.
        embedder: Optional EmbeddingProvider for cache key generation.

    Returns:
        GeneratedAnswer with text, citations, and metadata.
    """
    t0 = time.monotonic()

    # Check Redis cache first
    if redis_cache is not None:
        cached = await redis_cache.get(query, config)
        if cached and "answer" in cached:
            return GeneratedAnswer(
                text=cached["answer"],
                citations=cached.get("citations", []),
                sources_used=cached.get("sources_used", []),
                model=model,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

    # Assemble context
    context = assemble_context(retrieval_result)
    system, user = build_prompt(query, context, system_prompt)

    # Generate
    try:
        res = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **(extra_params or {}),
        )
        answer_text = res.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"Answer generation failed: {e}")
        answer_text = f"Unable to generate answer: {e}"

    # Extract citations
    citations = extract_citations(answer_text)

    # Extract sources used
    sources_used = list(set(c["doc_id"] for c in citations))

    elapsed = (time.monotonic() - t0) * 1000
    result = GeneratedAnswer(
        text=answer_text,
        citations=citations,
        sources_used=sources_used,
        model=model,
        elapsed_ms=round(elapsed, 1),
    )

    # Cache the answer
    if redis_cache is not None:
        try:
            await redis_cache.set(query, {
                "answer": answer_text,
                "citations": citations,
                "sources_used": sources_used,
                "strategy": retrieval_result.get("strategy", "unknown"),
                "chunks": retrieval_result.get("chunks", []),
                "entities": retrieval_result.get("entities", []),
                "communities": retrieval_result.get("communities", []),
            }, config)
        except Exception:
            pass

    return result


def format_answer_with_sources(answer: GeneratedAnswer,
                                retrieval_result: dict = None) -> str:
    """Format the final answer with a sources section appended."""
    parts = [answer.text]

    if answer.citations:
        parts.append("\n---\n## Sources\n")
        # Map doc_ids to titles
        chunks = (retrieval_result or {}).get("chunks", [])
        doc_titles: Dict[str, str] = {}
        for ch in chunks:
            doc_id = ch.get("doc_id", "")
            if doc_id not in doc_titles:
                doc_titles[doc_id] = ch.get("metadata", {}).get("title", doc_id)

        cited = set()
        for i, cit in enumerate(answer.citations):
            doc = cit["doc_id"]
            chunk = cit["chunk_index"]
            title = doc_titles.get(doc, doc)
            key = (doc, chunk)
            if key not in cited:
                cited.add(key)
                parts.append(f"{i + 1}. {title} (chunk {chunk})")

    return "\n".join(parts)
