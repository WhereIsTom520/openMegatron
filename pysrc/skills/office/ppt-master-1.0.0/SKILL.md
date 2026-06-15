---
name: ppt_master
description: Generate editable PowerPoint decks from a topic, JSON slide plan, or Markdown outline. Produces native .pptx files with editable text boxes, bullet lists, tables, and image placeholders.
category: office
entry_function: main
parameters:
  type: object
  properties:
    topic:
      type: string
      description: Presentation topic. Used for title and automatic outline generation when slides/outline are omitted.
    action:
      type: string
      description: One of generate, list_templates, setup, or check. Defaults to generate.
    mode:
      type: string
      description: One of auto, external, builtin. auto tries a local hugohe3/ppt-master bridge before falling back to the builtin renderer.
    source:
      type: string
      description: Source document path or URL. Markdown/text can be rendered directly; PDF/DOCX/HTML/XLSX may use an external PPT Master checkout for conversion.
    ppt_master_path:
      type: string
      description: Local path to the hugohe3/ppt-master repository or its skills/ppt-master directory. If omitted, setup uses ~/.openmegatron/ppt-master.
    project:
      type: string
      description: Existing external PPT Master project directory containing generated SVG output for native PPTX export.
    svg_dir:
      type: string
      description: Directory containing SVG slides to export through the external PPT Master svg_to_pptx.py script.
    python_executable:
      type: string
      description: Python executable used for external PPT Master scripts. Defaults to the current Python.
    slides:
      type: array
      description: Structured slide objects. Each item may include title, subtitle, bullets, body, table, image, notes, or layout.
      items:
        type: object
    outline:
      type: string
      description: Markdown outline. Use "## Slide title" headings and bullet lines for slide content.
    output:
      type: string
      description: Output .pptx path. Defaults to outputs/ppt_master/<topic>.pptx.
    title:
      type: string
      description: Deck title. Defaults to topic.
    subtitle:
      type: string
      description: Optional title-slide subtitle.
    author:
      type: string
      description: Optional author text for title slide.
    audience:
      type: string
      description: Target audience used by the generated fallback outline.
    language:
      type: string
      description: Language hint for generated fallback slides, e.g. zh or en.
    style:
      type: string
      description: One of professional, academic, creative, minimal.
    theme_color:
      type: string
      description: Primary accent color in hex, e.g. "#2563eb".
    slide_count:
      type: integer
      description: Desired slide count for generated fallback outline. Default 6.
    include_toc:
      type: boolean
      description: Add agenda/table-of-contents slide. Default true.
    image_placeholders:
      type: boolean
      description: Render image placeholder boxes when image paths are missing. Default true.
    overwrite:
      type: boolean
      description: Overwrite existing output file. Default false.
    timeout_seconds:
      type: integer
      description: External script timeout in seconds. Default 300.
    auto_install:
      type: boolean
      description: Automatically clone/install external PPT Master when a source/project requires it. Default true.
    install_dependencies:
      type: boolean
      description: Install upstream Python dependencies during setup. Default true.
    update:
      type: boolean
      description: Run git pull when the external repository already exists. Default false.
  required: []
keywords:
  - ppt
  - pptx
  - powerpoint
  - presentation
  - slide deck
  - editable ppt
  - ai ppt
  - ppt master
  - external ppt master
  - document to ppt
  - pdf to ppt
  - docx to ppt
  - 演示文稿
  - 幻灯片
  - 可编辑PPT
  - 课件
capabilities:
  - create
  - transform
  - write
consumes:
  source: file or URL
  topic: string
  outline: markdown
  slides: json
produces:
  pptx: Editable PowerPoint deck file.
side_effects:
  - Creates or overwrites .pptx files.
risk: low
---

# PPT Master

Use this skill when the user wants an AI-generated PowerPoint deck, courseware, pitch deck, meeting deck, report presentation, or an editable `.pptx` from a topic/outline/document.

Prefer `mode: "auto"` unless the user explicitly asks for the built-in renderer. In auto mode, the skill auto-discovers or auto-installs a local `hugohe3/ppt-master` checkout for source conversion or SVG export. It falls back to the built-in editable PPTX renderer only after reporting the external setup/conversion failure in the result.

Pass `action: "setup"` for one-click upstream bootstrap. Pass `action: "check"` to diagnose whether the external engine is installed and which scripts/dependencies are available. Pass `action: "list_templates"` to inspect available templates/styles. Pass `project` or `svg_dir` when an external PPT Master workflow has already generated SVG pages and the user wants a final native PPTX export.

Prefer passing a structured `slides` array when the model has enough content. Use `outline` for user-supplied Markdown. Use `source` for document paths, Markdown files, text files, or URLs. If the user only gives a topic, pass the topic plus audience/style/slide_count so the skill can create a concise starter deck.

The generated deck must remain editable: do not rasterize full slides into images unless the user explicitly requests a picture-only deck.
