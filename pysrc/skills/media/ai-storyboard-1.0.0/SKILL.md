---
name: ai_storyboard
description: Generate a complete storyboard video project from a text description using AI. Produces scenes.json, subtitles.json, narration text, DESIGN.md, and silent audio — ready for HyperFrames rendering or manual refinement.
category: media
entry_function: main
parameters:
  type: object
  properties:
    topic:
      type: string
      description: The topic, theme, or content description for the video.
    style:
      type: string
      description: Visual style guide (e.g., "corporate training", "product explainer", "tech presentation", "cartoon", "minimalist").
    duration:
      type: integer
      description: Target total duration in seconds (default 60).
    scene_count:
      type: integer
      description: Number of scenes to generate (default 6).
    language:
      type: string
      description: Narration/subtitle language (default "zh-CN", also "en").
    output_dir:
      type: string
      description: Output directory for the generated project.
    project_name:
      type: string
      description: Project folder name (default derived from topic).
    tone:
      type: string
      description: Narration tone — professional, casual, enthusiastic, academic (default professional).
  required:
    - topic
keywords: [storyboard, 分镜, ai, generate, video, hyperframes, scene, narration, subtitle, script]
produces:
  project_dir: HyperFrames project directory with DESIGN.md, scenes.json, subtitles.json, narration.txt, and narration_fit.wav.
side_effects:
  - Creates project folder with generated assets.
  - Requires a configured LLM provider in pysrc/model.toml.
  - Does not render MP4; use HyperFrames or the ffmpeg-tool skill after generation.
risk: low
---
