from __future__ import annotations

import json
import subprocess
import sys
import tempfile
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


def check_ffmpeg() -> str | None:
    """Return ffmpeg path if available, else None."""
    for cmd in ("ffmpeg", "ffmpeg.exe"):
        try:
            subprocess.run([cmd, "-version"], capture_output=True, timeout=5)
            return cmd
        except (FileNotFoundError, subprocess.SubprocessError):
            continue
    return None


def ffprobe_meta(input_path: str) -> dict:
    """Get video metadata via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        input_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return {"status": "error", "error": result.stderr.strip()}
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    return {
        "status": "success",
        "file": input_path,
        "format": fmt.get("format_name", ""),
        "duration_s": float(fmt.get("duration", 0)),
        "size_bytes": int(fmt.get("size", 0)),
        "bitrate": fmt.get("bit_rate", ""),
        "video": {
            "codec": video_stream.get("codec_name", ""),
            "width": video_stream.get("width", 0),
            "height": video_stream.get("height", 0),
            "fps": eval_fps(video_stream.get("r_frame_rate", "0/1")),
            "pix_fmt": video_stream.get("pix_fmt", ""),
        },
        "audio": {
            "codec": audio_stream.get("codec_name", ""),
            "sample_rate": audio_stream.get("sample_rate", ""),
            "channels": audio_stream.get("channels", 0),
        } if audio_stream else None,
    }


def eval_fps(r_frame_rate: str) -> float:
    try:
        parts = r_frame_rate.split("/")
        return round(float(parts[0]) / float(parts[1]), 3) if len(parts) == 2 else float(parts[0])
    except (ValueError, ZeroDivisionError, IndexError):
        return 0.0


def build_cmd(action: str, args: dict) -> list[str]:
    output = str(Path(args["output"]).expanduser()) if args.get("output") else ""
    overwrite = args.get("overwrite", False)
    base = ["ffmpeg", "-y"] if overwrite else ["ffmpeg", "-n"]

    if action == "trim":
        input_path = str(Path(args["input"]).expanduser())
        cmd = base + ["-i", input_path]
        if args.get("ss"):
            cmd += ["-ss", str(args["ss"])]
        if args.get("t"):
            cmd += ["-t", str(args["t"])]
        elif args.get("to"):
            cmd += ["-to", str(args["to"])]
        cmd += ["-c", "copy"] if not args.get("recode") else []
        if not output:
            return cmd
        cmd.append(output)
        return cmd

    if action == "concat":
        inputs = args.get("inputs", [])
        if not inputs:
            return base
        file_list = []
        for p in inputs:
            abs_p = str(Path(p).expanduser().resolve())
            file_list.append(f"file '{abs_p}'")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        tmp.write("\n".join(file_list))
        tmp.close()
        cmd = base + ["-f", "concat", "-safe", "0", "-i", tmp.name]
        if output:
            cmd += ["-c", "copy", output]
        return cmd

    if action == "add_subtitles":
        input_path = str(Path(args["input"]).expanduser())
        sub_path = str(Path(args["subtitle_file"]).expanduser())
        cmd = base + ["-i", input_path, "-vf", f"subtitles={sub_path}"]
        if not output:
            return cmd
        cmd.append(output)
        return cmd

    if action == "add_audio":
        input_path = str(Path(args["input"]).expanduser())
        audio_path = str(Path(args["audio_file"]).expanduser())
        replace = args.get("audio_replace", False)
        volume = args.get("audio_volume", 1.0)
        cmd = base + ["-i", input_path, "-i", audio_path]
        if replace:
            cmd += ["-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0"]
        else:
            cmd += ["-filter_complex",
                    f"[1:a]volume={volume}[a1];[0:a][a1]amix=inputs=2:duration=first"]
        if not output:
            return cmd
        cmd.append(output)
        return cmd

    if action == "convert":
        input_path = str(Path(args["input"]).expanduser())
        cmd = base + ["-i", input_path]
        target_format = args.get("format", "")
        vcodec = args.get("vcodec", "")
        acodec = args.get("acodec", "")
        crf = args.get("crf", 23)
        bitrate = args.get("bitrate", "")
        width = args.get("width")
        height = args.get("height")
        fps = args.get("fps")

        if target_format == "gif":
            stem = Path(output).stem if output else Path(input_path).stem
            palette_path = Path(tempfile.gettempdir()) / f"{stem}_palette.png"
            subprocess.run(
                ["ffmpeg", "-y" if overwrite else "-n", "-i", input_path,
                 "-vf", f"fps={fps or 10},scale={width or 720}:-1:flags=lanczos,palettegen",
                 str(palette_path)],
                capture_output=True, timeout=60,
            )
            cmd = ["ffmpeg", "-y", "-i", input_path, "-i", str(palette_path),
                   "-lavfi", f"fps={fps or 10},scale={width or 720}:-1:flags=lanczos[x];[x][1:v]paletteuse"]
            if output:
                cmd.append(output)
            return cmd

        if vcodec:
            cmd += ["-c:v", vcodec]
        if acodec:
            cmd += ["-c:a", acodec]
        if crf and vcodec and vcodec != "copy":
            cmd += ["-crf", str(crf)]
        if bitrate:
            cmd += ["-b:v", bitrate]
        if width and height:
            cmd += ["-vf", f"scale={width}:{height}"]
        elif width:
            cmd += ["-vf", f"scale={width}:-2"]
        elif height:
            cmd += ["-vf", f"scale=-2:{height}"]
        if fps:
            cmd += ["-r", str(fps)]
        if not output:
            return cmd
        if target_format and not output.lower().endswith(f".{target_format}"):
            output = f"{output}.{target_format}"
        cmd.append(output)
        return cmd

    if action == "extract_frames":
        input_path = str(Path(args["input"]).expanduser())
        output_pattern = output or "frame_%04d.png"
        rate = args.get("fps", 1)
        max_frames = args.get("max_frames")
        cmd = base + ["-i", input_path, "-vf", f"fps={rate}"]
        if max_frames:
            cmd += ["-vframes", str(max_frames)]
        cmd.append(output_pattern)
        return cmd

    if action == "extract_audio":
        input_path = str(Path(args["input"]).expanduser())
        cmd = base + ["-i", input_path, "-vn"]
        acodec = args.get("acodec", "mp3")
        if acodec:
            cmd += ["-c:a", acodec]
        if not output:
            return cmd
        cmd.append(output)
        return cmd

    return base


def main() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    if not action:
        print(json.dumps({"status": "error", "error": "Missing required 'action' parameter."}, ensure_ascii=False))
        return 2

    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        print(json.dumps({"status": "error", "error": "FFmpeg not found. Install FFmpeg and add it to PATH."}, ensure_ascii=False))
        return 2

    try:
        if action == "info":
            result = ffprobe_meta(str(Path(args["input"]).expanduser()))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("status") == "success" else 2

        cmd = build_cmd(action, args)
        if not cmd or len(cmd) < 2:
            print(json.dumps({"status": "error", "error": f"Invalid parameters for action '{action}'."}, ensure_ascii=False))
            return 2

        logger_cmd = " ".join(c if " " not in c else f'"{c}"' for c in cmd)
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if process.returncode != 0:
            error_msg = process.stderr.strip() or f"FFmpeg exited with code {process.returncode}"
            print(json.dumps({"status": "error", "action": action, "error": error_msg}, ensure_ascii=False))
            return 2

        output_path = cmd[-1] if not cmd[-1].startswith("-") else ""
        print(json.dumps({
            "status": "success",
            "action": action,
            "output": str(Path(output_path).resolve()) if output_path else None,
            "command": logger_cmd,
        }, ensure_ascii=False, indent=2))
        return 0

    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "error", "action": action, "error": "FFmpeg timed out (600s limit)."}, ensure_ascii=False))
        return 2
    except Exception as e:
        print(json.dumps({"status": "error", "action": action, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


