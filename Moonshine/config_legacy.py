"""Compatibility exports for Moonshine configuration."""

from moonshine.moonshine_cli.config import (
    AppConfig,
    MemoryConfig,
    ProviderConfig,
    ensure_project_layout,
    ensure_runtime_home,
    load_config,
    resolve_home,
    save_config,
)
from moonshine.moonshine_constants import MoonshinePaths

__all__ = [
    "AppConfig",
    "MemoryConfig",
    "MoonshinePaths",
    "ProviderConfig",
    "ensure_project_layout",
    "ensure_runtime_home",
    "load_config",
    "resolve_home",
    "save_config",
]

"""


@dataclass
class ProviderConfig:
    """Provider configuration."""

    type: str = "offline"
    model: str = "moonshine-basic"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    timeout_seconds: int = 60
    temperature: float = 0.2


@dataclass
class MemoryConfig:
    """Memory system configuration."""

    auto_extract: bool = True
    max_index_lines: int = 200
    session_search_limit: int = 5
    knowledge_search_limit: int = 5
    review_stale_days: int = 14


@dataclass
class AppConfig:
    """Application configuration."""

    default_mode: str = "chat"
    default_project: str = "general"
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """Build AppConfig from a raw dictionary."""
        provider_data = dict(data.get("provider", {}))
        memory_data = dict(data.get("memory", {}))
        return cls(
            default_mode=data.get("default_mode", "chat"),
            default_project=data.get("default_project", "general"),
            provider=ProviderConfig(**provider_data),
            memory=MemoryConfig(**memory_data),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return asdict(self)


@dataclass
class MoonshinePaths:
    """Runtime directory layout."""

    home: Path

    @property
    def config_file(self) -> Path:
        return self.home / "config.json"

    @property
    def claude_file(self) -> Path:
        return self.home / "CLAUDE.md"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def memory_index_file(self) -> Path:
        return self.memory_dir / "MEMORY.md"

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"

    @property
    def databases_dir(self) -> Path:
        return self.home / "databases"

    @property
    def sessions_db(self) -> Path:
        return self.databases_dir / "sessions.sqlite3"

    @property
    def knowledge_db(self) -> Path:
        return self.databases_dir / "knowledge.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    def project_rules_file(self, project_slug: str) -> Path:
        return self.projects_dir / project_slug / "rules.md"


def resolve_home(home: Optional[str] = None) -> Path:
    """Resolve runtime home directory."""
    if home:
        return Path(home).expanduser().resolve()
    return (Path.cwd() / ".moonshine").resolve()


def ensure_runtime_home(paths: MoonshinePaths, default_project: str = "general") -> None:
    """Create the runtime home and all default files."""
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.memory_dir.mkdir(parents=True, exist_ok=True)
    paths.projects_dir.mkdir(parents=True, exist_ok=True)
    paths.databases_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    if not paths.config_file.exists():
        atomic_write(paths.config_file, json.dumps(default_config(), indent=2, ensure_ascii=False))

    if not paths.claude_file.exists():
        atomic_write(paths.claude_file, DEFAULT_CLAUDE_MD.strip() + "\n")

    if not paths.memory_index_file.exists():
        atomic_write(paths.memory_index_file, "# MEMORY.md — Moonshine Memory Index\n\n")

    for spec in general_specs():
        file_path = paths.memory_dir / spec["relative_path"]
        if not file_path.exists():
            atomic_write(file_path, spec["header"].strip() + "\n\n")

    ensure_project_layout(paths, default_project)


def ensure_project_layout(paths: MoonshinePaths, project_slug: str) -> None:
    """Ensure project-specific files exist."""
    project_dir = paths.projects_dir / project_slug
    project_dir.mkdir(parents=True, exist_ok=True)
    rules_path = paths.project_rules_file(project_slug)
    if not rules_path.exists():
        atomic_write(rules_path, DEFAULT_PROJECT_RULES_TEMPLATE.format(project_slug=project_slug).strip() + "\n")


def load_config(paths: MoonshinePaths) -> AppConfig:
    """Load application configuration."""
    raw = json.loads(paths.config_file.read_text(encoding="utf-8"))
    return AppConfig.from_dict(raw)


def save_config(paths: MoonshinePaths, config: AppConfig) -> None:
    """Save application configuration."""
    atomic_write(paths.config_file, json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
"""
