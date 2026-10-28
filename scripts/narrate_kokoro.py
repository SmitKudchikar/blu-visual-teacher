"""Generate local English or Hindi narration with Kokoro ONNX."""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

from kokoro_onnx import Kokoro
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "models" / "kokoro" / "kokoro-v1.0.int8.onnx"
DEFAULT_VOICES = ROOT / "models" / "kokoro" / "voices-v1.0.bin"
HINDI_VOICES = ("hf_alpha", "hf_beta", "hm_omega", "hm_psi")
ENGLISH_VOICES = ("hm_omega", "hm_psi", "hf_alpha", "hf_beta", "af_heart", "af_bella", "am_adam", "bf_emma", "bm_george")


def timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthesize local English or Hindi narration with Kokoro ONNX")
    parser.add_argument("lesson", nargs="?", type=Path, default=ROOT / "data" / "lessons" / "sample-lesson.json")
    parser.add_argument("--language", choices=("en-IN", "hi-IN"), default="hi-IN")
    parser.add_argument("--voice", help="Kokoro voice; defaults to hf_alpha for Hindi and hm_omega for Indian-accented English")
    parser.add_argument("--speed", type=float, default=1.0, help="speech speed from 0.5 to 2.0")
    parser.add_argument("--pause", type=float, default=0.6)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--voices", type=Path, default=DEFAULT_VOICES)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.pause < 0:
        parser.error("--pause must be zero or greater")
    if not 0.5 <= args.speed <= 2.0:
        parser.error("--speed must be between 0.5 and 2.0")
    is_hindi = args.language == "hi-IN"
    valid_voices = HINDI_VOICES if is_hindi else ENGLISH_VOICES
    voice = args.voice or ("hf_alpha" if is_hindi else "hm_omega")
    if voice not in valid_voices:
        parser.error(f"voice {voice!r} is not a supported {'Hindi' if is_hindi else 'English'} voice; choose from {', '.join(valid_voices)}")
    language_code = "hi" if is_hindi else "en-us"
    language_name = "Hindi" if is_hindi else "English"
    if args.output is None:
        args.output = ROOT / "build" / ("narration-hi" if is_hindi else "narration-en")

    for asset in (args.model, args.voices):
        if not asset.is_file():
            print(f"ERROR: Kokoro asset not found: {asset}", file=sys.stderr)
            print("Download the ONNX model and voices-v1.0.bin from the official kokoro-onnx model-files release.", file=sys.stderr)
            return 2
    try:
        lesson = json.loads(args.lesson.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.language not in lesson.get("languages", []):
        print(f"ERROR: This lesson does not include {args.language}.", file=sys.stderr)
        return 1

    kokoro = Kokoro(str(args.model), str(args.voices))
    args.output.mkdir(parents=True, exist_ok=True)
    pending = []
    for index, scene in enumerate(lesson["scenes"], start=1):
        text = scene.get("narration", {}).get(args.language)
        if not text:
            print(f"ERROR: scene {scene.get('id')} has no {args.language} narration", file=sys.stderr)
            return 1
        path = args.output / f"{index:02}-{scene['id']}.wav"
        print(f"Synthesizing {language_name} {index}/{len(lesson['scenes'])}: {scene['id']}", flush=True)
        samples, sample_rate = kokoro.create(text, voice=voice, speed=args.speed, lang=language_code)
        sf.write(str(path), samples, sample_rate, subtype="PCM_16")
        pending.append((index, scene, path))

    cursor = 0.0
    manifest_scenes = []
    srt_cues = []
    for index, scene, path in pending:
        try:
            with wave.open(str(path), "rb") as wav:
                duration = wav.getnframes() / wav.getframerate()
        except (OSError, wave.Error, ZeroDivisionError) as exc:
            print(f"ERROR: Kokoro output is not a readable WAV at {path}: {exc}", file=sys.stderr)
            return 1
        end_audio = cursor + duration
        scene_end = end_audio + args.pause
        manifest_scenes.append({
            "sceneId": scene["id"], "audioFile": path.name,
            "audioDurationSeconds": round(duration, 3), "pauseSeconds": args.pause,
            "sceneDurationSeconds": round(duration + args.pause, 3),
            "startSeconds": round(cursor, 3), "endSeconds": round(scene_end, 3),
        })
        caption = scene.get("captions", {}).get(args.language, "")
        srt_cues.append(f"{index}\n{timestamp(cursor)} --> {timestamp(end_audio)}\n{caption}\n")
        print(f"{scene['id']}: {duration:.2f}s audio + {args.pause:.2f}s pause")
        cursor = scene_end

    manifest = {
        "lessonId": lesson["id"], "language": args.language, "voiceName": voice, "speed": args.speed,
        "voiceId": voice, "ttsEngine": "kokoro-onnx", "totalDurationSeconds": round(cursor, 3),
        "scenes": manifest_scenes,
    }
    (args.output / "voice_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / f"captions-{args.language}.srt").write_text("\n".join(srt_cues), encoding="utf-8-sig")
    print(f"\n{language_name} narration saved under: {args.output}")
    print(f"Total scene timeline: {cursor:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
