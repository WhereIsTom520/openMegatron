from __future__ import annotations

import json
import sys
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


def check_pillow():
    try:
        from PIL import Image, __version__
        return (Image, __version__)
    except ImportError:
        return (None, None)


def get_image_info(image, path: str) -> dict:
    return {
        "file": path,
        "format": image.format or "unknown",
        "mode": image.mode,
        "width": image.width,
        "height": image.height,
        "size_bytes": Path(path).stat().st_size,
    }


def main() -> int:
    args = parse_cli_args()
    action = args.get("action", "")
    input_path = args.get("input", "")
    if not action or not input_path:
        print(json.dumps({"status": "error", "error": "Missing required 'action' or 'input'."}, ensure_ascii=False))
        return 2

    pillow, ver = check_pillow()
    if not pillow:
        print(json.dumps({"status": "error", "error": "Pillow not found. Install with: pip install Pillow"}, ensure_ascii=False))
        return 2

    Image = pillow
    input_path = str(Path(input_path).expanduser())
    output = str(Path(args["output"]).expanduser()) if args.get("output") else ""
    overwrite = args.get("overwrite", False)

    try:
        if action == "info":
            img = Image.open(input_path)
            info = get_image_info(img, input_path)
            img.close()
            print(json.dumps({"status": "success", "action": "info", **info}, ensure_ascii=False, indent=2))
            return 0

        if action == "convert":
            from PIL import Image as PIL_Image
            img = PIL_Image.open(input_path)
            fmt = args.get("format", "").upper() or Path(output).suffix.lstrip(".").upper()
            if not fmt:
                fmt = img.format or "PNG"
            out_path = Path(output)
            if not out_path.suffix:
                out_path = out_path.with_suffix("." + fmt.lower())
            if out_path.exists() and not overwrite:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            quality = args.get("quality", 85)
            if fmt in ("JPEG", "JPG"):
                img = img.convert("RGB")
                img.save(str(out_path), format="JPEG", quality=quality)
            elif fmt == "WEBP":
                img.save(str(out_path), format="WEBP", quality=quality)
            else:
                img.save(str(out_path), format=fmt)
            img.close()
            print(json.dumps({"status": "success", "action": "convert", "output": str(out_path.resolve())}, ensure_ascii=False, indent=2))
            return 0

        if action == "resize":
            from PIL import Image as PIL_Image
            img = PIL_Image.open(input_path)
            w = args.get("width") or img.width
            h = args.get("height") or img.height
            mode = args.get("mode", "fit")
            if mode == "fit":
                img.thumbnail((w, h), PIL_Image.LANCZOS)
            elif mode == "fill":
                img = img.resize((w, h), PIL_Image.LANCZOS)
            elif mode == "exact":
                img = img.resize((w, h), PIL_Image.NEAREST)
            out_path = Path(output) if output else Path(input_path)
            if out_path.exists() and not overwrite and output:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            img.save(str(out_path))
            img.close()
            print(json.dumps({"status": "success", "action": "resize", "output": str(out_path.resolve()),
                              "width": w, "height": h}, ensure_ascii=False, indent=2))
            return 0

        if action == "compress":
            from PIL import Image as PIL_Image
            img = PIL_Image.open(input_path)
            quality = args.get("quality", 70)
            out_path = Path(output) if output else Path(input_path)
            fmt = img.format or "JPEG"
            if fmt in ("PNG",):
                img.quantize(colors=256, method=PIL_Image.Quantize.MEDIANCUT)
                img.save(str(out_path), format="PNG", optimize=True)
            elif fmt in ("JPEG", "JPG"):
                img = img.convert("RGB")
                img.save(str(out_path), format="JPEG", quality=quality, optimize=True)
            elif fmt == "WEBP":
                img.save(str(out_path), format="WEBP", quality=quality)
            else:
                img.save(str(out_path), format=fmt, optimize=True)
            img.close()
            new_size = out_path.stat().st_size
            old_size = Path(input_path).stat().st_size
            saved_pct = round((1 - new_size / old_size) * 100, 1) if old_size else 0
            print(json.dumps({"status": "success", "action": "compress", "output": str(out_path.resolve()),
                              "old_bytes": old_size, "new_bytes": new_size, "saved_pct": saved_pct}, ensure_ascii=False, indent=2))
            return 0

        if action == "crop":
            from PIL import Image as PIL_Image
            img = PIL_Image.open(input_path)
            w, h = img.size
            box_w = args.get("width", w)
            box_h = args.get("height", h)
            left = (w - box_w) // 2
            top = (h - box_h) // 2
            cropped = img.crop((left, top, left + box_w, top + box_h))
            out_path = Path(output) if output else Path(input_path)
            if out_path.exists() and not overwrite and output:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            cropped.save(str(out_path))
            img.close()
            cropped.close()
            print(json.dumps({"status": "success", "action": "crop", "output": str(out_path.resolve()),
                              "width": box_w, "height": box_h}, ensure_ascii=False, indent=2))
            return 0

        if action == "flip":
            from PIL import Image as PIL_Image
            img = PIL_Image.open(input_path)
            direction = args.get("direction", "horizontal")
            if direction == "horizontal":
                flipped = img.transpose(PIL_Image.FLIP_LEFT_RIGHT)
            elif direction == "vertical":
                flipped = img.transpose(PIL_Image.FLIP_TOP_BOTTOM)
            else:
                flipped = img.transpose(PIL_Image.FLIP_LEFT_RIGHT).transpose(PIL_Image.FLIP_TOP_BOTTOM)
            out_path = Path(output) if output else Path(input_path)
            if out_path.exists() and not overwrite and output:
                print(json.dumps({"status": "error", "error": f"Output exists: {out_path}"}, ensure_ascii=False))
                return 2
            flipped.save(str(out_path))
            img.close()
            flipped.close()
            print(json.dumps({"status": "success", "action": "flip", "output": str(out_path.resolve()),
                              "direction": direction}, ensure_ascii=False, indent=2))
            return 0

        if action == "batch":
            from PIL import Image as PIL_Image
            root_dir = Path(input_path)
            pattern = args.get("pattern", "*")
            recursive = args.get("recursive", False)
            output_dir = Path(output) if output else root_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            files = list(root_dir.glob(pattern))
            if recursive:
                files.extend(root_dir.rglob(pattern))
            results = []
            for f in files:
                if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
                    continue
                try:
                    img = PIL_Image.open(str(f))
                    fmt = args.get("format", "").upper() or img.format or "PNG"
                    quality = args.get("quality", 85)
                    out_f = output_dir / f"processed_{f.name}"
                    if fmt in ("JPEG", "JPG"):
                        img = img.convert("RGB")
                        img.save(str(out_f), format="JPEG", quality=quality)
                    elif fmt == "WEBP":
                        img.save(str(out_f), format="WEBP", quality=quality)
                    else:
                        img.save(str(out_f), format=fmt)
                    img.close()
                    results.append(str(out_f))
                except Exception as e:
                    results.append(f"{f.name}: error - {e}")
            print(json.dumps({"status": "success", "action": "batch", "processed": len(results),
                              "outputs": results}, ensure_ascii=False, indent=2))
            return 0

        print(json.dumps({"status": "error", "error": f"Unknown action: {action}"}, ensure_ascii=False))
        return 2

    except Exception as e:
        print(json.dumps({"status": "error", "action": action, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
