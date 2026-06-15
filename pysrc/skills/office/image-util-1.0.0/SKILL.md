---
name: image_util
description: Image processing operations — convert format, resize, compress, crop, flip, tint, and get metadata. Requires Pillow.
category: office
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: >
        One of: info, convert, resize, compress, crop, flip, batch.
    input:
      type: string
      description: Input image path (or directory for batch).
    output:
      type: string
      description: Output path or directory.
    format:
      type: string
      description: Target format for convert (PNG, JPEG, WebP, BMP, GIF).
    quality:
      type: integer
      description: JPEG/WebP quality 1-100 (default 85).
    width:
      type: integer
      description: Target width in pixels for resize/crop.
    height:
      type: integer
      description: Target height in pixels for resize/crop.
    mode:
      type: string
      description: Resize mode (fit, fill, exact) — default fit.
    direction:
      type: string
      description: Flip direction (horizontal, vertical, both).
    tint:
      type: string
      description: Tint color as hex (e.g., "#4488ff") or named color.
    overwrite:
      type: boolean
      description: Overwrite output if exists (default false).
    recursive:
      type: boolean
      description: Scan subdirectories for batch (default false).
    pattern:
      type: string
      description: Glob pattern for batch (default "*").
  required:
    - action
    - input
keywords: [image, png, jpeg, webp, resize, compress, convert, crop, flip, pillow, office]
produces:
  stdout: JSON with status, action, file paths
side_effects:
  - Creates or overwrites image files.
risk: low
---
