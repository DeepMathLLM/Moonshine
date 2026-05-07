"""Markdown-backed MCP server descriptors and stdio transport helpers."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
from datetime import datetime
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from moonshine.credentials import get_credential
from moonshine.markdown_metadata import load_markdown_metadata
from moonshine.moonshine_constants import MoonshinePaths
from moonshine.tools.path_utils import strip_active_project_prefix
from moonshine.utils import read_text
from moonshine.utils import shorten


MCP_PROTOCOL_VERSION = "2024-11-05"
SAFE_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "TERM",
    "SHELL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
})
CREDENTIAL_PATTERN = re.compile(
    r"(?:ghp_[A-Za-z0-9_]{1,255}|sk-[A-Za-z0-9_]{1,255}|Bearer\s+\S+|token=[^\s&,;\"']+|key=[^\s&,;\"']+|password=[^\s&,;\"']+|secret=[^\s&,;\"']+)",
    re.IGNORECASE,
)
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"you\s+are\s+now\s+a", re.I),
    re.compile(r"system\s*:\s*", re.I),
    re.compile(r"<\s*(system|human|assistant)\s*>", re.I),
    re.compile(r"do\s+not\s+(tell|inform|mention|reveal)", re.I),
]


class MCPTransportError(RuntimeError):
    """Raised when an MCP transport operation fails."""


def sanitize_mcp_name_component(value: object) -> str:
    """Return a provider-safe MCP name component."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "tool"


def prefixed_mcp_tool_name(server_slug: str, tool_name: str) -> str:
    """Return a Hermes-style provider-facing MCP tool name."""
    return "mcp_%s_%s" % (
        sanitize_mcp_name_component(server_slug),
        sanitize_mcp_name_component(tool_name),
    )


def _sanitize_error(text: object) -> str:
    """Strip credential-like patterns before returning errors to the model."""
    return CREDENTIAL_PATTERN.sub("[REDACTED]", str(text))


def _build_safe_env(user_env: Optional[Dict[str, str]], defaults: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Build a filtered subprocess environment inspired by Hermes."""
    env: Dict[str, str] = {}
    for key, value in os.environ.items():
        if key in SAFE_ENV_KEYS or key.startswith("XDG_"):
            env[key] = value
    for key, value in dict(defaults or {}).items():
        env.setdefault(str(key), str(value))
    for key, value in dict(user_env or {}).items():
        env[str(key)] = _interpolate_env_value(str(value), env)
    return env


def _interpolate_env_value(value: str, env: Optional[Dict[str, str]] = None) -> str:
    """Resolve ${ENV_VAR} placeholders in descriptor env values."""
    lookup = dict(os.environ)
    lookup.update(dict(env or {}))

    def _replace(match):
        return lookup.get(match.group(1), "")

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", _replace, value)


def _description_warnings(description: str) -> List[str]:
    """Return warning labels for suspicious MCP tool descriptions."""
    warnings = []
    for pattern in INJECTION_PATTERNS:
        if pattern.search(description or ""):
            warnings.append("possible prompt-injection text: %s" % pattern.pattern)
    return warnings


@dataclass
class MCPServerDefinition:
    """Markdown-backed MCP server descriptor."""

    slug: str
    title: str
    description: str
    path: str
    source: str
    body: str = ""
    transport: str = "stdio"
    enabled: bool = False
    command: str = ""
    args: List[str] = field(default_factory=list)
    cwd: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    tool_hints: List[Dict[str, object]] = field(default_factory=list)
    discover_tools: bool = True
    timeout_seconds: float = 30.0

    def to_display_item(self) -> Dict[str, object]:
        """Return a display-friendly representation."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "path": self.path,
            "source": self.source,
            "transport": self.transport,
            "enabled": self.enabled,
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "discover_tools": self.discover_tools,
            "timeout_seconds": self.timeout_seconds,
            "tool_hints": list(self.tool_hints),
        }


