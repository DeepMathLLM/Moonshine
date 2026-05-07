"""Runtime dependency installation helpers for Moonshine init."""

from __future__ import annotations

import importlib.util
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence


@dataclass(frozen=True)
class RuntimeDependency:
    """A runtime library Moonshine can install or check."""

    package: str
    import_name: str
    purpose: str


REQUIRED_RUNTIME_DEPENDENCIES = (
    RuntimeDependency(
        package="tiktoken>=0.7.0",
        import_name="tiktoken",
        purpose="accurate token counting for context budgeting and compression",
    ),
    RuntimeDependency(
        package="lancedb>=0.14.0",
        import_name="lancedb",
        purpose="local vector index for structured knowledge memory",
    ),
    RuntimeDependency(
        package="chromadb>=0.5.0",
        import_name="chromadb",
        purpose="compatible vector backend fallback for semantic knowledge search",
    ),
    RuntimeDependency(
        package="langgraph>=0.2.0",
        import_name="langgraph",
        purpose="research workflow checkpointing and graph-style scheduling",
    ),
)


@dataclass
class DependencyInstallResult:
    """Result returned by the dependency installer."""

    command: List[str]
    exit_code: int
    output: str
    dependencies: List[RuntimeDependency]
    missing_before: List[RuntimeDependency]

    @property
    def success(self) -> bool:
        return self.exit_code == 0


def project_root() -> Path:
    """Return the package root that owns Moonshine's pyproject.toml."""
    return Path(__file__).resolve().parents[1]


def dependency_status(
    dependencies: Sequence[RuntimeDependency] = REQUIRED_RUNTIME_DEPENDENCIES,
) -> List[RuntimeDependency]:
    """Return dependencies whose import target is not currently available."""
    missing = []
    for dependency in dependencies:
        if importlib.util.find_spec(dependency.import_name) is None:
            missing.append(dependency)
    return missing


def build_dependency_install_command(extra: str = "all") -> List[str]:
    """Build the pip command used by `moonshine init --install-deps`."""
    target = str(project_root())
    if extra:
        target = "%s[%s]" % (target, extra)
    return [sys.executable, "-m", "pip", "install", "-e", target]


def format_dependency_install_command(extra: str = "all") -> str:
    """Return a shell-friendly display form of the install command."""
    return " ".join(shlex.quote(part) for part in build_dependency_install_command(extra=extra))


def install_runtime_dependencies(extra: str = "all") -> DependencyInstallResult:
    """Install Moonshine runtime dependencies from the local pyproject."""
    dependencies = list(REQUIRED_RUNTIME_DEPENDENCIES)
    missing_before = dependency_status(dependencies)
    command = build_dependency_install_command(extra=extra)
    if not missing_before:
        return DependencyInstallResult(
            command=command,
            exit_code=0,
            output="All required libraries are already importable.",
            dependencies=dependencies,
            missing_before=missing_before,
        )
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root().parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        return DependencyInstallResult(
            command=command,
            exit_code=completed.returncode,
            output=completed.stdout or "",
            dependencies=dependencies,
            missing_before=missing_before,
        )
    except OSError as exc:
        return DependencyInstallResult(
            command=command,
            exit_code=127,
            output=str(exc),
            dependencies=dependencies,
            missing_before=missing_before,
        )


def _format_dependency_list(dependencies: Sequence[RuntimeDependency]) -> List[str]:
    return ["- %s: %s" % (dependency.package, dependency.purpose) for dependency in dependencies]


def _tail_output(output: str, max_chars: int = 2400) -> str:
    normalized = output.strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[-max_chars:]


def render_dependency_check_report(
    dependencies: Sequence[RuntimeDependency] = REQUIRED_RUNTIME_DEPENDENCIES,
) -> str:
    """Render a human-readable dependency availability report."""
    dependencies = list(dependencies)
    missing = dependency_status(dependencies)
    if not missing:
        return "All Moonshine runtime dependencies are available."
    lines = ["Missing Moonshine runtime dependencies:"]
    lines.extend(_format_dependency_list(missing))
    lines.append("")
    lines.append("Install them with:")
    lines.append("  %s" % format_dependency_install_command(extra="all"))
    return "\n".join(lines)


def render_dependency_install_report(result: DependencyInstallResult) -> str:
    """Render installer output with actionable failure guidance."""
    command_text = " ".join(shlex.quote(part) for part in result.command)
    if result.success:
        lines = ["Runtime dependencies installed or verified successfully."]
        if result.missing_before:
            lines.append("Installed/verified libraries:")
            lines.extend(_format_dependency_list(result.missing_before))
        else:
            lines.append("All required libraries were already importable before installation.")
        return "\n".join(lines)

    lines = [
        "Dependency installation failed (exit code %s)." % result.exit_code,
        "Moonshine home was initialized, but these runtime libraries still need attention:",
    ]
    lines.extend(_format_dependency_list(result.dependencies))
    lines.extend(
        [
            "",
            "Command attempted:",
            "  %s" % command_text,
        ]
    )
    tail = _tail_output(result.output)
    if tail:
        lines.extend(["", "Installer output:", tail])
    lines.extend(
        [
            "",
            "After fixing network, Python, or pip environment issues, rerun:",
            "  %s" % format_dependency_install_command(extra="all"),
        ]
    )
    return "\n".join(lines)
