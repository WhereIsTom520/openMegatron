from __future__ import annotations

import asyncio
import json
import re
import sys
import wave
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from openai import AsyncOpenAI


# ── paths ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PYSRC_DIR = SCRIPT_DIR.parents[3]  # pysrc/
MODEL_TOML = PYSRC_DIR / "model.toml"
WIDTH, HEIGHT = 1920, 1080


# ── helpers ────────────────────────────────────────────────────────────

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


def load_llm_config() -> dict:
    """Read model.toml and extract active LLM provider config."""
    if not MODEL_TOML.exists():
        raise FileNotFoundError(
            f"LLM config not found at {MODEL_TOML}. "
            "Run scripts/llm_setup.py or copy model.example.toml to model.toml first."
        )
    with open(MODEL_TOML, "rb") as f:
        config = tomllib.load(f)

    llm_block = config.get("llm", {})
    active = llm_block.get("active_provider", "openai")
    provider = llm_block.get(active, {})
    api_key = provider.get("api_key") or llm_block.get("api_key", "")
    base_url = provider.get("base_url") or llm_block.get("base_url", "")
    model = provider.get("model", "gpt-4o-mini")
    extra_params = provider.get("extra_params", {}) or {}

    if not api_key:
        raise ValueError(
            f"API key not configured for provider '{active}'. "
            f"Set api_key in pysrc/model.toml under [llm.{active}]."
        )

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "provider": active,
        "extra_params": extra_params,
    }


def write_silent_wav(path: Path, duration: float, sample_rate: int = 24000) -> None:
    frame_count = max(1, int(duration * sample_rate))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        chunk = b"\x00\x00" * sample_rate
        full_chunks, remainder = divmod(frame_count, sample_rate)
        for _ in range(full_chunks):
            wav.writeframes(chunk)
        if remainder:
            wav.writeframes(b"\x00\x00" * remainder)


# ── LLM prompt ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a professional video storyboard director. Generate a complete storyboard for a video project.

Output ONLY valid JSON with this exact structure:
{
  "overview": {
    "title": "Video title",
    "visual_style": "Overall visual style description",
    "color_palette": ["#hex1", "#hex2", ...],
    "typography": "Font/style notes"
  },
  "scenes": [
    {
      "section": "Section name (e.g. Opening, Body, Conclusion)",
      "title": "Scene title",
      "id": "S01",
      "duration": 8.0,
      "visual": "Detailed visual description — what appears on screen, layout, elements",
      "camera": "Camera motion — pan, zoom, static, dolly, etc.",
      "animation": "Animation/transition effects",
      "captionLabel": "Short label for screen overlay",
      "captionPhrase": "Screen copy / on-screen text",
      "narration": "Spoken narration text for this scene"
    }
  ],
  "subtitles": [
    {
      "index": 1,
      "start": 0.0,
      "end": 4.0,
      "text": "Subtitle text line",
      "scene": "S01"
    }
  ]
}

RULES:
- Scenes should flow logically from intro to conclusion.
- Each scene duration should match its content weight.
- Subtitles align with narration timing (each 3-6 seconds per subtitle).
- Visual descriptions are concrete and actionable for a visual designer.
- Camera and animation fields describe realistic motion/transitions.
- Narration text is natural spoken language in the target language."""


def build_user_prompt(topic: str, style: str, duration: int, scene_count: int,
                       language: str, tone: str) -> str:
    return f"""Generate a storyboard for a video with these specifications:

Topic: {topic}
Visual Style: {style}
Target Duration: {duration} seconds
Number of Scenes: {scene_count}
Language: {language}
Narration Tone: {tone}