class MCPStdioClient(object):
    """Minimal MCP stdio client using JSON-RPC content-length framing."""

    def __init__(self, server: MCPServerDefinition, default_env: Optional[Dict[str, str]] = None):
        self.server = server
        self.default_env = {str(key): str(value) for key, value in dict(default_env or {}).items()}
        self.process: Optional[subprocess.Popen] = None
        self._next_id = 1
        self._stderr_lines: List[str] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread: Optional[threading.Thread] = None

    def __enter__(self) -> "MCPStdioClient":
        self.start()
        try:
            self.initialize()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _resolve_cwd(self) -> str:
        """Resolve descriptor cwd relative to the descriptor file."""
        if not self.server.cwd:
            return str(Path(self.server.path).resolve().parent)
        candidate = Path(self.server.cwd).expanduser()
        if not candidate.is_absolute():
            candidate = Path(self.server.path).resolve().parent / candidate
        return str(candidate.resolve())

    def _default_env(self) -> Dict[str, str]:
        """Build Moonshine-specific defaults for descriptor interpolation."""
        descriptor_path = Path(self.server.path).resolve()
        try:
            # Runtime descriptors live at <home>/tools/mcp/servers/<name>.md.
            home = descriptor_path.parents[3]
        except IndexError:
            home = descriptor_path.parent
        sessions_dir = home / "sessions"
        defaults = {
            "MOONSHINE_HOME": str(home),
            "MOONSHINE_SESSIONS_DIR": str(sessions_dir),
        }
        defaults.update(self.default_env)
        filesystem_root = (
            os.environ.get("MOONSHINE_MCP_FILESYSTEM_ROOT")
            or defaults.get("MOONSHINE_MCP_FILESYSTEM_ROOT")
            or str(sessions_dir)
        )
        defaults["MOONSHINE_MCP_FILESYSTEM_ROOT"] = filesystem_root
        return defaults

    def start(self) -> None:
        """Start the MCP server process."""
        if self.server.transport.lower() != "stdio":
            raise MCPTransportError("unsupported MCP transport for %s: %s" % (self.server.slug, self.server.transport))
        if not self.server.command:
            raise MCPTransportError("MCP server '%s' does not define a command" % self.server.slug)
        env = _build_safe_env(self.server.env, defaults=self._default_env())
        command_name = os.path.expanduser(str(self.server.command).strip())
        resolved_command = shutil.which(command_name, path=env.get("PATH")) or command_name
        command = [resolved_command] + [_interpolate_env_value(str(item), env) for item in list(self.server.args or [])]
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self._resolve_cwd(),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise MCPTransportError("failed to start MCP server '%s': %s" % (self.server.slug, _sanitize_error(exc))) from exc
        if self.process.stderr is not None:
            self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()

    def close(self) -> None:
        """Terminate the MCP server process."""
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            if process.stdout:
                process.stdout.close()
        except OSError:
            pass
        try:
            if process.stderr:
                process.stderr.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)

    def _drain_stderr(self) -> None:
        """Capture stderr for transport diagnostics without leaking secrets."""
        process = self.process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                chunk = process.stderr.readline()
                if not chunk:
                    break
                line = _sanitize_error(chunk.decode("utf-8", errors="replace").strip())
                if not line:
                    continue
                with self._stderr_lock:
                    self._stderr_lines.append(line)
                    if len(self._stderr_lines) > 12:
                        self._stderr_lines = self._stderr_lines[-12:]
        except OSError:
            return

    def _stderr_tail(self, max_chars: int = 600) -> str:
        """Return the recent stderr tail for richer MCP diagnostics."""
        with self._stderr_lock:
            text = "\n".join(self._stderr_lines[-12:]).strip()
        return shorten(text, max_chars) if text else ""

    def _transport_context(self, process: Optional[subprocess.Popen] = None) -> str:
        """Return a short transport context string for MCP failures."""
        parts: List[str] = []
        current = process or self.process
        if current is not None and current.poll() is not None:
            parts.append("exit code %s" % current.returncode)
        stderr_tail = self._stderr_tail()
        if stderr_tail:
            parts.append("stderr tail: %s" % stderr_tail)
        if not parts:
            return ""
        return " (%s)" % "; ".join(parts)

    def _require_process(self) -> subprocess.Popen:
        """Return the live process or raise a transport error."""
        if self.process is None:
            raise MCPTransportError("MCP server '%s' is not running" % self.server.slug)
        if self.process.poll() is not None:
            raise MCPTransportError(
                "MCP server '%s' exited with code %s%s"
                % (self.server.slug, self.process.returncode, self._transport_context(self.process))
            )
        if self.process.stdin is None or self.process.stdout is None:
            raise MCPTransportError("MCP server '%s' is missing stdio pipes" % self.server.slug)
        return self.process

    def _send_message(self, payload: Dict[str, object]) -> None:
        """Send one framed JSON-RPC message."""
        process = self._require_process()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        header = ("Content-Length: %s\r\n\r\n" % len(body)).encode("ascii")
        try:
            process.stdin.write(header + body)
            process.stdin.flush()
        except OSError as exc:
            raise MCPTransportError("failed writing to MCP server '%s': %s" % (self.server.slug, _sanitize_error(exc))) from exc

    def _read_message_blocking(self) -> Dict[str, object]:
        """Read one framed JSON-RPC message from stdout."""
        process = self._require_process()
        headers: Dict[str, str] = {}
        while True:
            line = process.stdout.readline()
            if not line:
                raise MCPTransportError(
                    "MCP server '%s' closed stdout%s" % (self.server.slug, self._transport_context(process))
                )
            if line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("ascii", errors="replace").strip()
            if ":" not in decoded:
                continue
            key, value = decoded.split(":", 1)
            headers[key.lower()] = value.strip()
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError as exc:
            raise MCPTransportError("invalid MCP content-length from '%s'" % self.server.slug) from exc
        if length <= 0:
            raise MCPTransportError("missing MCP content from '%s'" % self.server.slug)
        body = process.stdout.read(length)
        if len(body) != length:
            raise MCPTransportError("short MCP response from '%s'%s" % (self.server.slug, self._transport_context(process)))
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise MCPTransportError("invalid JSON-RPC payload from '%s'" % self.server.slug) from exc
        if not isinstance(payload, dict):
            raise MCPTransportError("MCP payload from '%s' must be an object" % self.server.slug)
        return payload

    def _read_message(self) -> Dict[str, object]:
        """Read one message with a timeout."""
        messages: "queue.Queue[Tuple[str, object]]" = queue.Queue(maxsize=1)

        def _target() -> None:
            try:
                messages.put(("ok", self._read_message_blocking()))
            except Exception as exc:
                messages.put(("error", exc))

        thread = threading.Thread(target=_target, daemon=True)
        thread.start()
        try:
            status, payload = messages.get(timeout=max(0.1, float(self.server.timeout_seconds)))
        except queue.Empty as exc:
            self.close()
            raise MCPTransportError(
                "timed out waiting for MCP server '%s' after %.1fs" % (self.server.slug, self.server.timeout_seconds)
            ) from exc
        if status == "error":
            if isinstance(payload, Exception):
                raise payload
            raise MCPTransportError(str(payload))
        return dict(payload)

    def _respond_method_not_found(self, request_id: object, method: str) -> None:
        """Reply to unexpected server-to-client JSON-RPC requests."""
        self._send_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "Moonshine MCP client does not implement server request '%s'." % method,
                },
            }
        )

    def request(self, method: str, params: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        """Send a JSON-RPC request and return its result object."""
        request_id = self._next_id
        self._next_id += 1
        self._send_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params or {}),
            }
        )
        while True:
            payload = self._read_message()
            if "id" in payload and payload.get("id") == request_id:
                if payload.get("error"):
                    raise MCPTransportError(
                        "MCP server '%s' returned error for %s: %s"
                        % (self.server.slug, method, _sanitize_error(payload.get("error")))
                    )
                result = payload.get("result", {})
                if isinstance(result, dict):
                    return result
                return {"value": result}
            if "method" in payload and "id" in payload:
                self._respond_method_not_found(payload.get("id"), str(payload.get("method") or ""))

    def notify(self, method: str, params: Optional[Dict[str, object]] = None) -> None:
        """Send a JSON-RPC notification."""
        self._send_message(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": dict(params or {}),
            }
        )

    def initialize(self) -> Dict[str, object]:
        """Initialize one MCP session."""
        result = self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "moonshine",
                    "version": "0.1.0",
                },
            },
        )
        self.notify("notifications/initialized", {})
        return result

    def list_tools(self) -> List[Dict[str, object]]:
        """Return the remote server's tool descriptors."""
        result = self.request("tools/list", {})
        tools = result.get("tools", [])
        if not isinstance(tools, list):
            raise MCPTransportError("MCP tools/list from '%s' returned invalid tools payload" % self.server.slug)
        return [dict(item) for item in tools if isinstance(item, dict)]

    def call_tool(self, tool_name: str, arguments: Dict[str, object]) -> Dict[str, object]:
        """Call one remote MCP tool."""
        return self.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": dict(arguments or {}),
            },
        )


