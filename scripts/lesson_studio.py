"""Run the Blu Visual Teacher concept input screen and local lesson planner."""
from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter, time_ns
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
PREVIEW = BUILD / "lesson-studio-preview.html"
STUDIO_PAGE = ROOT / "templates" / "lesson_studio.html"
STATIC_DIR = ROOT / "static"
PORT = 8765
LANGUAGES = {"en-IN", "hi-IN", "ta-IN"}
VOICE_OPTIONS = {
    "en-IN": {"hm_omega", "hm_psi", "hf_alpha", "hf_beta", "af_heart", "af_bella", "am_adam", "bf_emma", "bm_george"},
    "hi-IN": {"hf_alpha", "hf_beta", "hm_omega", "hm_psi"},
}
CURRENT_VIDEOS: dict[str, Path] = {}
CURRENT_AUDIO_DIRS: dict[str, Path] = {}
CACHE_VERSION = "blu-lesson-v10"


def cache_id(topic: str, level: str, duration: int, language: str, voice: str, speed: float) -> str:
    key = {"version": CACHE_VERSION, "topic": topic.strip().casefold(), "level": level,
           "duration": duration, "language": language, "voice": voice, "speed": speed}
    encoded = json.dumps(key, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, value: dict) -> None:
        self.send_bytes(status, "application/json; charset=utf-8", json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def send_event(self, value: dict) -> None:
        self.wfile.write(("data: " + json.dumps(value, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route.startswith("/audio/"):
            parts = route.split("/", 3)
            language = parts[2] if len(parts) > 2 else ""
            filename = unquote(parts[3]) if len(parts) > 3 else ""
            directory = CURRENT_AUDIO_DIRS.get(language)
            if not directory or not filename or Path(filename).name != filename or Path(filename).suffix.lower() != ".wav":
                self.send_bytes(404, "text/plain; charset=utf-8", b"Narration audio not found")
                return
            audio_path = directory / filename
            try:
                self.send_bytes(200, "audio/wav", audio_path.read_bytes())
            except OSError:
                self.send_bytes(404, "text/plain; charset=utf-8", b"Narration audio not found")
            return
        if route.startswith("/video/"):
            language = route.rsplit("/", 1)[-1]
            videos = {"hi-IN": BUILD / "lesson-hi.mp4", "en-IN": BUILD / "lesson-en.mp4"}
            path = CURRENT_VIDEOS.get(language) or videos.get(language)
            if not path or not path.is_file():
                self.send_bytes(404, "text/plain; charset=utf-8", b"Sample video not found")
                return
            self.send_video(path)
            return
        if route == "/favicon.ico":
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        static_assets = {
            "/static/lesson_studio.css": (STATIC_DIR / "lesson_studio.css", "text/css; charset=utf-8"),
            "/static/lesson_studio.js": (STATIC_DIR / "lesson_studio.js", "text/javascript; charset=utf-8"),
        }
        if route in static_assets:
            path, content_type = static_assets[route]
            try:
                self.send_bytes(200, content_type, path.read_bytes())
            except OSError as exc:
                self.send_bytes(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))
            return
        if route == "/":
            try:
                page = STUDIO_PAGE.read_bytes()
            except OSError as exc:
                self.send_bytes(500, "text/plain; charset=utf-8",
                                f"Could not load the lesson studio UI: {exc}".encode("utf-8"))
                return
            self.send_bytes(200, "text/html; charset=utf-8", page)
            return
        if route == "/preview":
            path = PREVIEW if PREVIEW.exists() else ROOT / "lesson-preview.html"
            try:
                self.send_bytes(200, "text/html; charset=utf-8", path.read_bytes())
            except OSError as exc:
                self.send_bytes(500, "text/plain; charset=utf-8", str(exc).encode())
            return
        self.send_bytes(404, "text/plain; charset=utf-8", b"Not found")

    def send_video(self, path: Path) -> None:
        size = path.stat().st_size
        start, end = 0, size - 1
        requested = self.headers.get("Range")
        if requested and requested.startswith("bytes=") and "," not in requested:
            bounds = requested[6:].split("-", 1)
            try:
                start = int(bounds[0]) if bounds[0] else max(0, size - int(bounds[1]))
                end = int(bounds[1]) if bounds[0] and bounds[1] else end
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            if start >= size or start < 0 or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
        partial = requested is not None
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as media:
            media.seek(start)
            self.wfile.write(media.read(end - start + 1))

    def do_POST(self) -> None:
        streaming = False
        if urlsplit(self.path).path != "/generate":
            self.send_json(404, {"ok": False, "error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16_000:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Expected lesson settings as a JSON object.")
            topic = str(data.get("topic", "")).strip()
            level = data.get("level")
            language = data.get("language")
            duration = int(data.get("duration", 0))
            voice = str(data.get("voice", "") or "")
            speed = float(data.get("speed", 1.0))
            if not voice and language == "en-IN":
                voice = "hm_omega"
            elif not voice and language == "hi-IN":
                voice = "hf_alpha"
            if not topic or len(topic) > 180:
                raise ValueError("Enter a topic up to 180 characters.")
            if level not in {"beginner", "intermediate"}:
                raise ValueError("Choose beginner or intermediate.")
            if language not in LANGUAGES:
                raise ValueError("Choose English (India), Hindi, or Tamil.")
            if not 30 <= duration <= 90:
                raise ValueError("Duration must be between 30 and 90 seconds.")
            if not 0.75 <= speed <= 1.25:
                raise ValueError("Speech speed must be between 0.75× and 1.25×.")
            if language in VOICE_OPTIONS and voice not in VOICE_OPTIONS[language]:
                raise ValueError(f"Choose a supported local voice for {language}.")
            if language == "ta-IN":
                voice = "windows-sapi"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            streaming = True
            self.send_event({"stage": "planning", "message": "Planning the lesson in the selected language."})
            BUILD.mkdir(parents=True, exist_ok=True)
            CURRENT_VIDEOS.pop(language, None)
            CURRENT_AUDIO_DIRS.clear()
            timings: dict[str, float] = {}
            request_started = perf_counter()
            key = cache_id(topic, level, duration, language, voice, speed)
            cache_dir = BUILD / "lesson-cache" / key
            marker = cache_dir / "complete.json"
            if marker.is_file() and all((cache_dir / name).is_file() for name in ("lesson.json", "preview.html", "video.mp4")):
                cache_started = perf_counter()
                try:
                    cached = json.loads(marker.read_text(encoding="utf-8"))
                    cached_lesson = json.loads((cache_dir / "lesson.json").read_text(encoding="utf-8"))
                    audio_languages = cached.get("audioLanguages", [])
                    if all((cache_dir / "audio" / locale).is_dir() for locale in audio_languages):
                        for audio_language in audio_languages:
                            CURRENT_AUDIO_DIRS[audio_language] = cache_dir / "audio" / audio_language
                        cached_video = cache_dir / "video.mp4"
                        CURRENT_VIDEOS[language] = cached_video
                        shutil.copy2(cache_dir / "preview.html", PREVIEW)
                        timings["cacheLookup"] = round(perf_counter() - cache_started, 3)
                        timings["total"] = round(perf_counter() - request_started, 3)
                        self.send_event({"stage": "preview", "message": f"Reused cached lesson. Available audio: {', '.join(CURRENT_AUDIO_DIRS) or 'none'}."})
                        self.send_event({"done": True, "ok": True, "cached": True, "fallback": cached.get("fallback", False),
                                         "videoReady": True, "title": cached_lesson.get("title", "Lesson"),
                                         "concept": cached_lesson.get("concept", ""), "duration": cached_lesson.get("targetDurationSeconds", duration),
                                         "timings": timings})
                        return
                except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                    CURRENT_AUDIO_DIRS.clear()
                    CURRENT_VIDEOS.pop(language, None)
            output = ROOT / "build" / "generated-lesson.json"
            # Plan the selected language first so the preview is available sooner.
            lesson_languages = language
            command = [sys.executable, str(ROOT / "scripts" / "generate_lesson.py"), topic, "--level", level,
                       "--duration", str(duration), "--languages", lesson_languages, "--output", str(output)]
            stage_started = perf_counter()
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=360)
            timings["planning"] = round(perf_counter() - stage_started, 3)
            self.send_event({"stage": "timing", "message": f"Planning completed in {timings['planning']:.2f}s."})
            if result.returncode:
                detail = (result.stderr or result.stdout)[-1200:]
                self.send_event({"done": True, "ok": False, "error": detail or "Lesson planning failed."})
                return
            lesson = json.loads(output.read_text(encoding="utf-8"))
            stage_started = perf_counter()
            render = subprocess.run([sys.executable, str(ROOT / "scripts" / "render_lesson.py"), str(output),
                                     "--output", str(PREVIEW)], cwd=ROOT, capture_output=True, text=True, timeout=60)
            timings["previewRender"] = round(perf_counter() - stage_started, 3)
            if render.returncode:
                detail = (render.stderr or render.stdout)[-1200:]
                self.send_event({"done": True, "ok": False,
                                 "error": detail or "Lesson preview rendering failed; see the studio terminal for details."})
                return
            fallback = "Using topic-specific fallback lesson:" in result.stdout or "Using built-in sample lesson:" in result.stdout
            fallback_reason = ""
            if fallback:
                fallback_line = next(
                    (line for line in result.stderr.splitlines() if line.startswith("Planner fallback:")),
                    "",
                )
                fallback_reason = fallback_line.partition(":")[2].strip()
            self.send_event({
                "stage": "preview",
                "message": f"Lesson ready: {lesson.get('title', 'Lesson')}. Preview is available; creating {language} narration…",
                "fallback": fallback,
                "fallbackReason": fallback_reason,
                "title": lesson.get("title", "Lesson"),
                "duration": lesson.get("targetDurationSeconds", duration),
            })
            narration_dirs: dict[str, Path] = {}
            narration_errors: dict[str, str] = {}
            video_path = BUILD / f"studio-lesson-{language}-{time_ns()}.mp4"
            try:
                for audio_language in (language,):
                    self.send_event({"stage": "narration", "message": f"Creating local {audio_language} narration…"})
                    narration_dir = BUILD / f"studio-narration-{audio_language}"
                    if audio_language in {"en-IN", "hi-IN"}:
                        narration_command = [sys.executable, str(ROOT / "scripts" / "narrate_kokoro.py"), str(output),
                                             "--language", audio_language, "--output", str(narration_dir)]
                        if audio_language == language:
                            narration_command.extend(["--voice", voice, "--speed", str(speed)])
                    else:
                        narration_command = [sys.executable, str(ROOT / "scripts" / "narrate_lesson.py"), str(output),
                                             "--language", audio_language, "--output", str(narration_dir)]
                        if audio_language == language:
                            narration_command.extend(["--speed", str(speed)])
                    narration_started = perf_counter()
                    narration = subprocess.run(narration_command, cwd=ROOT, capture_output=True, text=True, timeout=600)
                    timings[f"narration:{audio_language}"] = round(perf_counter() - narration_started, 3)
                    self.send_event({"stage": "timing", "message": f"{audio_language} narration completed in {timings[f'narration:{audio_language}']:.2f}s."})
                    if narration.returncode:
                        narration_errors[audio_language] = (narration.stderr or narration.stdout)[-700:]
                        continue
                    narration_dirs[audio_language] = narration_dir
                    CURRENT_AUDIO_DIRS[audio_language] = narration_dir
                sync_args = [sys.executable, str(ROOT / "scripts" / "render_lesson.py"), str(output), "--output", str(PREVIEW)]
                for narration_dir in narration_dirs.values():
                    sync_args.extend(["--narration-manifest", str(narration_dir / "voice_manifest.json")])
                stage_started = perf_counter()
                sync_render = subprocess.run(sync_args, cwd=ROOT, capture_output=True, text=True, timeout=60)
                timings["synchronizedPreview"] = round(perf_counter() - stage_started, 3)
                if sync_render.returncode == 0:
                    available = ", ".join(narration_dirs) or "none"
                    unavailable = ", ".join(f"{locale} (local voice unavailable)" for locale in narration_errors)
                    suffix = f"; unavailable: {unavailable}" if unavailable else ""
                    self.send_event({"stage": "preview", "message": f"Available preview audio: {available}{suffix}. Use Play, Pause, or Replay."})
                else:
                    print(f"Synchronized preview rendering failed: {(sync_render.stderr or sync_render.stdout)[-800:]}", file=sys.stderr)
                if language not in narration_dirs:
                    video_error = narration_errors.get(language, f"No local voice is available for {language}.")
                    self.send_event({"done": True, "ok": True, "fallback": fallback, "videoReady": False,
                                     "videoError": video_error, "title": lesson.get("title", "Lesson"),
                                     "duration": lesson.get("targetDurationSeconds", duration)})
                    return
                narration_dir = narration_dirs[language]
                self.send_event({"stage": "exporting", "message": f"Rendering the {language} MP4…"})
                export_started = perf_counter()
                export = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_lesson.py"), str(output),
                                         "--language", language, "--narration", str(narration_dir),
                                         "--output", str(video_path)], cwd=ROOT, capture_output=True,
                                        text=True, timeout=600)
                timings["export"] = round(perf_counter() - export_started, 3)
                self.send_event({"stage": "timing", "message": f"MP4 export completed in {timings['export']:.2f}s."})
                if export.returncode:
                    video_error = (export.stderr or export.stdout)[-1000:]
                    self.send_event({"done": True, "ok": True, "fallback": fallback, "videoReady": False,
                                         "videoError": video_error, "title": lesson.get("title", "Lesson"),
                                         "duration": lesson.get("targetDurationSeconds", duration)})
                    return
            except subprocess.TimeoutExpired as exc:
                step = "Narration" if exc.cmd and "narrate" in str(exc.cmd) else "MP4 export"
                self.send_event({"done": True, "ok": True, "fallback": fallback, "videoReady": False,
                                     "videoError": f"{step} timed out. The lesson preview is still available.",
                                     "title": lesson.get("title", "Lesson"),
                                     "duration": lesson.get("targetDurationSeconds", duration)})
                return
            CURRENT_VIDEOS[language] = video_path
            timings["total"] = round(perf_counter() - request_started, 3)
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / "audio").mkdir(exist_ok=True)
            shutil.copy2(output, cache_dir / "lesson.json")
            shutil.copy2(PREVIEW, cache_dir / "preview.html")
            shutil.copy2(video_path, cache_dir / "video.mp4")
            for audio_language, narration_dir in narration_dirs.items():
                shutil.copytree(narration_dir, cache_dir / "audio" / audio_language, dirs_exist_ok=True)
            (cache_dir / "complete.json").write_text(json.dumps({
                "audioLanguages": list(narration_dirs), "fallback": fallback, "timings": timings,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            self.send_event({"done": True, "ok": True, "fallback": fallback,
                                 "fallbackReason": fallback_reason, "videoReady": True,
                                 "title": lesson.get("title", "Lesson"),
                                 "concept": lesson.get("concept", ""),
                                 "duration": lesson.get("targetDurationSeconds", duration), "timings": timings})
        except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
            if streaming:
                self.send_event({"done": True, "ok": False, "error": str(exc)})
            else:
                self.send_json(400, {"ok": False, "error": str(exc)})
        except subprocess.TimeoutExpired:
            if streaming:
                self.send_event({"done": True, "ok": False, "error": "A local step timed out. The lesson screen can retry using the built-in fallback."})
            else:
                self.send_json(504, {"ok": False, "error": "The local planner timed out. Submit again to use the sample fallback."})
        except Exception as exc:
            if streaming:
                self.send_event({"done": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            else:
                self.send_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    print(f"Blu Visual Teacher is ready at http://127.0.0.1:{PORT}")
    print("Press Ctrl+C to stop the local screen.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
