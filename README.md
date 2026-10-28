# Blu Visual Teacher

Blu turns a topic into a short lesson plan, a browser preview, narration, captions, and an H.264/AAC MP4. The planner talks only to the local Ollama server at `127.0.0.1`; narration and export use local files and tools.

## Run on Windows

1. Install Python 3.11 or newer and [Ollama](https://ollama.com/download).
2. From this folder, create and populate a virtual environment:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. While online, download the configured planner model and the Kokoro files if they are not already present:

   ```powershell
   ollama pull qwen2.5:3b
   ```

   Put `kokoro-v1.0.int8.onnx` and `voices-v1.0.bin` in `models/kokoro/`. The project currently includes both files. Start Ollama before launching the studio:

   ```powershell
   ollama serve
   python scripts/lesson_studio.py
   ```

4. Open `http://127.0.0.1:8765`, enter a topic, choose level, language, and duration, then create the lesson. Once Ollama, Python packages, Kokoro files, FFmpeg, and any required Windows speech voices are installed, disconnect Wi-Fi and run the same flow to check offline behavior.

The planner falls back to a topic-specific local lesson if Ollama is unavailable or rejects its response. That fallback still needs a usable local voice backend for a narrated MP4.

## Models, voices, and licenses

| Component | Use | License / status |
|---|---|---|
| Ollama `qwen2.5:3b` | Local lesson planning | The corresponding Qwen2.5-3B-Instruct model is marked `qwen-research`; read the [model license](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct) and [license file](https://huggingface.co/Qwen/Qwen2.5-3B/blob/main/LICENSE) before use or redistribution. Do not assume it is Apache-2.0. |
| Kokoro v1.0 INT8 ONNX | Local English and Hindi narration | The [Kokoro model page](https://huggingface.co/hexgrad/Kokoro-82M) lists Apache-2.0 for model weights. The ONNX runtime project is MIT; see its [license and model-file releases](https://github.com/thewh1teagle/kokoro-onnx/releases). Review the [voice list and any voice-specific attribution](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md) separately. |
| Kokoro `voices-v1.0.bin` | Local voice embeddings | Voice bundle is downloaded separately from the runtime package. Check the upstream voice documentation for per-voice terms before redistribution. |
| Windows SAPI voice (Tamil path) | Optional local narration through `pyttsx3` | The voice is supplied by the Windows installation and is not bundled here. Its availability and terms depend on the installed voice pack. SAPI did not initialize in the development environment, so Tamil narration is not verified there. |

The studio plans one lesson with English (India), Hindi, and Tamil narration and captions, attempts local narration for all three, and exports MP4 audio in the language selected in the input form. Available language tracks can be switched in the synchronized browser preview. English defaults to Kokoro's `hm_omega` voice speaking English (`en-us`); Hindi defaults to `hf_alpha`. Other Kokoro voice IDs can be passed with `--voice`. Tamil uses a locally installed Windows SAPI voice, not a bundled Tamil model. There is no IndicTrans2 model wired into the current pipeline. Tamil voice-over remains dependent on a matching installed SAPI voice and is not verified on this machine, so the three-language audio requirement is not yet fully met.

The studio exposes the available Kokoro voices for English and Hindi and a speech-speed slider (0.75×–1.25×). SAPI speech rate is adjusted when the installed engine supports it. Each generation shows measured planning, narration, preview, export, and total times in the UI. Completed lesson packages are cached under `build/lesson-cache/` by normalized topic, level, duration, selected language, voice, speed, and pipeline version; repeat requests reuse the matching lesson, audio, preview, and MP4.

## Other runtime components

- `requirements.txt` lists Python dependencies.
- `imageio-ffmpeg` supplies the FFmpeg executable used for 1280×720 H.264/AAC export. Review the [imageio-ffmpeg notices](https://github.com/imageio/imageio-ffmpeg) and the bundled FFmpeg build's license configuration before redistributing binaries.
- Export uses Windows Nirmala fonts when installed, with Arial as a fallback. Noto fonts are not bundled, so verify Indic glyph rendering on the target demo laptop.
- The fixed renderer fixture is `data/lessons/sample-lesson.json`; prompt regression cases are in `data/prompts/lesson_prompt_eval_cases.json`.

## Offline demo checklist

Before judging, verify `ollama list` includes `qwen2.5:3b`, start `ollama serve`, disconnect Wi-Fi, and run `python scripts/lesson_studio.py`. In the five-minute demo, enter a fresh judge-selected topic and language, generate the preview, and play it. If the planner fails, use a supported topic with the built-in local fallback and play that lesson; do not claim arbitrary topics have a curated fallback. Confirm the local voice for the chosen language works before the demo. Reserve the following three minutes for questions.

## Checks and hardware record

The project has been checked for Python syntax, schema validity, fallback handling across the prompt cases, browser preview generation, and MP4/SRT export. The submission Moon phases pair uses generated English and Hindi Kokoro narration. Tamil voice availability and Wi-Fi-off end-to-end behavior remain unverified.

**Test laptop:** 12th Gen Intel Core i5-12450H, 15.7 GB RAM, Windows 11 Home Single Language. CPU/GPU inference mode has not been measured for this run. Record Ollama's actual inference mode and test once with Wi-Fi disabled on the demo laptop.

## Current limitations

- The browser lesson includes English, Hindi, and Tamil text, with a language selector and synchronized playback for each voice track that generated successfully. Missing local voices leave that language captions available without audio. Telugu, Bengali, and Marathi are not exposed by the studio because no local speech pipeline for them is installed.
- Scene-by-scene teacher editing, graphs/timelines/quizzes, and single-scene regeneration are not implemented yet.
- Stage timings are collected on the current machine, but it has not been verified as the weakest laptop in the judging setup.
- Tamil depends on an installed SAPI voice and has not passed a local runtime check here.
- Lesson accuracy checks catch schema, topic-overlap, language-script, duration, and code-output issues, but do not independently fact-check explanations.
- English uses Kokoro's configured voice; verify the voice's accent is suitable for the judging requirement before the demo.

