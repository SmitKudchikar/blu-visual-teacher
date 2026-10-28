"""Create offline per-scene narration, scene timing, and an SRT for one lesson language.

Uses Windows SAPI voices exposed by pyttsx3. Install the desired speech voices in
Windows before going offline; this script never sends lesson text to a cloud service.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from pathlib import Path
from typing import Any

import pyttsx3
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_NAMES = {
    "en-IN": "English (India)", "hi-IN": "Hindi", "ta-IN": "Tamil",
    "te-IN": "Telugu", "bn-IN": "Bengali", "mr-IN": "Marathi",
}
LANGUAGE_ALIASES = {
    "en-IN": ("en-in", "english india", "india english"),
    "hi-IN": ("hi-in", "hindi"),
    "ta-IN": ("ta-in", "tamil"),
    "te-IN": ("te-in", "telugu"),
    "bn-IN": ("bn-in", "bengali"),
    "mr-IN": ("mr-in", "marathi"),
}


def voice_text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    return str(value or "")


def voice_description(voice: Any) -> str:
    langs = " ".join(voice_text(item) for item in getattr(voice, "languages", []))
    return f"{voice_text(getattr(voice, 'name', 'Unnamed voice'))} | {voice_text(getattr(voice, 'id', ''))} | {langs}"


def find_voice(voices: list[Any], language: str) -> Any | None:
    aliases = LANGUAGE_ALIASES[language]
    for voice in voices:
        details = voice_description(voice).lower()
        if any(alias in details for alias in aliases):
            return voice
    return None


def timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def safe_name(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return slug or "scene"


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthesize local narration and audio-based scene timings")
    parser.add_argument("lesson", nargs="?", type=Path, default=ROOT / "data" / "lessons" / "sample-lesson.json")
    parser.add_argument("--language", choices=LANGUAGE_NAMES, default="en-IN")
    parser.add_argument("--pause", type=float, default=0.6, help="seconds to add after each scene narration")
    parser.add_argument("--speed", type=float, default=1.0, help="speech speed from 0.75 to 1.25")
    parser.add_argument("--voice", help="optional substring of a locally installed voice name or ID")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "narration")
    parser.add_argument("--list-voices", action="store_true", help="list locally installed SAPI voices and exit")
    args = parser.parse_args()

    try:
        engine = pyttsx3.init()
        voices = list(engine.getProperty("voices") or [])
    except Exception as exc:
        if args.list_voices:
            print(f"ERROR: Windows local speech voices could not be loaded: {type(exc).__name__}: {exc}", file=sys.stderr)
            print("Install and enable a local voice for the requested language, then retry.", file=sys.stderr)
            return 3
        print(f"ERROR: Windows local speech voices could not be loaded: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"No audio was generated for {LANGUAGE_NAMES.get(args.language, args.language)}. Check Windows speech settings or use a bundled local TTS model.", file=sys.stderr)
        return 3
    if args.list_voices:
        if not voices:
            print("No SAPI voices found. Install a Windows speech voice and try again.")
            return 1
        for voice in voices:
            print(voice_description(voice))
        return 0

    if args.pause < 0:
        parser.error("--pause must be zero or greater")
    if not 0.75 <= args.speed <= 1.25:
        parser.error("--speed must be between 0.75 and 1.25")
    if args.language not in LANGUAGE_NAMES:
        parser.error("unsupported language")
    try:
        lesson = json.loads(args.lesson.read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "schemas" / "lesson.schema.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    problems = list(Draft202012Validator(schema).iter_errors(lesson))
    if problems:
        for problem in problems:
            path = ".".join(map(str, problem.absolute_path)) or "<root>"
            print(f"Invalid lesson at {path}: {problem.message}", file=sys.stderr)
        return 1
    if args.language not in lesson["languages"]:
        print(f"ERROR: {args.language} is not included in this lesson's languages", file=sys.stderr)
        return 1

    if args.voice:
        voice = next((item for item in voices if args.voice.casefold() in voice_description(item).casefold()), None)
        if voice is None:
            print(f"ERROR: No installed voice matches {args.voice!r}. Use --list-voices to see choices.", file=sys.stderr)
            return 3
        if find_voice([voice], args.language) is None:
            print(f"WARNING: {voice_text(voice.name)} may not match requested language/accent {args.language}; using it because --voice was explicit.", file=sys.stderr)
    else:
        voice = find_voice(voices, args.language)
    if voice is None:
        print(f"ERROR: No local {LANGUAGE_NAMES[args.language]} voice was detected.", file=sys.stderr)
        print("Available voices:", file=sys.stderr)
        for item in voices:
            print(f"- {voice_description(item)}", file=sys.stderr)
        print("Install a matching Windows speech voice (Settings > Time & language > Speech), then retry.", file=sys.stderr)
        return 3

    args.output.mkdir(parents=True, exist_ok=True)
    engine.setProperty("voice", voice.id)
    try:
        engine.setProperty("rate", round(float(engine.getProperty("rate")) * args.speed))
    except (TypeError, ValueError):
        print("WARNING: this local SAPI engine does not expose a speech-rate setting; using its default speed.", file=sys.stderr)
    jobs = []
    for index, scene in enumerate(lesson["scenes"], start=1):
        text = scene["narration"].get(args.language)
        if not text:
            print(f"ERROR: scene {scene['id']} has no narration for {args.language}", file=sys.stderr)
            return 1
        filename = f"{index:02}-{safe_name(scene['id'])}.wav"
        audio_path = args.output / filename
        engine.save_to_file(text, str(audio_path))
        jobs.append((index, scene, text, filename, audio_path))
        print(f"Queued narration {index}/{len(lesson['scenes'])}: {scene['id']}", flush=True)

    print("Synthesizing queued narration; please wait...", flush=True)
    engine.runAndWait()

    manifest_scenes = []
    srt_cues = []
    cursor = 0.0
    for index, scene, text, filename, audio_path in jobs:
        try:
            with wave.open(str(audio_path), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
        except (OSError, wave.Error, ZeroDivisionError) as exc:
            print(f"ERROR: TTS did not produce a readable WAV at {audio_path}: {exc}", file=sys.stderr)
            return 1
        end_audio = cursor + duration
        scene_end = end_audio + args.pause
        manifest_scenes.append({
            "sceneId": scene["id"], "audioFile": filename,
            "audioDurationSeconds": round(duration, 3),
            "pauseSeconds": args.pause,
            "sceneDurationSeconds": round(duration + args.pause, 3),
            "startSeconds": round(cursor, 3), "endSeconds": round(scene_end, 3),
        })
        caption = scene["captions"].get(args.language, text)
        srt_cues.append(f"{index}\n{timestamp(cursor)} --> {timestamp(end_audio)}\n{caption}\n")
        print(f"{scene['id']}: {duration:.2f}s audio + {args.pause:.2f}s pause")
        cursor = scene_end

    manifest = {
        "lessonId": lesson["id"], "language": args.language,
        "voiceName": voice_text(getattr(voice, "name", "")),
        "voiceId": voice.id, "totalDurationSeconds": round(cursor, 3),
        "speed": args.speed,
        "scenes": manifest_scenes,
    }
    (args.output / "voice_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / f"captions-{args.language}.srt").write_text("\n".join(srt_cues), encoding="utf-8-sig")
    print(f"\nNarration saved under: {args.output}")
    print(f"Total scene timeline: {cursor:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


