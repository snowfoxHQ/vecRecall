"""Repo-map 产物完整性校验。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from vecrecall.repomap.builder import REQUIRED_FILES, resolve_memory_root

_PLACEHOLDER = re.compile(r"\[([^\]\n]+)\]")


def _placeholders(text: str) -> list[str]:
    without_code = re.sub(r"```.*?```", "", text, flags=re.S)
    without_code = re.sub(r"`[^`\n]*`", "", without_code)
    found = []
    for match in _PLACEHOLDER.finditer(without_code):
        end = match.end()
        if re.match(r"^[ \t\r\n]*\(", without_code[end:]):
            continue
        value = match.group(1).strip()
        if value and not value.startswith("#") and re.search(r"[A-Za-z]", value):
            found.append(match.group(0))
    return found


def _section_count(text: str) -> int:
    lines = text.splitlines()
    title_seen = False
    count = 0
    for line in lines:
        if line.startswith("# ") and not title_seen:
            title_seen = True
            continue
        if title_seen and line.startswith("## "):
            count += 1
    return count


def validate_repo_map(path: str) -> dict:
    """返回 {ok, errors, warnings, checked}，不抛异常。"""
    memory = resolve_memory_root(path)
    errors: list[str] = []
    warnings: list[str] = []
    checked: list[str] = []

    if not memory.is_dir():
        return {
            "ok": False,
            "errors": [f"{memory}: repo map directory is missing"],
            "warnings": warnings,
            "checked": checked,
        }

    for rel in REQUIRED_FILES:
        checked.append(rel)
        target = memory / rel
        if not target.is_file():
            errors.append(f"{rel}: required file is missing")

    for rel in ("PROFILE.md", "resources/commits.md"):
        target = memory / rel
        if not target.is_file():
            continue
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{rel}: could not read: {exc}")
            continue
        for placeholder in _placeholders(text):
            errors.append(f"{rel}: unfilled placeholder {placeholder}")
        if _section_count(text) == 0:
            warnings.append(f"{rel}: no '## ' sections found")

    for rel in ("raw/git-commits.json", "baseline.json"):
        target = memory / rel
        if not target.is_file():
            continue
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{rel}: invalid JSON: {exc}")
            continue
        if rel == "baseline.json" and isinstance(payload, dict):
            if payload.get("schema_version") != 1:
                errors.append("baseline.json: unsupported schema_version")
        if rel == "raw/git-commits.json" and isinstance(payload, dict):
            if payload.get("status") not in ("ok", "unavailable"):
                errors.append("raw/git-commits.json: invalid git status")

    checked = sorted(set(checked))
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checked": checked,
        "memory_path": str(memory),
    }