The scenes should tell a coherent story from introduction to conclusion.
Duration should be distributed meaningfully across scenes.
Subtitles should sync with narration timing."""


# ── LLM call ───────────────────────────────────────────────────────────

async def call_llm(llm_cfg: dict, prompt: str) -> dict:
    client = AsyncOpenAI(
        api_key=llm_cfg["api_key"],
        base_url=llm_cfg["base_url"],
    )
    model = llm_cfg["model"]

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
        max_tokens=4096,
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM returned empty response")

    # Try to extract JSON from possible markdown fences
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    raw_json = json_match.group(1) if json_match else content

    parsed = json.loads(raw_json)
    return parsed


# ── output builders ────────────────────────────────────────────────────

def build_scenes(raw_scenes: list[dict]) -> list[dict]:
    scenes = []
    start = 0.0
    for s in raw_scenes:
        duration = float(s.get("duration", 5.0))
        caption = s.get("captionPhrase", "")
        label = s.get("captionLabel", "")
        phrase = s.get("captionPhrase", "")
        scene = {
            "section": s.get("section", ""),
            "title": s.get("title", ""),
            "id": s.get("id", f"S{len(scenes)+1:02d}"),
            "duration": duration,
            "start": round(start, 3),
            "visual": s.get("visual", ""),
            "camera": s.get("camera", ""),
            "motion": s.get("animation", ""),
            "caption": caption,
            "captionLabel": label,
            "captionPhrase": phrase,
            "narration": s.get("narration", ""),
        }
        scenes.append(scene)
        start += duration
    return scenes


def build_subtitles(raw_subtitles: list[dict]) -> list[dict]:
    items = []
    for s in raw_subtitles:
        st = float(s.get("start", 0))
        en = float(s.get("end", st + 3))
        if en <= st:
            continue
        items.append({
            "index": s.get("index", len(items) + 1),
            "start": round(st, 3),
            "end": round(en, 3),
            "duration": max(0.05, round(en - st - 0.012, 3)),
            "text": s.get("text", ""),
            "scene": s.get("scene", ""),
        })
    return items


def build_html(scenes: list[dict], subtitles: list[dict], overview: dict) -> str:
    total_duration = max(
        float(scenes[-1]["start"]) + float(scenes[-1]["duration"]),
        max((float(s["end"]) for s in subtitles), default=0) + 2,
    ) if scenes else 60.0

    # Build scene divs
    scene_divs = []
    for i, scene in enumerate(scenes):
        bg_color = _pick_color(i, overview)
        z = len(scenes) - i
        scene_divs.append(f"""    <div
      class="scene"
      id="scene-{scene['id']}"
      data-start="{scene['start']}"
      data-duration="{scene['duration']}"
      style="background: {bg_color}; z-index: {z};"
    >
      <div class="scene-content">
        <h2 class="scene-title">{_esc(scene['title'])}</h2>
        <p class="scene-label">{_esc(scene.get('captionPhrase', ''))}</p>
        <p class="scene-note">{_esc(scene.get('visual', ''))}</p>
      </div>
    </div>""")

    # Build subtitle divs
    sub_divs = []
    for i, sub in enumerate(subtitles):
        sub_divs.append(f"""    <div
      class="subtitle"
      id="subtitle-{i+1}"
      data-start="{sub['start']}"
      data-duration="{sub['duration']}"
    >{_esc(sub['text'])}</div>""")

    style_css = f"""
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ background: #000; overflow: hidden; }}
    #root {{ position: relative; width: {WIDTH}px; height: {HEIGHT}px; margin: auto; overflow: hidden; font-family: system-ui, -apple-system, sans-serif; }}
    .scene {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }}
    .scene-content {{ text-align: center; padding: 60px; }}
    .scene-title {{ font-size: 48px; font-weight: 700; color: #fff; margin-bottom: 20px; text-shadow: 0 2px 8px rgba(0,0,0,0.3); }}
    .scene-label {{ font-size: 28px; color: rgba(255,255,255,0.9); margin-bottom: 30px; }}
    .scene-note {{ font-size: 18px; color: rgba(255,255,255,0.6); max-width: 800px; line-height: 1.5; }}
    .subtitle {{ position: absolute; bottom: 80px; left: 50%; transform: translateX(-50%); font-size: 32px; color: #fff; text-align: center; background: rgba(0,0,0,0.6); padding: 12px 24px; border-radius: 8px; max-width: 80%; font-weight: 500; line-height: 1.4; white-space: pre-wrap; }}
"""

    scenes_json = json.dumps(scenes, ensure_ascii=False)
    subtitles_json = json.dumps(subtitles, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={WIDTH}, height={HEIGHT}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
{style_css}
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="{total_duration:.3f}"
      data-width="{WIDTH}"
      data-height="{HEIGHT}"
    >
      <audio
        id="narration"
        src="assets/narration_fit.wav"
        data-start="0"
        data-duration="{total_duration:.3f}"
        data-track-index="4"
        data-volume="1"
      ></audio>
{chr(10).join(scene_divs)}
{chr(10).join(sub_divs)}
    </div>
    <script data-composition-id="main">
      const scenes = {scenes_json};
      const subtitles = {subtitles_json};
    </script>
    <script>
      document.addEventListener("DOMContentLoaded", () => {{
        const scenes = document.querySelectorAll(".scene");
        const subtitles = document.querySelectorAll(".subtitle");
        const tl = gsap.timeline({{ paused: true }});

        scenes.forEach((el, i) => {{
          const start = parseFloat(el.dataset.start);
          const dur = parseFloat(el.dataset.duration);
          tl.to(el, {{ opacity: 1, duration: 0.6, ease: "power2.out" }}, start);
          if (i < scenes.length - 1) {{
            const nextStart = parseFloat(scenes[i + 1].dataset.start);
            tl.to(el, {{ opacity: 0, duration: 0.4 }}, nextStart - 0.1);
          }}
        }});

        const totalDur = parseFloat(document.getElementById("root").dataset.duration);
        tl.to({{}}, {{ duration: totalDur }});
        tl.play();
      }});
    </script>
  </body>
</html>"""


def _pick_color(index: int, overview: dict) -> str:
    palette = overview.get("color_palette", [])
    if palette:
        return palette[index % len(palette)]
    defaults = ["#1a1a2e", "#16213e", "#0f3460", "#533483", "#2d4059", "#1a5276"]
    return defaults[index % len(defaults)]


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def write_design(project_dir: Path, overview: dict) -> None:
    lines = [
        f"# {overview.get('title', 'AI-Generated Storyboard')}",
        "",
        "## Visual Style",
        overview.get("visual_style", ""),
        "",
        "## Color Palette",
    ]
    for c in overview.get("color_palette", []):
        lines.append(f"- {c}")
    lines += [
        "",
        "## Typography",
        overview.get("typography", ""),
        "",
        "---",
        "*Generated by ai-storyboard skill*",
    ]
    (project_dir / "DESIGN.md").write_text("\n".join(lines), encoding="utf-8")


# ── main ───────────────────────────────────────────────────────────────

async def main_async() -> int:
    args = parse_cli_args()
    topic = args.get("topic", "")
    if not topic:
        print(json.dumps({"status": "error", "error": "Missing required 'topic' parameter."}, ensure_ascii=False))
        return 2

    style = args.get("style", "corporate training presentation")
    duration = int(args.get("duration", 60))
    scene_count = max(1, int(args.get("scene_count", 6)))
    language = args.get("language", "zh-CN")
    tone = args.get("tone", "professional")
    output_dir = Path(str(args.get("output_dir", "")) or str(Path.cwd() / "generated-videos")).expanduser()
    project_name = str(args.get("project_name") or re.sub(r"[^a-zA-Z0-9._\-\u4e00-\u9fff]+", "-", topic).strip("-") or "ai-storyboard-video")
    project_dir = output_dir / project_name
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load LLM config
    try:
        llm_cfg = load_llm_config()
    except (FileNotFoundError, ValueError) as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        return 2

    # 2. Generate storyboard via LLM
    prompt = build_user_prompt(topic, style, duration, scene_count, language, tone)
    try:
        data = await call_llm(llm_cfg, prompt)
    except Exception as e:
        print(json.dumps({"status": "error", "error": f"LLM call failed: {e}"}, ensure_ascii=False))
        return 2

    overview = data.get("overview", {})
    raw_scenes = data.get("scenes", [])
    raw_subtitles = data.get("subtitles", [])

    if not raw_scenes:
        print(json.dumps({"status": "error", "error": "LLM returned no scenes."}, ensure_ascii=False))
        return 2

    scenes = build_scenes(raw_scenes)
    subtitles = build_subtitles(raw_subtitles)

    # If LLM didn't generate subtitles, derive them from narration
    if not subtitles and scenes:
        for i, scene in enumerate(scenes):
            start = scene["start"]
            dur = scene["duration"]
            text = scene.get("narration", "")
            if text:
                mid = start + dur / 2
                subtitles.append({
                    "index": i + 1,
                    "start": round(start, 3),
                    "end": round(start + dur, 3),
                    "duration": max(0.05, round(dur - 0.012, 3)),
                    "text": text,
                    "scene": scene["id"],
                })

    total_duration = float(scenes[-1]["start"]) + float(scenes[-1]["duration"]) if scenes else 60.0
    narration_text = "\n".join(s.get("narration", "") for s in scenes)

    # 3. Write output files
    (assets_dir / "scenes.json").write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")
    (assets_dir / "subtitles.json").write_text(json.dumps(subtitles, ensure_ascii=False, indent=2), encoding="utf-8")
    (assets_dir / "narration.txt").write_text(narration_text, encoding="utf-8")
    write_silent_wav(assets_dir / "narration_fit.wav", total_duration)
    write_design(project_dir, overview)
    (project_dir / "index.html").write_text(build_html(scenes, subtitles, overview), encoding="utf-8")

    print(json.dumps({
        "status": "success",
        "completed": True,
        "project": str(project_dir),
        "scenes": len(scenes),
        "subtitles": len(subtitles),
        "duration": round(total_duration, 1),
        "topic": topic,
        "style": style,
        "language": language,
        "llm_provider": llm_cfg["provider"],
        "llm_model": llm_cfg["model"],
        "index": str(project_dir / "index.html"),
        "narration_text": str(assets_dir / "narration.txt"),
        "silent_audio": str(assets_dir / "narration_fit.wav"),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())

