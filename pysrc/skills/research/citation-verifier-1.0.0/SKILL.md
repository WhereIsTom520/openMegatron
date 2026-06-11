---
name: citation_verifier
version: 1.1.0
description: Verify citation format, check reference existence (DOI/URL), detect retractions, and produce a unified audit report. One-click full citation health check.
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "verify | audit. verify=format+plausibility check. audit=full check (format + existence + retraction + link)."
      enum: ["verify", "audit"]
    review:
      type: string
      description: Review text containing citations like [1].
    papers:
      type: array
      description: Paper metadata list with doi/title/authors fields.
    matrix:
      type: array
      description: Evidence matrix rows (alternative to papers).
    path:
      type: string
      description: JSON file containing review and papers or matrix.
    citation_style:
      type: string
      description: "gbt7714 | ieee | apa | bibtex. Default gbt7714."
      enum: ["gbt7714", "ieee", "apa", "bibtex"]
      default: "gbt7714"
    include_references:
      type: boolean
      description: Whether to include formatted references. Default true.
      default: true
    max_link_checks:
      type: integer
      description: Max papers to check for live HTTP reachability. Default 20.
      default: 20
      minimum: 1
      maximum: 50
  required:
    - action
keywords: [citation, verify, references, bibliography, bibtex, review, hallucination, audit, retraction, doi, link, existence]
---

# Citation Verifier v1.1.0

## Actions

### `verify` — Format & Plausibility Check
Checks [N] citation indices against the paper list, computes lexical/semantic overlap
between citing sentences and paper metadata, flags weak support, and returns
formatted references in GB/T7714, IEEE, APA, or BibTeX.

### `audit` — Full Existence + Retraction + Link Check ★ NEW
One-click comprehensive citation audit:
1. **Format check** — same as `verify`: [N] index validity, weak support detection
2. **DOI resolution** — checks each paper's DOI via CrossRef API to confirm the paper exists
3. **Retraction check** — detects if any cited paper has been retracted or corrected
4. **HTTP reachability** — live HTTP HEAD/GET on each reference URL, flags broken links
5. **Unified report** — single output with status per reference, hallucination risk, and evidence boundary

Output includes:
- Per-reference: format status, exists (DOI resolves), retracted, link reachable, metadata source
- Summary: total references, valid format count, resolved DOIs, retractions found, broken links
- Evidence boundary statement

## Usage Examples

```
# Full audit of a review's citations
→ citation_verifier audit review="..." papers=[...] max_link_checks=20

# Quick format-only check
→ citation_verifier verify review="..." papers=[...]
```
