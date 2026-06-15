---
name: file_util
description: File management operations — batch rename, organize by type/date, find duplicates, generate directory tree, archive/unarchive files.
category: office
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: >
        One of: rename, organize, deduplicate, tree, archive, unarchive.
    input:
      type: string
      description: Target directory (or archive file for unarchive).
    output:
      type: string
      description: Output directory for organize/unarchive, or tree file.
    pattern:
      type: string
      description: Rename pattern — use {n} for counter, {date}, {ext} (e.g., "photo_{n:03d}").
    prefix:
      type: string
      description: Prefix for rename.
    suffix:
      type: string
      description: Suffix for rename (before extension).
    recursive:
      type: boolean
      description: Scan subdirectories (default false).
    dry_run:
      type: boolean
      description: Show what would be done without making changes (default false).
    archive_format:
      type: string
      description: Archive format — zip or tar.gz (default zip).
    dedup_mode:
      type: string
      description: Dedup mode — content (hash-based) or name (default content).
    overwrite:
      type: boolean
      description: Overwrite output if exists (default false).
  required:
    - action
    - input
keywords: [file, rename, organize, duplicate, tree, archive, zip, office]
produces:
  stdout: JSON with status, action, file lists
side_effects:
  - Renames, moves, archives, or deletes files.
risk: medium
---
