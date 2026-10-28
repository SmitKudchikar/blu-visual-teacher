"""Export a lesson plus per-scene narration to a 720p H.264 MP4 and SRT."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 1280, 720
BG = (8, 17, 31)
PANEL = (18, 33, 53)
CYAN = (96, 221, 220)
TEXT = (237, 245, 255)
MUTED = (158, 176, 199)


def font(size: int, mono: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = ([r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf"] if mono else
                  [r"C:\Windows\Fonts\Nirmala.ttc", r"C:\Windows\Fonts\mangal.ttf", r"C:\Windows\Fonts\arial.ttf"])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=fnt)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def centered(draw: ImageDraw.ImageDraw, text: str, y: int, fnt: ImageFont.ImageFont, fill: tuple[int, int, int], max_width: int = 1080) -> int:
    lines = wrap(draw, text, fnt, max_width)
    line_height = draw.textbbox((0, 0), "Ag", font=fnt)[3] + 12
    for line in lines:
        box = draw.textbbox((0, 0), line, font=fnt)
        draw.text(((W - (box[2] - box[0])) / 2, y), line, font=fnt, fill=fill)
        y += line_height
    return y


def rounded_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt: ImageFont.ImageFont, fill: tuple[int, int, int] = TEXT) -> None:
    draw.rounded_rectangle(box, radius=18, fill=PANEL, outline=CYAN, width=3)
    max_width = box[2] - box[0] - 40
    max_height = box[3] - box[1] - 12
    size = getattr(fnt, "size", 23)
    while size >= 13:
        candidate = font(size)
        lines = wrap(draw, text, candidate, max_width)
        line_height = draw.textbbox((0, 0), "Ag", font=candidate)[3] + 5
        if len(lines) * line_height <= max_height and all(draw.textbbox((0, 0), line, font=candidate)[2] <= max_width for line in lines):
            break
        size -= 1
    if len(lines) * line_height > max_height:
        lines = lines[:max(1, max_height // line_height)]
        lines[-1] = lines[-1][:max(1, len(lines[-1]) - 2)].rstrip() + "…"
    y = (box[1] + box[3] - len(lines) * line_height) / 2
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=candidate)
        draw.text(((box[0] + box[2] - (bb[2] - bb[0])) / 2, y), line, font=candidate, fill=fill)
        y += line_height


def draw_object(draw: ImageDraw.ImageDraw, name: str, x: int, y: int, radius: int, phase: float = 0.0) -> None:
    """Small vector illustrations used by the offline MP4 renderer."""
    wobble = math.sin(phase * 4 * math.pi)
    y = int(y + (14 * wobble if name in {"earth", "cloud", "ball", "insect"} else 0))
    r = int(radius * (1 + .1 * wobble)) if name == "sun" else radius
    if name == "sun":
        for angle in range(0, 360, 30):
            a = math.radians(angle + 90 * phase)
            draw.line((x + math.cos(a)*r*.72, y + math.sin(a)*r*.72,
                       x + math.cos(a)*r, y + math.sin(a)*r), fill=(255, 190, 62), width=max(3, r//14))
        draw.ellipse((x-r*.62,y-r*.62,x+r*.62,y+r*.62), fill=(255,210,65), outline=(255,236,140), width=3)
    elif name == "moon":
        moon_r = int(r*.7)
        left, top = x-moon_r, y-moon_r
        diameter = moon_r*2 + 1
        draw.ellipse((left, top, x+moon_r, y+moon_r), fill=(49, 66, 88), outline=(203, 215, 226), width=2)
        mask = Image.new("L", (diameter, diameter), 0)
        mask_draw = ImageDraw.Draw(mask)
        illumination = .08 + .92 * (1 - math.cos(2*math.pi*phase)) / 2
        lit_width = max(0, min(diameter, round(diameter*illumination)))
        mask_draw.ellipse((0, 0, diameter-1, diameter-1), fill=255)
        if lit_width < diameter:
            mask_draw.rectangle((lit_width, 0, diameter, diameter), fill=0)
        draw._image.paste((239, 237, 216), (left, top, x+moon_r+1, y+moon_r+1), mask)
    elif name == "cloud":
        for dx,dy,rr in ((-.45,.2,.4),(0,-.1,.5),(.43,.17,.37)):
            draw.ellipse((x+(dx-rr)*r,y+(dy-rr)*r,x+(dx+rr)*r,y+(dy+rr)*r),fill=(220,237,247))
        draw.rounded_rectangle((x-r*.7,y,x+r*.7,y+r*.48),radius=int(r*.2),fill=(220,237,247))
    elif name in {"plant","flower"}:
        draw.line((x,y+r*.7,x,y-r*.45),fill=(87,193,116),width=max(4,r//10))
        draw.ellipse((x-r*.65,y-r*.12,x,y+r*.2),fill=(77,171,89))
        draw.ellipse((x,y-r*.38,x+r*.65,y-r*.05),fill=(104,211,112))
        if name == "flower":
            for angle in range(0,360,72):
                a=math.radians(angle)
                px=x+math.cos(a)*r*.32; py=y-r*.55+math.sin(a)*r*.32
                draw.ellipse((px-r*.22,py-r*.22,px+r*.22,py+r*.22),fill=(255,132,180))
            draw.ellipse((x-r*.17,y-r*.72,x+r*.17,y-r*.38),fill=(255,209,70))
    elif name == "water":
        draw.polygon([(x,y-r*.85),(x-r*.55,y+r*.25),(x-r*.4,y+r*.65),(x,y+r*.8),(x+r*.4,y+r*.65),(x+r*.55,y+r*.25)],fill=(75,178,241))
    elif name == "earth":
        draw.ellipse((x-r*.7,y-r*.7,x+r*.7,y+r*.7),fill=(68,146,218))
        draw.polygon([(x-r*.48,y-r*.25),(x-r*.1,y-r*.6),(x+r*.2,y-r*.3),(x+r*.05,y+r*.05),(x-r*.3,y+r*.1)],fill=(95,185,114))
        draw.ellipse((x+r*.1,y+r*.15,x+r*.55,y+r*.55),fill=(95,185,114))
    elif name == "ball":
        draw.ellipse((x-r*.7,y-r*.7,x+r*.7,y+r*.7),fill=(255,131,83),outline=(255,204,166),width=3)
        draw.arc((x-r*.55,y-r*.55,x+r*.55,y+r*.55),45,230,fill=(255,230,190),width=3)
    elif name == "insect":
        draw.ellipse((x-r*.25,y-r*.55,x+r*.25,y+r*.55),fill=(240,178,70))
        draw.ellipse((x-r*.27,y-r*.67,x+r*.27,y-r*.23),fill=(36,44,53))
        for dy in (-.3,0,.3):
            draw.line((x-r*.18,y+dy*r,x-r*.7,y+(dy-.22)*r),fill=(240,178,70),width=3)
            draw.line((x+r*.18,y+dy*r,x+r*.7,y+(dy-.22)*r),fill=(240,178,70),width=3)

def make_frame(lesson: dict[str, Any], scene: dict[str, Any], language: str, path: Path, phase: float = 1.0) -> None:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    # A restrained decorative grid keeps the same visual identity in every scene.
    for x in range(30, W, 60):
        draw.line((x, 0, x, H), fill=(13, 27, 44), width=1)
    for y in range(30, H, 60):
        draw.line((0, y, W, y), fill=(13, 27, 44), width=1)
    draw.text((64, 42), "BLU VISUAL TEACHER", font=font(20), fill=CYAN)
    draw.text((W - 420, 46), f"{lesson['level'].upper()}  ·  {lesson['targetDurationSeconds']}s", font=font(19), fill=MUTED)
    visual = scene["visual"]
    objects = lesson.get("visualObjects", [])
    caption = scene.get("captions", {}).get(language, scene.get("narration", {}).get(language, ""))

    if scene["type"] == "title-card":
        if objects:
            centered(draw, "A SHORT VISUAL LESSON", 104, font(22), CYAN)
            for i, name in enumerate(objects[:3]):
                draw_object(draw, name, int(W//2 + (i-(len(objects[:3])-1)/2)*180), 230, 68, phase)
            if "sun" in objects and "earth" in objects:
                sun_x = int(W//2 + (objects.index("sun")-(len(objects[:3])-1)/2)*180)
                earth_x = int(W//2 + (objects.index("earth")-(len(objects[:3])-1)/2)*180)
                for offset in (0, .33, .66):
                    t = (phase * 2 + offset) % 1
                    px = int(sun_x + (earth_x - sun_x) * t)
                    draw.ellipse((px-6, 224, px+6, 236), fill=(255, 223, 95))
            heading_end = centered(draw, visual["heading"], 345, font(50), TEXT, 1080)
            centered(draw, visual["subtitle"], max(440, heading_end + 12), font(25), MUTED, 1080)
        else:
            centered(draw, "A SHORT VISUAL LESSON", 196, font(22), CYAN)
            heading_end = centered(draw, visual["heading"], 265, font(54), TEXT, 1080)
            centered(draw, visual["subtitle"], min(510, max(380, heading_end + 14)), font(26), MUTED, 1080)
    elif scene["type"] == "diagram":
        centered(draw, "FOLLOW THE FLOW", 124, font(23), CYAN)
        nodes = visual["nodes"]
        positions = {node["id"]: (int(node["x"] * (W - 340) + 170), int(node["y"] * 360 + 100)) for node in nodes}
        arrows = visual["arrows"]
        scaled_progress = max(0.0, min(1.0, phase)) * max(1, len(arrows))
        for arrow_index, arrow in enumerate(arrows):
            x1, y1 = positions[arrow["from"]]
            x2, y2 = positions[arrow["to"]]
            start_x = x1 + (82 if x2 > x1 else -82)
            end_x = x2 - (82 if x2 > x1 else -82)
            arrow_progress = max(0.0, min(1.0, scaled_progress - arrow_index))
            if arrow_progress <= 0:
                continue
            tipx = start_x + (end_x - start_x) * arrow_progress
            tipy = y1 + (y2 - y1) * arrow_progress
            draw.line((start_x, y1, tipx, tipy), fill=(147, 184, 212), width=5)
            angle = math.atan2(y2 - y1, x2 - x1)
            p1 = (tipx - 16 * math.cos(angle - .55), tipy - 16 * math.sin(angle - .55))
            p2 = (tipx - 16 * math.cos(angle + .55), tipy - 16 * math.sin(angle + .55))
            if arrow_progress >= 1:
                draw.polygon([(tipx, tipy), p1, p2], fill=(147, 184, 212))
            if arrow.get("label") and arrow_progress >= 1:
                centered(draw, arrow["label"], max(190, (y1 + y2) // 2 - 35), font(17), CYAN, 300)
        active_node = min(len(nodes) - 1, int(max(0.0, min(.999, phase)) * max(1, len(nodes))))
        for node_index, node in enumerate(nodes):
            cx, cy = positions[node["id"]]
            box = (cx - 145, cy - 56, cx + 145, cy + 56)
            outline = (141, 243, 228) if node_index == active_node else CYAN
            draw.rounded_rectangle(box, radius=20, fill=PANEL, outline=outline, width=4 if node_index == active_node else 2)
            rounded_text(draw, box, node["label"], font(23))
        if "sun" in objects and "earth" in objects and "sun" in positions and "earth" in positions:
            sx, sy = positions["sun"]
            ex, ey = positions["earth"]
            for name, ox, oy in (("sun", sx, sy), ("earth", ex, ey)):
                draw_object(draw, name, ox, oy - 103, 42, phase)
            for offset in (0, .25, .5, .75):
                t = (phase * 2 + offset) % 1
                px = int(sx + (ex-sx)*t)
                py = int((sy-103) + ((ey-103)-(sy-103))*t)
                draw.ellipse((px-5,py-5,px+5,py+5),fill=(255,220,90))
    elif scene["type"] == "code-walkthrough":
        heading = visual.get("heading", {})
        if isinstance(heading, dict):
            heading = heading.get(language, heading.get("en-IN", "Trace the example step by step"))
        centered(draw, str(heading or "Trace the example step by step"), 124, font(23), CYAN)
        code_box = (58, 190, 826, 530)
        out_box = (850, 190, 1222, 530)
        draw.rounded_rectangle(code_box, radius=16, fill=(4, 11, 20), outline=(38, 57, 83), width=2)
        draw.rounded_rectangle(out_box, radius=16, fill=(4, 11, 20), outline=(38, 57, 83), width=2)
        code_lines = visual["code"]
        active_line = min(len(code_lines) - 1, int(max(0.0, min(.999, phase)) * max(1, len(code_lines)))) + 1
        for index, line in enumerate(code_lines[:6], start=1):
            yy = 224 + (index - 1) * 47
            if index == active_line and index in visual["highlightLines"]:
                draw.rounded_rectangle((72, yy - 5, 810, yy + 36), radius=8, fill=(23, 56, 74))
            size = 26
            line_font = font(size, mono=True)
            while draw.textbbox((0, 0), line, font=line_font)[2] > 700 and size > 14:
                size -= 2
                line_font = font(size, mono=True)
            draw.text((90, yy), line, font=line_font, fill=(141, 243, 228) if index == active_line else TEXT)
        draw.text((884, 218), "OUTPUT", font=font(19, mono=True), fill=CYAN)
        for index, line in enumerate(visual["output"][:6]):
            draw.text((886, 260 + index * 38), line[:26], font=font(23, mono=True), fill=(159, 240, 189))
    elif scene["type"] == "concept-card":
        centered(draw, visual["heading"], 150, font(38), CYAN)
        points = visual["points"]
        visible_points = max(1, min(len(points), math.ceil(phase * len(points))))
        spacing = min(82, 280 / max(1, len(points)))
        box_height = min(64, int(spacing - 8))
        for index, point in enumerate(points[:visible_points]):
            top = 225 + int(index * spacing)
            rounded_text(draw, (120, top, 1160, top + box_height), point, font(23 if len(points) > 2 else 25))

    if objects and scene["type"] != "title-card" and not (scene["type"] == "diagram" and "sun" in objects and "earth" in objects and "sun" in positions and "earth" in positions):
        for i, name in enumerate(objects[:3]):
            draw_object(draw, name, 900 + i*86, 127, 38, phase)

    # Captions are burned into the picture; SRT is also written separately.
    cap_font = font(21)
    cap_lines = wrap(draw, caption, cap_font, W - 190)
    if len(cap_lines) > 3:
        cap_lines = cap_lines[:3]
        cap_lines[-1] = cap_lines[-1].rstrip(" .…") + "…"
    cap_h = max(62, 28 + len(cap_lines) * 31)
    draw.rounded_rectangle((66, H - cap_h - 30, W - 66, H - 28), radius=13, fill=(2, 7, 13))
    yy = H - cap_h - 13
    for line in cap_lines:
        bb = draw.textbbox((0, 0), line, font=cap_font)
        draw.text(((W - (bb[2] - bb[0])) / 2, yy), line, font=cap_font, fill=TEXT)
        yy += 31
    image.save(path, format="PNG")


def timestamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a lesson to 720p H.264 using local narration")
    parser.add_argument("lesson", nargs="?", type=Path, default=ROOT / "data" / "lessons" / "sample-lesson.json")
    parser.add_argument("--language", default="en-IN")
    parser.add_argument("--narration", type=Path, default=ROOT / "build" / "narration")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "lesson.mp4")
    args = parser.parse_args()
    try:
        lesson = json.loads(args.lesson.read_text(encoding="utf-8"))
        manifest_path = args.narration / "voice_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Create narration first with: python scripts/narrate_lesson.py --language en-IN --voice David", file=sys.stderr)
        return 2
    if manifest.get("language") != args.language:
        print(f"ERROR: narration language is {manifest.get('language')}, requested {args.language}", file=sys.stderr)
        return 2
    scenes_by_id = {item["sceneId"]: item for item in manifest.get("scenes", [])}
    if len(scenes_by_id) != len(lesson.get("scenes", [])):
        print("ERROR: narration manifest does not contain one audio entry per lesson scene", file=sys.stderr)
        return 2
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    work = args.output.parent / (args.output.stem + "_work")
    work.mkdir(parents=True, exist_ok=True)
    segment_paths = []
    srt_cues = []
    for index, scene in enumerate(lesson["scenes"], start=1):
        timing = scenes_by_id.get(scene["id"])
        if not timing:
            print(f"ERROR: no narration timing for scene {scene['id']}", file=sys.stderr)
            return 2
        audio = args.narration / timing["audioFile"]
        if not audio.exists():
            print(f"ERROR: missing audio file {audio}", file=sys.stderr)
            return 2
        segment = work / f"{index:02}-segment.mp4"
        duration = float(timing["sceneDurationSeconds"])
        pause = float(timing["pauseSeconds"])
        visual = scene.get("visual", {})
        if scene["type"] == "diagram":
            animation_steps = len(visual.get("arrows", [])) or len(visual.get("nodes", []))
        elif scene["type"] == "code-walkthrough":
            animation_steps = len(visual.get("highlightLines", [])) or len(visual.get("code", []))
        elif scene["type"] == "concept-card":
            animation_steps = len(visual.get("points", []))
        else:
            animation_steps = 2
        frame_count = min(72, max(24, round(duration * 4))) if lesson.get("visualObjects") else max(2, min(4, animation_steps))
        frame_paths = []
        frame_list = []
        frame_duration = duration / frame_count
        for frame_index in range(frame_count):
            frame = work / f"{index:02}-phase-{frame_index:02}.png"
            make_frame(lesson, scene, args.language, frame, (frame_index + 1) / frame_count)
            frame_paths.append(frame)
            frame_list.extend([f"file '{frame.name}'", f"duration {frame_duration:.6f}"])
        frame_list.append(f"file '{frame_paths[-1].name}'")
        timeline = work / f"{index:02}-frames.txt"
        timeline.write_text("\n".join(frame_list) + "\n", encoding="utf-8")
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(timeline), "-i", str(audio), "-vf", "scale=1280:720,format=yuv420p", "-af", f"apad=pad_dur={pause}", "-c:v", "libx264", "-preset", "ultrafast", "-r", "30", "-fps_mode", "cfr", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(segment)]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: FFmpeg failed on scene {scene['id']}: {exc.stderr[-1200:]}", file=sys.stderr)
            return 1
        segment_paths.append(segment)
        start = float(timing["startSeconds"])
        audio_end = start + float(timing["audioDurationSeconds"])
        caption = scene.get("captions", {}).get(args.language, "")
        srt_cues.append(f"{index}\n{timestamp(start)} --> {timestamp(audio_end)}\n{caption}\n")
        print(f"Rendered scene {index}/{len(lesson['scenes'])}: {scene['id']} ({duration:.2f}s)")

    concat_file = work / "segments.txt"
    concat_file.write_text("\n".join(f"file '{path.name}'" for path in segment_paths) + "\n", encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(args.output)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: FFmpeg could not join scene videos: {exc.stderr[-1200:]}", file=sys.stderr)
        return 1
    srt_path = args.output.with_suffix(f".{args.language}.srt")
    srt_path.write_text("\n".join(srt_cues), encoding="utf-8-sig")
    print(f"MP4 created: {args.output}")
    print(f"SRT created: {srt_path}")
    print(f"Used local FFmpeg: {ffmpeg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

