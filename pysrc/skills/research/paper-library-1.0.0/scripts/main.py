from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


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


def _get_db(args: dict) -> str:
    custom = args.get("db_path", "")
    if custom:
        return str(Path(custom).expanduser())
    default = Path.home() / ".openmegatron" / "paper_library.db"
    default.parent.mkdir(parents=True, exist_ok=True)
    return str(default)


def _init_db(db_path: str):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS papers (
                paper_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                authors TEXT DEFAULT '',
                year INTEGER,
                venue TEXT DEFAULT '',
                doi TEXT DEFAULT '',
                url TEXT DEFAULT '',
                abstract TEXT DEFAULT '',
                citations INTEGER DEFAULT 0,
                source TEXT DEFAULT '',
                query TEXT DEFAULT '',
                saved_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                notes TEXT DEFAULT '',
                tags TEXT DEFAULT ''
            )
        """)
        conn.commit()


def _normalize_paper(r: dict, idx: int = 0) -> dict:
    doi = (r.get("doi") or "").strip()
    authors = r.get("authors") or r.get("author") or ""
    if isinstance(authors, list):
        authors = ", ".join(authors)
    paper_id = r.get("id") or r.get("paper_id") or doi or f"lib_{datetime.now().timestamp()}_{idx}"
    return {
        "paper_id": str(paper_id),
        "title": str(r.get("title", "") or ""),
        "authors": str(authors or ""),
        "year": r.get("year") if isinstance(r.get("year"), int) else (int(str(r.get("year", 0))) if str(r.get("year", "")).isdigit() else None),
        "venue": str(r.get("venue", "") or ""),
        "doi": doi,
        "url": str(r.get("url", "") or ""),
        "abstract": str(r.get("abstract") or r.get("evidence_text") or r.get("key_findings") or ""),
        "citations": int(r.get("citations") or r.get("cited_by_count") or 0),
        "source": str(r.get("source", "") or ""),
        "query": str(r.get("query", "") or ""),
    }


def _save_papers(db_path: str, papers: list[dict]) -> dict:
    _init_db(db_path)
    now = datetime.now().isoformat()
    saved, updated = 0, 0
    with sqlite3.connect(db_path) as conn:
        for p in papers:
            norm = _normalize_paper(p)
            existing = conn.execute("SELECT paper_id FROM papers WHERE paper_id=?", (norm["paper_id"],)).fetchone()
            if existing:
                conn.execute("""
                    UPDATE papers SET title=?, authors=?, year=?, venue=?, doi=?, url=?,
                    abstract=?, citations=?, source=?, query=?, updated_at=?
                    WHERE paper_id=?
                """, (norm["title"], norm["authors"], norm["year"], norm["venue"],
                      norm["doi"], norm["url"], norm["abstract"], norm["citations"],
                      norm["source"], norm["query"], now, norm["paper_id"]))
                updated += 1
            else:
                conn.execute("""
                    INSERT INTO papers (paper_id, title, authors, year, venue, doi, url,
                        abstract, citations, source, query, saved_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (norm["paper_id"], norm["title"], norm["authors"], norm["year"],
                      norm["venue"], norm["doi"], norm["url"], norm["abstract"],
                      norm["citations"], norm["source"], norm["query"], now, now))
                saved += 1
        conn.commit()
    return {"saved": saved, "updated": updated, "total": saved + updated}


def _list_papers(db_path: str, filter_tag: str = "", filter_year: int = None,
                 filter_source: str = "", limit: int = 50) -> list[dict]:
    _init_db(db_path)
    q = "SELECT * FROM papers WHERE 1=1"
    params = []
    if filter_tag:
        q += " AND (tags LIKE ? OR tags LIKE ? OR tags = ?)"
        params += [f"{filter_tag},%", f"%,{filter_tag},%", filter_tag]
    if filter_year:
        q += " AND year = ?"
        params.append(filter_year)
    if filter_source:
        q += " AND source = ?"
        params.append(filter_source)
    q += " ORDER BY saved_at DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def _search_papers(db_path: str, query: str, limit: int = 50) -> list[dict]:
    _init_db(db_path)
    q = """SELECT * FROM papers WHERE
        title LIKE ? OR authors LIKE ? OR abstract LIKE ? OR venue LIKE ? OR doi LIKE ?
        ORDER BY citations DESC, saved_at DESC LIMIT ?"""
    like = f"%{query}%"
    params = [like, like, like, like, like, limit]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def _tag_paper(db_path: str, paper_id: str, tag: str = "", remove: bool = False) -> dict:
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT tags FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
        if not row:
            return {"error": f"Paper not found: {paper_id}"}
        current = row[0].strip()
        tags = [t.strip() for t in current.split(",") if t.strip()] if current else []
        if remove:
            if tag in tags:
                tags = [t for t in tags if t != tag]
        else:
            if tag and tag not in tags:
                tags.append(tag)
        new_tags = ",".join(tags)
        conn.execute("UPDATE papers SET tags=?, updated_at=? WHERE paper_id=?",
                     (new_tags, datetime.now().isoformat(), paper_id))
        conn.commit()
    return {"paper_id": paper_id, "tags": tags}


