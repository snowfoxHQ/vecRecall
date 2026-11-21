"""仓库结构收集与增量更新。"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import time
from pathlib import Path

MAP_DIR = ".vecrecall/repo"
REQUIRED_FILES = (
    "PROFILE.md",
    "resources/commits.md",
    "raw/git-commits.json",
    "baseline.json",
)
_EXCLUDED_DIRS = {
    ".git", ".vecrecall", "__pycache__", "node_modules", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", "dist", "build",
}
_ENTRY_FILES = {
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml",
    "go.mod", "Makefile", "README.md", "README_EN.md", "main.py", "__main__.py",
}


def _run_git(repo: Path, args: list[str], timeout: float = 15.0) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", f"git_unavailable: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, proc.stdout, (proc.stderr or "git_error").strip()[:500]
    return True, proc.stdout.strip(), ""


def _first_docstring(path: Path) -> str:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
        doc = ast.get_docstring(tree) or ""
        if doc:
            return " ".join(doc.split())[:200]
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node) or ""
                if doc:
                    return " ".join(doc.split())[:200]
    except (OSError, SyntaxError, ValueError):
        pass
    return ""


def _scan(repo: Path, max_files: int = 400) -> dict:
    dirs: list[str] = []
    files: list[str] = []
    modules: list[dict] = []
    for root, subdirs, filenames in os.walk(repo):
        relative = Path(root).relative_to(repo)
        subdirs[:] = [name for name in subdirs
                      if name not in _EXCLUDED_DIRS and not name.startswith(".")]
        filenames[:] = [name for name in filenames
                        if name not in _EXCLUDED_DIRS and not name.startswith(".")]
        depth = 0 if relative == Path(".") else len(relative.parts)
        for name in filenames:
            rel = str(Path(relative) / name).replace("\\", "/").lstrip("./")
            files.append(rel)
            if name in _ENTRY_FILES and rel not in dirs:
                dirs.append(rel)
            if name.endswith(".py") and depth <= 3 and len(modules) < max_files:
                path = Path(root) / name
                module_rel = str(path.relative_to(repo)).replace("\\", "/")
                modules.append({"path": module_rel, "summary": _first_docstring(path)})
    dirs = sorted(set(dirs))
    files = sorted(set(files))
    return {"directories": dirs, "entry_points": dirs, "files": files, "modules": modules}


def _commit_lines(repo: Path, max_commits: int) -> tuple[list[dict], str]:
    ok, output, error = _run_git(repo, [
        "log", f"--max-count={max(1, int(max_commits))}",
        "--pretty=format:%H%x09%ad%x09%s", "--date=short",
    ])
    if not ok:
        return [], error or "git_unavailable"
    commits = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return commits, ""


def resolve_memory_root(path: str) -> Path:
    candidate = Path(path).resolve()
    return candidate if candidate.name == "repo" else candidate / MAP_DIR


def build_repo_map(
    repo_path: str,
    output_dir: str | None = None,
    *,
    max_commits: int = 50,
) -> dict:
    """收集仓库结构、入口、模块职责与 git 证据，写入可校验的知识包。"""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return {"ok": False, "error": f"not_a_directory: {repo}"}
    out = Path(output_dir).resolve() if output_dir else (repo / MAP_DIR)
    (out / "resources").mkdir(parents=True, exist_ok=True)
    (out / "raw").mkdir(parents=True, exist_ok=True)

    scan = _scan(repo)
    head_ok, head, head_error = _run_git(repo, ["rev-parse", "HEAD"])
    commits, commit_error = _commit_lines(repo, max_commits)
    git_available = head_ok
    status = "ok" if git_available else "unavailable"
    reason = "" if git_available else (head_error or commit_error or "git_unavailable")

    raw = {
        "status": status,
        "reason": reason,
        "head": head if head_ok else None,
        "commits": commits,
        "collected_at": time.time(),
    }
    (out / "raw" / "git-commits.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    profile = [
        "# Repository Profile",
        "",
        "## Entry Points",
        "",
    ]
    profile += [f"- `{item}`" for item in (scan["entry_points"] or ["(none detected)"])]
    profile += ["", "## Module Responsibilities", ""]
    for module in scan["modules"]:
        summary = module["summary"] or "(no docstring)"
        profile.append(f"- `{module['path']}`: {summary}")
    if not scan["modules"]:
        profile.append("(none detected)")
    profile += ["", "## Git Baseline", "", f"- status: {status}", f"- head: {head or '(unavailable)'}", ""]
    (out / "PROFILE.md").write_text("\n".join(profile), encoding="utf-8")

    commit_doc = ["# Commit Evidence", "", "## Commits", ""]
    if commits:
        commit_doc += [f"- {item['date']} `{item['sha'][:12]}` {item['subject']}" for item in commits]
    else:
        commit_doc.append(f"- ({status}: {reason or 'no commits'})")
    (out / "resources" / "commits.md").write_text("\n".join(commit_doc) + "\n", encoding="utf-8")

    baseline = {
        "schema_version": 1,
        "repo": str(repo),
        "head": head if head_ok else None,
        "git_status": status,
        "git_reason": reason,
        "created_at": time.time(),
        "file_count": len(scan["files"]),
        "module_count": len(scan["modules"]),
    }
    (out / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "ok": True,
        "repo": str(repo),
        "memory_path": str(out),
        "git_status": status,
        "git_reason": reason,
        "commit_count": len(commits),
        "module_count": len(scan["modules"]),
        "file_count": len(scan["files"]),
    }


def detect_updates(repo_path: str, memory_path: str | None = None) -> dict:
    """基于 baseline SHA 计算增量；不可用必须显式报告，不能伪装成空结果。"""
    repo = Path(repo_path).resolve()
    memory = resolve_memory_root(memory_path) if memory_path else (repo / MAP_DIR)
    baseline_path = memory / "baseline.json"
    if not baseline_path.is_file():
        return {"ok": False, "reason": "baseline_missing", "memory_path": str(memory)}

    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": "baseline_invalid", "error": str(exc)}

    head_ok, head, head_error = _run_git(repo, ["rev-parse", "HEAD"])
    if not head_ok:
        return {
            "ok": False,
            "reason": "git_unavailable",
            "error": head_error,
            "head": None,
            "note": "unavailable is not an empty delta; existing evidence is preserved",
        }

    base_sha = baseline.get("head")
    if not base_sha:
        return {
            "ok": True,
            "head": head,
            "baseline_head": None,
            "commit_delta_status": "unavailable_at_baseline",
            "new_commits": [],
        }

    delta_ok, output, delta_error = _run_git(repo, [
        "log", f"{base_sha}..{head}", "--pretty=format:%H%x09%ad%x09%s", "--date=short",
    ])
    if not delta_ok:
        return {"ok": False, "reason": "git_log_failed", "error": delta_error, "head": head}

    commits = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return {
        "ok": True,
        "head": head,
        "baseline_head": base_sha,
        "commit_delta_status": "ok",
        "new_commits": commits,
        "needs_update": bool(commits),
    }
