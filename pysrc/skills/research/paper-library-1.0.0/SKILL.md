---
name: paper_library
version: 1.1.0
description: Persistent paper library with SQLite storage. Save, search, tag, deduplicate, import BibTeX, and export.
category: research
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "save_papers | list | search | tag | note | stats | export | clear | deduplicate | import_bibtex"
      enum: ["save_papers", "list", "search", "tag", "note", "stats", "export", "clear", "deduplicate", "import_bibtex"]
    papers:
      type: array
      description: Paper dicts or reading dicts to save.
    path:
      type: string
      description: Path to JSON/CSV/BibTeX file.
    readings:
      type: array
      description: Paper readings from paper_reader.
    query:
      type: string
      description: Search query.
    tag:
      type: string
      description: Tag value.
    paper_id:
      type: string
      description: Paper ID for tag/note operations.
    notes:
      type: string
      description: Notes text.
    filter_tag:
      type: string
      description: Filter by tag.
    filter_year:
      type: integer
      description: Filter by year.
    limit:
      type: integer
      description: Max results (default 50).
    format:
      type: string
      description: "Export format: json | csv | bibtex"
      enum: ["json", "csv", "bibtex"]
    output:
      type: string
      description: Output file path.
    db_path:
      type: string
      description: Custom DB file path.
    dry_run:
      type: boolean
      description: "deduplicate: preview duplicates without removing. Default false."
      default: false
  required:
    - action
keywords: [paper, library, save, search, tag, note, organize, persist, deduplicate, bibtex, import, export]
produces:
  stdout: JSON with status and results.
side_effects:
  - Creates/updates SQLite database file for persistent paper storage.
risk: low
---

# Paper Library v1.1.0

## Actions

### `save_papers`, `list`, `search`, `tag`, `note`, `stats`, `export`, `clear`
Original behavior — full CRUD for paper library with SQLite persistence.

### `deduplicate` ★ NEW
Find and merge duplicate papers in the library:
- **DOI exact match**: same DOI → definitely a duplicate
- **Title fuzzy match**: Levenshtein distance on normalized titles
- **arXiv version detection**: arXiv ID vs published DOI variants
- **Dry-run mode**: `dry_run=true` previews duplicates without removing
- Returns: duplicate groups with confidence scores, merge recommendations

### `import_bibtex` ★ NEW
Import papers from a .bib file into the library:
- Parses standard BibTeX entries (article, inproceedings, misc, techreport)
- Extracts: title, authors, year, venue, doi, abstract when available
- Auto-deduplicates against existing library entries during import
- Returns: count of imported, skipped (duplicates), and failed entries
