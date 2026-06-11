from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import emit, fail, normalize_paper, parse_params


# ── Paths ──────────────────────────────────────────────────────────────

def _corpus_path(custom: str = "") -> str:
    if custom:
        return str(Path(custom).expanduser())
    default = Path.home() / ".openmegatron" / "citation_corpus.json"
    default.parent.mkdir(parents=True, exist_ok=True)
    return str(default)


# ── Sentence splitting ─────────────────────────────────────────────────

def split_sentences(text: str) -> list[dict]:
    """Split text into sentences. Handles English (.!?) and Chinese (。！？) punctuation."""
    if not text:
        return []
    # Split on sentence-ending punctuation followed by space or Chinese punctuation
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])|(?<=[。！？])", text)
    sentences = []
    pos = 0
    for part in parts:
        t = part.strip()
        if not t:
            continue
        start = text.find(t, pos)
        if start < 0:
            start = pos
        end = start + len(t)
        sentences.append({"text": t, "start_char": start, "end_char": end})
        pos = end
    return sentences


# ── Corpus file operations ─────────────────────────────────────────────

def load_corpus(filepath: str) -> list[dict]:
    p = Path(filepath)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8", errors="replace"))


def save_corpus(filepath: str, papers: list[dict]):
    # Convert papers to corpus entries (split abstract into sentences)
    entries = []
    for p in papers:
        title = p.get("title", "") or ""
        authors = p.get("authors") or p.get("author") or ""
        if isinstance(authors, list):
            authors = ", ".join(authors)
        venue = p.get("venue", "") or ""
        year = p.get("year", "")
        doi = p.get("doi", "") or ""
        abstract = p.get("abstract") or p.get("evidence_text") or ""
        sentences = split_sentences(abstract)
        for s in sentences:
            if len(s["text"]) > 20:  # skip very short fragments
                entries.append({
                    "sentence": s["text"],
                    "title": title,
                    "authors": authors,
                    "venue": venue,
                    "year": year,
                    "doi": doi,
                })
    # Deduplicate by sentence text
    seen = set()
    unique = []
    for e in entries:
        key = e["sentence"][:100]
        if key not in seen:
            seen.add(key)
            unique.append(e)
    Path(filepath).write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
    return unique


# ── TF-IDF core ────────────────────────────────────────────────────────

def build_tfidf(corpus: list[dict]):
    sentences = [e["sentence"] for e in corpus]
    if not sentences:
        return None, None, []
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=5000)
    mat = vec.fit_transform(sentences)
    return vec, mat, corpus


