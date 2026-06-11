"""Bounded Python execution tools for project-local experiments."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from moonshine.tools.path_utils import strip_active_project_prefix
from moonshine.utils import trim_text_to_token_budget


DEFAULT_PYTHON_TIMEOUT_SECONDS = 30
MAX_PYTHON_TIMEOUT_SECONDS = 120
DEFAULT_PIP_TIMEOUT_SECONDS = 300
MAX_PIP_TIMEOUT_SECONDS = 900
OUTPUT_TOKEN_BUDGET = 6000
PACKAGE_SPEC_RE = re.compile(r"^[A-Za-z0-9_.\-\[\],<>=!~;:'\" ]+$")


def _coerce_args(args: object) -> List[str]:
    """Return command-line arguments as strings."""
    if args is None:
        return []
    if not isinstance(args, list):
        raise ValueError("args must be an array of strings")
    result = []
    for item in args:
        text = str(item)
        if "\x00" in text:
            raise ValueError("args cannot contain NUL bytes")
        result.append(text)
    return result


def _coerce_timeout(value: object, *, default_seconds: int = DEFAULT_PYTHON_TIMEOUT_SECONDS, max_seconds: int = MAX_PYTHON_TIMEOUT_SECONDS) -> int:
    """Clamp timeout to a small bounded execution window."""
    if value in (None, ""):
        return default_seconds
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer") from exc
    if seconds < 1:
        return 1
    return min(seconds, max_seconds)


def _resolve_project_script(runtime: dict, path_value: object) -> tuple[Path, Path, str]:
    """Resolve a Python script path under the active project root."""
    paths = runtime["paths"]
    project_slug = str(runtime.get("project_slug") or "").strip()
    if not project_slug:
        raise ValueError("run_python_script requires an active project")
    project_root = paths.project_dir(project_slug).resolve()
    raw_input = str(path_value or "").strip()
    if not raw_input:
        raise ValueError("path is required")
    raw_path = strip_active_project_prefix(paths, project_slug, project_root, raw_input)
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        target = (project_root / candidate).resolve()
    if target != project_root and project_root not in target.parents:
        raise ValueError("script path must stay inside the active project")
    if target.suffix.lower() != ".py":
        raise ValueError("run_python_script only runs .py files")
    if not target.exists():
        raise FileNotFoundError("script does not exist: %s" % target)
    if not target.is_file():
        raise ValueError("script path is not a file: %s" % target)
    return project_root, target, str(target.relative_to(project_root))


def _python_env() -> Dict[str, str]:
    """Build a conservative Python subprocess environment."""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONNOUSERSITE", "1")
    return env


def _resolve_project_root(runtime: dict) -> Path:
    """Return the active project root for Python tools."""
    paths = runtime["paths"]
    project_slug = str(runtime.get("project_slug") or "").strip()
    if not project_slug:
        raise ValueError("Python project tools require an active project")
    return paths.project_dir(project_slug).resolve()


def _coerce_package_specs(packages: object) -> List[str]:
    """Validate pip package requirement strings without allowing raw pip flags."""
    if not isinstance(packages, list) or not packages:
        raise ValueError("packages must be a non-empty array of package requirement strings")
    result = []
    for item in packages:
        spec = str(item or "").strip()
        lowered = spec.lower()
        if not spec:
            raise ValueError("package requirement strings cannot be empty")
        if "\x00" in spec or "\n" in spec or "\r" in spec:
            raise ValueError("package requirement strings cannot contain control characters")
        if spec.startswith("-"):
            raise ValueError("package requirement strings cannot be pip options")
        if "://" in spec or lowered.startswith(("git+", "file:", "http:", "https:")):
            raise ValueError("package requirement strings cannot be URLs or VCS/local path installs")
        if "/" in spec or "\\" in spec:
            raise ValueError("package requirement strings cannot be filesystem paths")
        if not PACKAGE_SPEC_RE.match(spec):
            raise ValueError("unsupported package requirement string: %s" % spec)
        result.append(spec)
    return result


def _in_virtualenv() -> bool:
    """Return True when the current interpreter appears to be inside a venv."""
    return bool(getattr(sys, "real_prefix", None)) or getattr(sys, "base_prefix", sys.prefix) != sys.prefix


def run_python_script(
    runtime: dict,
    path: str,
    args: object = None,
    timeout_seconds: object = None,
) -> dict:
    """Run a project-local Python script with bounded timeout and captured output."""
    project_root, script_path, relative_path = _resolve_project_script(runtime, path)
    argv = _coerce_args(args)
    timeout = _coerce_timeout(timeout_seconds)
    command = [sys.executable, str(script_path)] + argv
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env=_python_env(),
        )
        stdout = trim_text_to_token_budget(completed.stdout or "", OUTPUT_TOKEN_BUDGET)
        stderr = trim_text_to_token_budget(completed.stderr or "", OUTPUT_TOKEN_BUDGET)
        status = "ok" if completed.returncode == 0 else "error"
        return {
            "status": status,
            "path": str(script_path),
            "relative_path": relative_path,
            "root": str(project_root),
            "command": [Path(sys.executable).name, relative_path] + argv,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timeout_seconds": timeout,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = trim_text_to_token_budget(exc.stdout or "", OUTPUT_TOKEN_BUDGET)
        stderr = trim_text_to_token_budget(exc.stderr or "", OUTPUT_TOKEN_BUDGET)
        return {
            "status": "timeout",
            "path": str(script_path),
            "relative_path": relative_path,
            "root": str(project_root),
            "command": [Path(sys.executable).name, relative_path] + argv,
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "timeout_seconds": timeout,
            "error": "Python script timed out after %s second(s)" % timeout,
        }


def install_python_package(
    runtime: dict,
    packages: object,
    timeout_seconds: object = None,
    upgrade: bool = False,
) -> dict:
    """Install Python packages into the current Moonshine Python environment."""
    project_root = _resolve_project_root(runtime)
    package_specs = _coerce_package_specs(packages)
    timeout = _coerce_timeout(
        timeout_seconds,
        default_seconds=DEFAULT_PIP_TIMEOUT_SECONDS,
        max_seconds=MAX_PIP_TIMEOUT_SECONDS,
    )
    command = [sys.executable, "-m", "pip", "install"]
    if bool(upgrade):
        command.append("--upgrade")
    command.extend(package_specs)
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env=_python_env(),
        )
        stdout = trim_text_to_token_budget(completed.stdout or "", OUTPUT_TOKEN_BUDGET)
        stderr = trim_text_to_token_budget(completed.stderr or "", OUTPUT_TOKEN_BUDGET)
        return {
            "status": "ok" if completed.returncode == 0 else "error",
            "root": str(project_root),
            "python": sys.executable,
            "in_virtualenv": _in_virtualenv(),
            "packages": package_specs,
            "upgrade": bool(upgrade),
            "command": [Path(sys.executable).name, "-m", "pip", "install"] + (["--upgrade"] if bool(upgrade) else []) + package_specs,
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timeout_seconds": timeout,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = trim_text_to_token_budget(exc.stdout or "", OUTPUT_TOKEN_BUDGET)
        stderr = trim_text_to_token_budget(exc.stderr or "", OUTPUT_TOKEN_BUDGET)
        return {
            "status": "timeout",
            "root": str(project_root),
            "python": sys.executable,
            "in_virtualenv": _in_virtualenv(),
            "packages": package_specs,
            "upgrade": bool(upgrade),
            "command": [Path(sys.executable).name, "-m", "pip", "install"] + (["--upgrade"] if bool(upgrade) else []) + package_specs,
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "timeout_seconds": timeout,
            "error": "pip install timed out after %s second(s)" % timeout,
        }