def _note_paper(db_path: str, paper_id: str, notes: str) -> dict:
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT paper_id FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
        if not row:
            return {"error": f"Paper not found: {paper_id}"}
        conn.execute("UPDATE papers SET notes=?, updated_at=? WHERE paper_id=?",
                     (notes, datetime.now().isoformat(), paper_id))
        conn.commit()
    return {"paper_id": paper_id, "notes": notes}


def _stats(db_path: str) -> dict:
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        read = conn.execute("SELECT COUNT(*) FROM papers WHERE tags LIKE '%read%'").fetchone()[0]
        to_read = conn.execute("SELECT COUNT(*) FROM papers WHERE tags LIKE '%to-read%'").fetchone()[0]
        tagged = conn.execute("SELECT COUNT(*) FROM papers WHERE tags != ''").fetchone()[0]
        has_notes = conn.execute("SELECT COUNT(*) FROM papers WHERE notes != ''").fetchone()[0]
        conn.row_factory = sqlite3.Row
        sources = [dict(r) for r in conn.execute(
            "SELECT source, COUNT(*) as count FROM papers WHERE source != '' GROUP BY source ORDER BY count DESC").fetchall()]
        years = [dict(r) for r in conn.execute(
            "SELECT year, COUNT(*) as count FROM papers WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC").fetchall()]
    return {
        "total": total, "read": read, "to_read": to_read,
        "tagged": tagged, "with_notes": has_notes,
        "sources": sources, "years": years,
    }


def _export(db_path: str, fmt: str, output: str = "") -> dict:
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM papers ORDER BY saved_at DESC").fetchall()
    papers = [dict(r) for r in rows]
    if fmt == "csv":
        if not papers:
            return {"total": 0, "format": "csv", "error": "No papers to export"}
        buf = []
        keys = ["paper_id", "title", "authors", "year", "venue", "doi", "url",
                "citations", "source", "query", "tags", "notes", "saved_at"]
        buf.append(",".join(keys))
        for p in papers:
            buf.append(",".join(f'"{str(p.get(k,"")).replace(chr(34),chr(34)+chr(34))}"' for k in keys))
        csv_text = "\n".join(buf)
        if output:
            Path(str(output)).expanduser().write_text(csv_text, encoding="utf-8")
        return {"total": len(papers), "format": "csv", "csv": csv_text}
    else:
        if output:
            Path(str(output)).expanduser().write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"total": len(papers), "format": "json", "papers": papers}


def _clear(db_path: str):
    _init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM papers")
        conn.commit()


# ── Deduplicate ───────────────────────────────────────

def _normalize_title(t: str) -> str:
    return " ".join(t.lower().replace("{", "").replace("}", "").replace("\n", " ").split())[:120]

def _deduplicate(db_path: str, dry_run: bool) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT paper_id, title, doi, year FROM papers ORDER BY paper_id").fetchall()
    groups = []
    seen_doi = {}
    seen_title = {}

    for r in rows:
        pid, title, doi, year = r["paper_id"], r["title"] or "", r["doi"] or "", r["year"]
        nt = _normalize_title(title)
        matched = None

        if doi and doi in seen_doi:
            matched = seen_doi[doi]
        elif nt and nt in seen_title:
            matched = seen_title[nt]

        if matched:
            groups.append({
                "kept_id": matched,
                "duplicate_id": pid,
                "duplicate_title": title[:120],
                "reason": "doi_match" if (doi and doi in seen_doi) else "title_fuzzy",
                "confidence": "high" if (doi and doi in seen_doi) else "medium",
            })
            if not dry_run:
                conn.execute("DELETE FROM papers WHERE paper_id = ?", (pid,))
        else:
            if doi:
                seen_doi[doi] = pid
            if nt:
                seen_title[nt] = pid

    conn.commit()
    conn.close()
    return {
        "duplicates_found": len(groups),
        "action": "preview" if dry_run else "merged",
        "groups": groups[:50],
    }


# ── Import BibTeX ─────────────────────────────────────

def _parse_bibtex(path_str: str) -> list[dict]:
    """Parse a .bib file into paper dicts."""
    content = Path(path_str).read_text(encoding="utf-8", errors="replace")
    entries = []
    # Match @type{key, fields}
    pattern = re.compile(r'@(\w+)\s*\{\s*([^,]+)\s*,\s*(.+?)\}\s*$', re.MULTILINE | re.DOTALL)
    import re as _re
    for m in _re.finditer(r'@(\w+)\s*\{\s*([^,]+)\s*,\s*(.+?)\}', content, _re.DOTALL):
        entry_type = m.group(1).lower()
        cite_key = m.group(2).strip()
        fields_str = m.group(3)

        paper = {"bibtex_type": entry_type, "cite_key": cite_key, "source": "bibtex_import"}
        # Extract fields
        field_pattern = _re.compile(r'(\w+)\s*=\s*[{"]([^}"]*)[}"]', _re.DOTALL)
        for fm in field_pattern.finditer(fields_str):
            key = fm.group(1).lower()
            val = fm.group(2).strip().replace("\n", " ").replace("  ", " ")
            if key == "title":
                paper["title"] = val
            elif key == "author":
                paper["authors"] = val
            elif key == "year":
                try:
                    paper["year"] = int(val)
                except ValueError:
                    paper["year"] = val
            elif key == "journal":
                paper["venue"] = val
            elif key == "booktitle":
                if "venue" not in paper:
                    paper["venue"] = val
            elif key == "doi":
                paper["doi"] = val
            elif key == "abstract":
                paper["abstract"] = val[:2000]
            elif key == "url":
                paper["url"] = val
        if paper.get("title"):
            entries.append(paper)

    return entries