def _timeout_from_metadata(metadata: Dict[str, object]) -> float:
    """Resolve a descriptor timeout from common MCP config spellings."""
    for key in ["timeout_seconds", "tool_timeout_sec", "tool_timeout_seconds"]:
        if key in metadata:
            try:
                return max(0.1, float(metadata[key]))
            except (TypeError, ValueError):
                return 30.0
    return 30.0


class MCPServerRegistry(object):
    """Load MCP server descriptors and call enabled MCP tools."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths

    def _default_env(self, runtime: Optional[Dict[str, object]] = None) -> Dict[str, str]:
        """Return Moonshine runtime defaults for MCP descriptor interpolation."""
        runtime = dict(runtime or {})
        session_id = str(runtime.get("session_id") or "").strip()
        project_slug = str(runtime.get("project_slug") or "").strip()
        session_dir = self.paths.session_dir(session_id) if session_id else self.paths.sessions_dir
        project_dir = self.paths.project_dir(project_slug) if project_slug else None
        default_filesystem_root = project_dir if project_dir is not None else session_dir
        defaults = {
            "MOONSHINE_HOME": str(self.paths.home),
            "MOONSHINE_SESSIONS_DIR": str(self.paths.sessions_dir),
            "MOONSHINE_CURRENT_SESSION_DIR": str(session_dir) if session_id else "",
            "MOONSHINE_MCP_FILESYSTEM_ROOT": str(default_filesystem_root),
        }
        if project_slug:
            defaults["MOONSHINE_PROJECT_DIR"] = str(project_dir)
        tavily_key = get_credential(self.paths, "TAVILY_API_KEY")
        if tavily_key:
            defaults["TAVILY_API_KEY"] = tavily_key
        if os.environ.get("MOONSHINE_MCP_FILESYSTEM_ROOT"):
            defaults["MOONSHINE_MCP_FILESYSTEM_ROOT"] = os.environ["MOONSHINE_MCP_FILESYSTEM_ROOT"]
        return defaults

    def _scan_directory(self, base_dir: Path, source: str) -> List[MCPServerDefinition]:
        """Scan a directory for markdown MCP server descriptors."""
        definitions: List[MCPServerDefinition] = []
        if not base_dir.exists():
            return definitions

        for descriptor in sorted(base_dir.rglob("*.md")):
            metadata, body = load_markdown_metadata(descriptor)
            slug = str(metadata.get("slug") or descriptor.stem).strip()
            if not slug:
                continue
            title = str(metadata.get("title") or slug.replace("-", " ").title()).strip()
            description = str(metadata.get("description") or "").strip()
            definitions.append(
                MCPServerDefinition(
                    slug=slug,
                    title=title,
                    description=description,
                    path=str(descriptor),
                    source=source,
                    body=body,
                    transport=str(metadata.get("transport") or "stdio").strip(),
                    enabled=bool(metadata.get("enabled", False)),
                    command=str(metadata.get("command") or "").strip(),
                    args=[str(item) for item in list(metadata.get("args") or [])],
                    cwd=str(metadata.get("cwd") or "").strip(),
                    env={str(key): str(value) for key, value in dict(metadata.get("env") or {}).items()},
                    tool_hints=[dict(item) for item in list(metadata.get("tool_hints") or []) if isinstance(item, dict)],
                    discover_tools=bool(metadata.get("discover_tools", True)),
                    timeout_seconds=_timeout_from_metadata(metadata),
                )
            )
        return definitions

    def list_servers(self) -> List[MCPServerDefinition]:
        """Return configured MCP server definitions."""
        packaged = self._scan_directory(self.paths.mcp_servers_dir, "runtime")
        by_slug = {item.slug: item for item in packaged}
        return [by_slug[key] for key in sorted(by_slug)]

    def get_server(self, slug: str) -> Optional[MCPServerDefinition]:
        """Return a server descriptor by slug."""
        for item in self.list_servers():
            if item.slug == slug:
                return item
        return None

    def build_prompt_index(self, limit: int = 6) -> str:
        """Build a compact MCP availability summary."""
        enabled = [item for item in self.list_servers() if item.enabled]
        if not enabled:
            return ""
        lines = ["Enabled MCP server descriptors:"]
        for item in enabled[:limit]:
            lines.append("- %s: %s" % (item.slug, item.description or item.title))
        if len(enabled) > limit:
            lines.append("- ... plus %s more MCP server definitions" % (len(enabled) - limit))
        lines.append("Inspect a descriptor with load_mcp_server_definition when external integration details are needed.")
        return "\n".join(lines)

    def _normalize_live_tool(self, server: MCPServerDefinition, item: Dict[str, object]) -> Dict[str, object]:
        """Normalize an MCP tools/list descriptor into a Moonshine tool hint."""
        remote_name = str(item.get("name") or "").strip()
        name = prefixed_mcp_tool_name(server.slug, remote_name)
        description = str(item.get("description") or "MCP tool from %s." % server.slug)
        input_schema = item.get("inputSchema") or item.get("parameters") or {}
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        return {
            "name": name,
            "remote_name": remote_name,
            "description": description,
            "parameters": dict(input_schema),
            "body": "Live MCP tool discovered from server '%s'." % server.slug,
            "source_path": server.path,
            "security_warnings": _description_warnings(description),
        }

    def list_server_tools(self, server_slug: str) -> List[Dict[str, object]]:
        """Discover tools from one enabled MCP server via tools/list."""
        server = self.get_server(server_slug)
        if server is None:
            raise KeyError("MCP server not found: %s" % server_slug)
        if not server.enabled:
            raise PermissionError("MCP server is disabled: %s" % server_slug)
        with MCPStdioClient(server, default_env=self._default_env()) as client:
            return [self._normalize_live_tool(server, item) for item in client.list_tools()]

    def export_enabled_tool_hints(self) -> List[Dict[str, object]]:
        """Return descriptor or live tool definitions from enabled servers."""
        hints: List[Dict[str, object]] = []
        for server in self.list_servers():
            if not server.enabled:
                continue
            for item in server.tool_hints:
                record = dict(item)
                remote_name = str(record.get("remote_name") or record.get("mcp_name") or record.get("name", "")).strip()
                record["name"] = prefixed_mcp_tool_name(server.slug, remote_name)
                record["server_slug"] = server.slug
                record["remote_name"] = remote_name
                record.setdefault("source_path", server.path)
                record.setdefault("security_warnings", _description_warnings(str(record.get("description") or "")))
                hints.append(record)
            if not server.discover_tools or (server.tool_hints and not server.discover_tools):
                continue
            try:
                live_hints = self.list_server_tools(server.slug)
            except Exception:
                continue
            known_remote_names = {str(item.get("remote_name", "")) for item in hints if item.get("server_slug") == server.slug}
            for item in live_hints:
                remote_name = str(item.get("remote_name") or "").strip()
                if not remote_name or remote_name in known_remote_names:
                    continue
                item["server_slug"] = server.slug
                item.setdefault("remote_name", remote_name)
                item.setdefault("source_path", server.path)
                hints.append(item)
        return hints

    def _extract_text_content(self, content: object) -> str:
        """Extract a readable text summary from MCP content blocks."""
        if not isinstance(content, list):
            return ""
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif "text" in item:
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)

    def _result_from_payload(
        self,
        *,
        server_slug: str,
        tool_name: str,
        content_text: str,
        structured_content: Optional[Dict[str, object]] = None,
        status: str = "ok",
        raw_result: Optional[Dict[str, object]] = None,
        fallback: str = "",
    ) -> Dict[str, object]:
        """Build one normalized MCP-style tool result."""
        result = dict(raw_result or {})
        if fallback:
            result["fallback"] = fallback
        return {
            "status": status,
            "server_slug": server_slug,
            "tool_name": tool_name,
            "content": content_text,
            "content_preview": shorten(content_text, 500),
            "structured_content": structured_content,
            "mcp_result": result,
        }

    def _resolve_filesystem_root(self, runtime: Optional[Dict[str, object]]) -> Path:
        """Resolve the allowed filesystem MCP root for local fallbacks."""
        defaults = self._default_env(runtime)
        return Path(defaults.get("MOONSHINE_MCP_FILESYSTEM_ROOT") or self.paths.sessions_dir).expanduser().resolve()

    def _resolve_filesystem_target(self, root: Path, path_value: object, runtime: Optional[Dict[str, object]] = None) -> Path:
        """Resolve one relative or rooted filesystem path under the allowed root."""
        runtime = dict(runtime or {})
        raw = strip_active_project_prefix(self.paths, str(runtime.get("project_slug") or ""), root, path_value)
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            target = candidate.resolve()
        else:
            target = (root / candidate).resolve()
        if target != root and root not in target.parents:
            raise ValueError("path must stay inside the configured filesystem root")
        return target

    def _filesystem_local_tool(
        self,
        server: MCPServerDefinition,
        tool_name: str,
        arguments: Dict[str, object],
        runtime: Optional[Dict[str, object]],
    ) -> Dict[str, object]:
        """Local implementation for filesystem MCP-namespaced tools."""
        try:
            root = self._resolve_filesystem_root(runtime)
            target = self._resolve_filesystem_target(root, arguments.get("path"), runtime)
            if tool_name == "read_file":
                content = read_text(target)
                structured = {"path": str(target), "root": str(root), "content": content}
                return self._result_from_payload(
                    server_slug=server.slug,
                    tool_name=tool_name,
                    content_text=content,
                    structured_content=structured,
                    raw_result={"content": [{"type": "text", "text": content}], "structuredContent": structured, "isError": False},
                    fallback="local-filesystem",
                )
            if tool_name == "write_file":
                target.parent.mkdir(parents=True, exist_ok=True)
                text = str(arguments.get("content") or "")
                target.write_text(text, encoding="utf-8")
                message = "Wrote %s" % target
                structured = {"path": str(target), "root": str(root), "bytes": len(text.encode("utf-8"))}
                return self._result_from_payload(
                    server_slug=server.slug,
                    tool_name=tool_name,
                    content_text=message,
                    structured_content=structured,
                    raw_result={"content": [{"type": "text", "text": message}], "structuredContent": structured, "isError": False},
                    fallback="local-filesystem",
                )
            if tool_name == "create_directory":
                target.mkdir(parents=True, exist_ok=True)
                message = "Created directory %s" % target
                structured = {"path": str(target), "root": str(root)}
                return self._result_from_payload(
                    server_slug=server.slug,
                    tool_name=tool_name,
                    content_text=message,
                    structured_content=structured,
                    raw_result={"content": [{"type": "text", "text": message}], "structuredContent": structured, "isError": False},
                    fallback="local-filesystem",
                )
            if tool_name == "list_directory":
                if not target.exists():
                    raise FileNotFoundError("directory does not exist: %s" % target)
                if not target.is_dir():
                    raise NotADirectoryError("%s is not a directory" % target)
                entries = []
                lines = []
                for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                    entry_type = "directory" if child.is_dir() else "file"
                    display = "%s/" % child.name if child.is_dir() else child.name
                    lines.append(display)
                    entries.append(
                        {
                            "name": child.name,
                            "type": entry_type,
                            "path": str(child),
                            "relative_path": str(child.relative_to(root)),
                        }
                    )
                content = "\n".join(lines)
                structured = {"path": str(target), "root": str(root), "entries": entries}
                return self._result_from_payload(
                    server_slug=server.slug,
                    tool_name=tool_name,
                    content_text=content,
                    structured_content=structured,
                    raw_result={"content": [{"type": "text", "text": content}], "structuredContent": structured, "isError": False},
                    fallback="local-filesystem",
                )
            if tool_name == "search_files":
                if not target.exists():
                    raise FileNotFoundError("search root does not exist: %s" % target)
                pattern = str(arguments.get("pattern") or "").strip()
                if not pattern:
                    raise ValueError("search_files requires a non-empty pattern")
                matches = []
                lines = []
                lowered_pattern = pattern.lower()
                use_glob = any(marker in pattern for marker in ["*", "?"])
                for child in target.rglob("*"):
                    relative_path = str(child.relative_to(root))
                    haystack = relative_path.lower()
                    if use_glob:
                        matched = fnmatch(relative_path, pattern) or fnmatch(child.name, pattern)
                    else:
                        matched = lowered_pattern in haystack or lowered_pattern in child.name.lower()
                    if not matched:
                        continue
                    lines.append(relative_path)
                    matches.append(
                        {
                            "name": child.name,
                            "path": str(child),
                            "relative_path": relative_path,
                            "type": "directory" if child.is_dir() else "file",
                        }
                    )
                content = "\n".join(lines)
                structured = {"path": str(target), "root": str(root), "pattern": pattern, "matches": matches}
                return self._result_from_payload(
                    server_slug=server.slug,
                    tool_name=tool_name,
                    content_text=content,
                    structured_content=structured,
                    raw_result={"content": [{"type": "text", "text": content}], "structuredContent": structured, "isError": False},
                    fallback="local-filesystem",
                )
            if tool_name == "get_file_info":
                if not target.exists():
                    raise FileNotFoundError("path does not exist: %s" % target)
                stat = target.stat()
                structured = {
                    "path": str(target),
                    "root": str(root),
                    "type": "directory" if target.is_dir() else "file",
                    "size": int(stat.st_size),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                }
                content = json.dumps(structured, ensure_ascii=False, indent=2)
                return self._result_from_payload(
                    server_slug=server.slug,
                    tool_name=tool_name,
                    content_text=content,
                    structured_content=structured,
                    raw_result={"content": [{"type": "text", "text": content}], "structuredContent": structured, "isError": False},
                    fallback="local-filesystem",
                )
            raise KeyError("unsupported filesystem MCP fallback tool: %s" % tool_name)
        except Exception as exc:
            message = "Local filesystem MCP tool failed: %s" % _sanitize_error(exc)
            return self._result_from_payload(
                server_slug=server.slug,
                tool_name=tool_name,
                content_text=message,
                structured_content={"error": message},
                status="error",
                raw_result={"content": [{"type": "text", "text": message}], "structuredContent": {"error": message}, "isError": True},
                fallback="local-filesystem",
            )

    def _tavily_request(
        self,
        server: MCPServerDefinition,
        endpoint: str,
        payload: Dict[str, object],
        runtime: Optional[Dict[str, object]],
    ) -> Dict[str, object]:
        """Call the Tavily HTTP API directly as an MCP fallback."""
        api_key = self._default_env(runtime).get("TAVILY_API_KEY") or get_credential(self.paths, "TAVILY_API_KEY")
        if not api_key:
            raise MCPTransportError("Tavily fallback requires TAVILY_API_KEY to be configured")
        request = Request(
            "https://api.tavily.com/%s" % endpoint.lstrip("/"),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % api_key,
            },
            method="POST",
        )
        with urlopen(request, timeout=max(1.0, float(server.timeout_seconds))) as response:
            raw = response.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise MCPTransportError("invalid JSON returned by Tavily fallback") from exc
        if not isinstance(data, dict):
            raise MCPTransportError("unexpected Tavily fallback response shape")
        return data

    def _tavily_fallback(
        self,
        server: MCPServerDefinition,
        tool_name: str,
        arguments: Dict[str, object],
        runtime: Optional[Dict[str, object]],
        cause: Exception,
    ) -> Optional[Dict[str, object]]:
        """Fallback direct Tavily API implementation for static MCP tool names."""
        try:
            if tool_name == "tavily-search":
                payload = {
                    "query": str(arguments.get("query") or "").strip(),
                    "topic": str(arguments.get("topic") or "general").strip() or "general",
                    "search_depth": str(arguments.get("search_depth") or "basic").strip() or "basic",
                    "max_results": int(arguments.get("max_results") or 5),
                    "include_answer": bool(arguments.get("include_answer", True)),
                    "include_raw_content": bool(arguments.get("include_raw_content", False)),
                    "include_images": bool(arguments.get("include_images", False)),
                    "include_favicon": bool(arguments.get("include_favicon", False)),
                    "include_domains": list(arguments.get("include_domains") or []),
                    "exclude_domains": list(arguments.get("exclude_domains") or []),
                    "days": arguments.get("days"),
                    "time_range": arguments.get("time_range"),
                }
                if not payload["query"]:
                    raise ValueError("tavily-search requires a non-empty query")
                payload = {key: value for key, value in payload.items() if value not in [None, [], ""]}
                data = self._tavily_request(server, "/search", payload, runtime)
                lines: List[str] = []
                answer = str(data.get("answer") or "").strip()
                if answer:
                    lines.append("Answer: %s" % answer)
                results = list(data.get("results") or [])
                for index, item in enumerate(results, start=1):
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or item.get("url") or "Result %s" % index).strip()
                    url = str(item.get("url") or "").strip()
                    snippet = str(item.get("content") or item.get("snippet") or "").strip()
                    block = ["%s. %s" % (index, title)]
                    if url:
                        block.append("URL: %s" % url)
                    if snippet:
                        block.append("Snippet: %s" % snippet)
                    lines.append("\n".join(block))
                content = "\n\n".join(lines).strip()
                if not content:
                    content = json.dumps(data, ensure_ascii=False, indent=2)
                return self._result_from_payload(
                    server_slug=server.slug,
                    tool_name=tool_name,
                    content_text=content,
                    structured_content=data,
                    raw_result={"content": [{"type": "text", "text": content}], "structuredContent": data, "isError": False},
                    fallback="tavily-http",
                )
            if tool_name == "tavily-extract":
                urls = arguments.get("urls")
                if isinstance(urls, str):
                    urls = [urls]
                payload = {
                    "urls": list(urls or []),
                    "extract_depth": str(arguments.get("extract_depth") or "basic").strip() or "basic",
                    "format": str(arguments.get("format") or "markdown").strip() or "markdown",
                    "include_images": bool(arguments.get("include_images", False)),
                    "include_favicon": bool(arguments.get("include_favicon", False)),
                    "query": str(arguments.get("query") or "").strip(),
                    "chunks_per_source": arguments.get("chunks_per_source"),
                    "timeout": arguments.get("timeout"),
                }
                if not payload["urls"]:
                    raise ValueError("tavily-extract requires at least one URL")
                payload = {key: value for key, value in payload.items() if value not in [None, [], ""]}
                data = self._tavily_request(server, "/extract", payload, runtime)
                lines = []
                for item in list(data.get("results") or []):
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    raw_content = str(item.get("raw_content") or "").strip()
                    block = []
                    if url:
                        block.append("URL: %s" % url)
                    if raw_content:
                        block.append(raw_content)
                    if block:
                        lines.append("\n".join(block))
                content = "\n\n".join(lines).strip()
                if not content:
                    content = json.dumps(data, ensure_ascii=False, indent=2)
                return self._result_from_payload(
                    server_slug=server.slug,
                    tool_name=tool_name,
                    content_text=content,
                    structured_content=data,
                    raw_result={"content": [{"type": "text", "text": content}], "structuredContent": data, "isError": False},
                    fallback="tavily-http",
                )
        except (HTTPError, URLError, ValueError, MCPTransportError) as exc:
            message = "Tavily fallback failed after MCP transport error: %s | %s" % (_sanitize_error(cause), _sanitize_error(exc))
            return self._result_from_payload(
                server_slug=server.slug,
                tool_name=tool_name,
                content_text=message,
                structured_content={"error": message},
                status="error",
                raw_result={"content": [{"type": "text", "text": message}], "structuredContent": {"error": message}, "isError": True},
                fallback="tavily-http",
            )
        return None

    def call_tool(
        self,
        server_slug: str,
        tool_name: str,
        arguments: Dict[str, object],
        runtime: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        """Call an external MCP tool through stdio transport."""
        server = self.get_server(server_slug)
        if server is None:
            raise KeyError("MCP server not found: %s" % server_slug)
        if not server.enabled:
            raise PermissionError("MCP server is disabled: %s" % server.slug)
        if server.slug == "filesystem":
            return self._filesystem_local_tool(server, tool_name, arguments, runtime)
        try:
            with MCPStdioClient(server, default_env=self._default_env(runtime)) as client:
                result = client.call_tool(tool_name, dict(arguments or {}))
        except Exception as exc:
            if server.slug == "tavily":
                fallback = self._tavily_fallback(server, tool_name, arguments, runtime, exc)
                if fallback is not None:
                    return fallback
            raise
        content_text = self._extract_text_content(result.get("content"))
        is_error = bool(result.get("isError", False))
        return {
            "status": "error" if is_error else "ok",
            "server_slug": server.slug,
            "tool_name": tool_name,
            "content": content_text,
            "content_preview": shorten(content_text, 500),
            "structured_content": result.get("structuredContent"),
            "mcp_result": result,
        }
