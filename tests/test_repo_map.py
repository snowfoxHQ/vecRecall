"""阶段3 回归：repo-map 构建、增量检测、产物校验。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vecrecall.repomap import build_repo_map, detect_updates, validate_repo_map  # noqa: E402
from vecrecall.repomap.builder import resolve_memory_root  # noqa: E402

PASS = 0
FAIL = 0


def test(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def git(repo: Path, *args: str) -> int:
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com",
    })
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.returncode


def test_build_and_validate() -> None:
    print("\n[build + validate]")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "proj"
        repo.mkdir()
        (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (repo / "main.py").write_text('"""Entry module."""\n\nprint("hi")\n', encoding="utf-8")
        (repo / "core").mkdir()
        (repo / "core" / "__init__.py").write_text("# core\n", encoding="utf-8")
        (repo / "core" / "engine.py").write_text('"""Core engine."""\n', encoding="utf-8")

        result = build_repo_map(str(repo))
        test("build succeeds without git", result["ok"] is True, str(result))
        test("git unavailable reported", result["git_status"] == "unavailable", str(result))
        test("modules collected", result["module_count"] >= 2, str(result))
        profile_text = (resolve_memory_root(str(repo)) / "PROFILE.md").read_text(encoding="utf-8")
        test("entry point collected", "README.md" in profile_text, profile_text[:200])

        report = validate_repo_map(str(repo))
        test("bundle valid", report["ok"] is True, str(report))
        test("required files checked", len(report["checked"]) == 4, str(report["checked"]))

        profile = resolve_memory_root(str(repo)) / "PROFILE.md"
        profile.write_text(profile.read_text(encoding="utf-8") + "\n- TODO: [FILL_ME]\n", encoding="utf-8")
        report = validate_repo_map(str(repo))
        test("placeholder detected", not report["ok"] and any("FILL_ME" in e for e in report["errors"]), str(report))

        (resolve_memory_root(str(repo)) / "baseline.json").unlink()
        report = validate_repo_map(str(repo))
        test("missing required file detected", not report["ok"] and any("baseline.json" in e for e in report["errors"]), str(report))


def test_incremental() -> None:
    print("\n[incremental]")
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "proj"
        repo.mkdir()
        if git(repo, "init", "-q") != 0:
            test("git available", False, "git init failed")
            return
        (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        if git(repo, "add", ".") != 0 or git(repo, "commit", "-m", "first") != 0:
            test("git available", False, "git commit failed")
            return

        result = build_repo_map(str(repo))
        test("git baseline captured", result["git_status"] == "ok" and result["commit_count"] == 1, str(result))

        result = detect_updates(str(repo))
        test("no delta after build", result["ok"] and result["new_commits"] == [], str(result))

        (repo / "README.md").write_text("# Demo v2\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "second")

        result = detect_updates(str(repo))
        test("one new commit detected", result["ok"] and len(result["new_commits"]) == 1, str(result))
        test("needs_update flagged", result.get("needs_update") is True, str(result))
        test("commit subject captured", result["new_commits"][0]["subject"] == "second", str(result))

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "plain"
        repo.mkdir()
        build_repo_map(str(repo))
        result = detect_updates(str(repo))
        test("git unavailable is explicit", not result["ok"] and result["reason"] == "git_unavailable", str(result))


def run_all() -> bool:
    test_build_and_validate()
    test_incremental()
    print(f"\n{PASS}/{PASS + FAIL} passed")
    return FAIL == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_all() else 1)