def _import_bibtex(db_path: str, path_str: str) -> dict:
    entries = _parse_bibtex(path_str)
    imported, skipped, failed = 0, 0, 0
    conn = sqlite3.connect(db_path)

    for entry in entries:
        title = entry.get("title", "")
        if not title:
            failed += 1
            continue
        doi = entry.get("doi", "")
        existing = None
        if doi:
            existing = conn.execute("SELECT paper_id FROM papers WHERE doi = ?", (doi,)).fetchone()
        if not existing and title:
            nt = _normalize_title(title)
            existing = conn.execute("SELECT paper_id FROM papers WHERE LOWER(REPLACE(REPLACE(title, '{', ''), '}', '')) = ?", (nt,)).fetchone()
        if existing:
            skipped += 1
            continue

        try:
            now = datetime.now().isoformat()
            cite_key = entry.get("cite_key", "")[:50]
            conn.execute(
                "INSERT OR IGNORE INTO papers (paper_id, title, authors, year, venue, doi, abstract, url, source, saved_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    cite_key, title[:300], entry.get("authors", ""), entry.get("year", ""),
                    entry.get("venue", ""), doi, entry.get("abstract", ""),
                    entry.get("url", ""), "bibtex_import", now, now,
                ),
            )
            imported += 1
        except Exception:
            failed += 1

    conn.commit()
    conn.close()
    return {"imported": imported, "skipped_existing": skipped, "failed": failed, "total_in_file": len(entries)}


def main() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    if not action:
        print(json.dumps({"status": "error", "error": "Missing 'action'."}, ensure_ascii=False))
        return 2

    db = _get_db(args)
    result = {"status": "success", "action": action}

    try:
        if action == "save_papers":
            papers = []
            if args.get("papers"):
                papers = args["papers"]
            elif args.get("readings"):
                papers = args["readings"]
            elif args.get("path"):
                p = Path(str(args["path"])).expanduser()
                raw = p.read_text(encoding="utf-8", errors="replace")
                data = json.loads(raw)
                papers = data if isinstance(data, list) else [data]
            if not papers:
                print(json.dumps({"status": "error", "error": "No papers provided."}, ensure_ascii=False))
                return 2
            result.update(_save_papers(db, papers))

        elif action == "list":
            papers = _list_papers(db, args.get("filter_tag", ""),
                                  args.get("filter_year"),
                                  args.get("filter_source", ""),
                                  int(args.get("limit", 50)))
            result["total"] = len(papers)
            result["papers"] = papers

        elif action == "search":
            query = args.get("query", "")
            if not query:
                print(json.dumps({"status": "error", "error": "Missing 'query'."}, ensure_ascii=False))
                return 2
            papers = _search_papers(db, query, int(args.get("limit", 50)))
            result["total"] = len(papers)
            result["papers"] = papers

        elif action == "tag":
            pid = args.get("paper_id", "")
            if not pid:
                print(json.dumps({"status": "error", "error": "Missing 'paper_id'."}, ensure_ascii=False))
                return 2
            tag = args.get("tag", "")
            remove = args.get("remove", False)
            result.update(_tag_paper(db, pid, tag, remove))

        elif action == "note":
            pid = args.get("paper_id", "")
            notes = args.get("notes", "")
            if not pid:
                print(json.dumps({"status": "error", "error": "Missing 'paper_id'."}, ensure_ascii=False))
                return 2
            result.update(_note_paper(db, pid, notes))

        elif action == "stats":
            result.update(_stats(db))

        elif action == "export":
            fmt = args.get("format", "json")
            out = args.get("output", "")
            result.update(_export(db, fmt, out))

        elif action == "clear":
            _clear(db)
            result["cleared"] = True

        elif action == "deduplicate":
            dry_run = args.get("dry_run", False)
            result.update(_deduplicate(db, dry_run))

        elif action == "import_bibtex":
            path_str = args.get("path", "")
            if not path_str:
                print(json.dumps({"status": "error", "error": "Missing 'path' for BibTeX file."}, ensure_ascii=False))
                return 2
            result.update(_import_bibtex(db, path_str))

        else:
            print(json.dumps({"status": "error", "error": f"Unknown action: {action}"}, ensure_ascii=False))
            return 2

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    except Exception as e:
        print(json.dumps({"status": "error", "action": action, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