def recommend(text: str, corpus_path: str, top_k: int = 3,
              min_sim: float = 0.15, max_sentences: int = 50) -> dict:
    corpus = load_corpus(corpus_path)
    if not corpus:
        return {"error": "Corpus is empty. Run build_corpus first."}

    vec, mat, _ = build_tfidf(corpus)
    if vec is None:
        return {"error": "Could not build TF-IDF from corpus."}

    sentences = split_sentences(text)[:max_sentences]
    if not sentences:
        return {"error": "No sentences found in input text."}

    results = []
    all_refs: dict = {}

    for sent in sentences:
        t = sent["text"]
        if len(t) < 15:  # skip very short sentences
            continue
        svec = vec.transform([t])
        sims = cosine_similarity(svec, mat)[0]
        top_idx = np.argsort(sims)[::-1][:top_k]
        recs = []
        for idx in top_idx:
            score = float(sims[idx])
            if score < min_sim:
                continue
            meta = corpus[idx]
            recs.append({
                "title": meta["title"],
                "authors": meta["authors"],
                "venue": meta["venue"],
                "year": meta["year"],
                "doi": meta["doi"],
                "similarity_score": round(score, 4),
                "similar_snippet": meta["sentence"],
            })
            key = f"{meta['title']}|{meta['venue']}|{meta['year']}"
            if key not in all_refs:
                all_refs[key] = recs[-1]

        if recs:
            results.append({
                "original_sentence": t,
                "start_char": sent["start_char"],
                "end_char": sent["end_char"],
                "recommended_citations": recs,
            })

    # Build combined bibliography
    bibliography = []
    for rec in all_refs.values():
        # Generate citation key
        first_author = (rec["authors"] or "").split(",")[0].split(" ")[0].strip()
        venue_short = rec["venue"].replace(" ", "").replace("-", "")[:12]
        cite_key = f"{first_author}{rec['year']}{venue_short}" if first_author else f"ref{len(bibliography)+1}"
        # BibTeX
        bibtex = f"@article{{{cite_key},\n"
        if rec["title"]:
            bibtex += f"  title={{{rec['title']}}},\n"
        if rec["authors"]:
            bibtex += f"  author={{{rec['authors']}}},\n"
        if rec["venue"]:
            bibtex += f"  journal={{{rec['venue']}}},\n"
        if rec["year"]:
            bibtex += f"  year={{{rec['year']}}},\n"
        if rec["doi"]:
            bibtex += f"  doi={{{rec['doi']}}},\n"
        bibtex += "}"
        plain = f"{rec['authors']}, \"{rec['title']}\", {rec['venue']}, {rec['year']}."
        bibliography.append({
            "citation_key": cite_key,
            "bibtex": bibtex,
            "plain_text": plain,
        })

    return {
        "text_with_suggestions": results,
        "combined_bibliography": bibliography,
        "stats": {
            "sentences_analyzed": len(sentences),
            "sentences_with_citations": len(results),
            "unique_references": len(bibliography),
            "corpus_size": len(corpus),
        },
    }


# ── Main ───────────────────────────────────────────────────────────────

def main() -> int:
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    action = str(params.get("action", "")).lower()

    if not action:
        emit({"status": "error", "error": "Missing action."})
        return 2

    corpus_file = _corpus_path(params.get("corpus_file", ""))

    try:
        if action == "recommend":
            text = params.get("text", "")
            if not text:
                emit({"status": "error", "error": "Missing 'text' for recommend."})
                return 2
            top_k = int(params.get("top_k", 3))
            min_sim = float(params.get("min_similarity", 0.15))
            max_sent = int(params.get("max_sentences", 50))
            result = recommend(text, corpus_file, top_k, min_sim, max_sent)
            if "error" in result:
                emit({"status": "error", "error": result["error"]})
                return 2
            emit({"status": "success", "action": "recommend", **result})
            return 0

        if action == "build_corpus":
            papers = params.get("papers") or params.get("items") or []
            if not papers and params.get("path"):
                p = Path(str(params["path"])).expanduser()
                if p.exists():
                    raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                    papers = raw if isinstance(raw, list) else [raw]
            if not papers:
                emit({"status": "error", "error": "No papers provided. Use 'papers', 'path', or 'items'."})
                return 2
            # Normalize and save
            entries = save_corpus(corpus_file, papers)
            emit({"status": "success", "action": "build_corpus",
                  "entries": len(entries), "file": corpus_file})
            return 0

        if action == "list_corpus":
            corpus = load_corpus(corpus_file)
            # Summarize by source paper count
            paper_titles = list(dict.fromkeys(e["title"] for e in corpus if e["title"]))
            emit({"status": "success", "action": "list_corpus",
                  "total_entries": len(corpus),
                  "paper_count": len(paper_titles),
                  "papers": [{"title": t} for t in paper_titles[:100]]})
            return 0

        if action == "clear_corpus":
            p = Path(corpus_file)
            if p.exists():
                p.unlink()
            emit({"status": "success", "action": "clear_corpus", "cleared": True})
            return 0

        emit({"status": "error", "error": f"Unknown action: {action}"})
        return 2

    except Exception as e:
        emit({"status": "error", "action": action, "error": str(e)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
