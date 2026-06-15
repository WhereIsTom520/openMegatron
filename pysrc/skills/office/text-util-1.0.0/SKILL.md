---
name: text_util
description: Text file operations — diff, search, replace, encoding conversion, word/line stats, file splitting and merging.
category: office
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: >
        One of: diff, grep, replace, encoding, stats, split, merge.
    input:
      type: string
      description: Input file path (or first file for diff/merge).
    input2:
      type: string
      description: Second file for diff.
    output:
      type: string
      description: Output file path.
    pattern:
      type: string
      description: Search pattern (regex) for grep/replace.
    replacement:
      type: string
      description: Replacement text for replace action.
    from_encoding:
      type: string
      description: Source encoding for encoding action (e.g., "gbk", "utf-8").
    to_encoding:
      type: string
      description: Target encoding for encoding action.
    chunk_lines:
      type: integer
      description: Lines per chunk for split action (default 1000).
    context:
      type: integer
      description: Context lines for grep diff (default 0).
    ignore_case:
      type: boolean
      description: Case-insensitive search (default false).
    overwrite:
      type: boolean
      description: Overwrite output if exists (default false).
  required:
    - action
    - input
keywords: [text, diff, grep, replace, encoding, split, merge, word count, office]
produces:
  stdout: JSON with status, action, file paths, match counts
side_effects:
  - Creates or overwrites text files.
risk: low
---
