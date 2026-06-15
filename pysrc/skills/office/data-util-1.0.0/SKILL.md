---
name: data_util
description: Data file processing — convert, transform, merge, validate CSV/JSON/YAML files.
category: office
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: >
        One of: csv2json, json2csv, csv_transform, csv_merge, yaml2json, json2yaml, validate_json.
    input:
      type: string
      description: Input file path (or first input for merge).
    output:
      type: string
      description: Output file path.
    input2:
      type: string
      description: Second input file for merge/diff actions.
    filter:
      type: string
      description: Column filter expression for csv_transform (e.g., "age>30").
    columns:
      type: string
      description: Comma-separated column names to select.
    sort_by:
      type: string
      description: Column to sort by.
    sort_desc:
      type: boolean
      description: Sort descending (default false).
    limit:
      type: integer
      description: Max rows to output.
    delimiter:
      type: string
      description: CSV delimiter (default ",").
    overwrite:
      type: boolean
      description: Overwrite output if exists (default false).
  required:
    - action
    - input
keywords: [csv, json, yaml, data, convert, merge, transform, validate, office]
produces:
  stdout: JSON with status, action, file paths, row counts
side_effects:
  - Creates or overwrites data files.
risk: low
---
