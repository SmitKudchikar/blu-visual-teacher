"""Validate lesson JSON before it enters the rendering pipeline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def semantic_errors(lesson: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    scenes = lesson.get("scenes", [])
    if isinstance(scenes, list) and not 3 <= len(scenes) <= 6:
        errors.append(f"scenes: expected 3–6 scenes, got {len(scenes)}")
    languages = set(lesson.get("languages", []))
    for index, scene in enumerate(scenes):
        where = f"scenes[{index}] ({scene.get('id', 'unnamed')})"
        for field in ("narration", "captions"):
            missing = languages - set(scene.get(field, {}))
            if missing:
                errors.append(f"{where}: {field} missing {', '.join(sorted(missing))}")
        visual = scene.get("visual", {})
        if scene.get("type") == "diagram":
            node_ids = {node.get("id") for node in visual.get("nodes", [])}
            for arrow in visual.get("arrows", []):
                for endpoint in ("from", "to"):
                    if arrow.get(endpoint) not in node_ids:
                        errors.append(f"{where}: arrow references unknown node {arrow.get(endpoint)!r}")
        if scene.get("type") == "code-walkthrough":
            count = len(visual.get("code", []))
            if any(line > count for line in visual.get("highlightLines", [])):
                errors.append(f"{where}: a highlighted line exceeds the code length ({count})")
            heading = visual.get("heading", {})
            if not isinstance(heading, dict) or set(heading) != languages:
                errors.append(f"{where}: visual.heading must include every lesson language")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Blu Visual Teacher lesson")
    parser.add_argument("lesson", nargs="?", type=Path, default=ROOT / "data" / "lessons" / "sample-lesson.json")
    parser.add_argument("--schema", type=Path, default=ROOT / "schemas" / "lesson.schema.json")
    args = parser.parse_args()
    try:
        lesson = read_json(args.lesson)
        schema = read_json(args.schema)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    problems = [
        f"{'.'.join(map(str, err.absolute_path)) or '<root>'}: {err.message}"
        for err in Draft202012Validator(schema).iter_errors(lesson)
    ]
    if isinstance(lesson, dict):
        problems.extend(semantic_errors(lesson))
    else:
        problems.append("<root>: lesson must be a JSON object")
    if problems:
        print("Lesson validation failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"Lesson is valid: {lesson['id']} ({len(lesson['scenes'])} scenes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
