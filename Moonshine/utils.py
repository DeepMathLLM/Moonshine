"""Shared utilities for Moonshine."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from functools import lru_cache
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def utc_now() -> str:
    """Return the current UTC timestamp in ISO format with a Z suffix."""
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def ensure_directory(path: Path) -> Path:
    """Create a directory tree if needed and return the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path, default: str = "") -> str:
    """Read UTF-8 text safely."""
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    """Read UTF-8 JSON safely."""
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> List[Any]:
    """Read a UTF-8 JSONL file safely, skipping malformed rows."""
    if not path.exists():
        return []
    rows: List[Any] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def parse_utc_timestamp(value: str) -> Optional[datetime]:
    """Parse an ISO UTC timestamp with a trailing Z suffix."""
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def atomic_write(path: Path, text: str) -> None:
    """Write text atomically."""
    ensure_directory(path.parent)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    try:
        os.replace(str(temp_path), str(path))
    except PermissionError:
        path.write_text(text, encoding="utf-8")
        if temp_path.exists():
            try:
                temp_path.unlink()
            except PermissionError:
                pass


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically."""
    atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, payload: Any) -> None:
    """Append a JSON line."""
    ensure_directory(path.parent)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def shorten(text: str, limit: int = 72) -> str:
    """Return a compact single-line summary."""
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def estimate_tokens_rough(text: str) -> int:
    """Rough token estimate using the Hermes-style chars-per-token heuristic."""
    if not text:
        return 0
    return (len(text) + 3) // 4


@lru_cache(maxsize=16)
def _get_tiktoken_encoding(model_name: str = "") -> Any:
    """Return a cached tiktoken encoding when the library is available."""
    if tiktoken is None:
        return None
    normalized = (model_name or "").strip()
    try:
        if normalized:
            return tiktoken.encoding_for_model(normalized)
    except Exception:
        pass
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def estimate_token_count(text: str, model_name: str = "") -> int:
    """Estimate token count, preferring tiktoken when available."""
    if not text:
        return 0
    encoding = _get_tiktoken_encoding(model_name)
    if encoding is not None:
        try:
            return len(encoding.encode(text, disallowed_special=()))
        except TypeError:
            return len(encoding.encode(text))
        except Exception:
            pass
    return estimate_tokens_rough(text)


def estimate_structured_token_count(value: Any, model_name: str = "") -> int:
    """Estimate token count for structured payloads."""
    if value is None:
        return 0
    if isinstance(value, str):
        return estimate_token_count(value, model_name=model_name)
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        payload = str(value)
    return estimate_token_count(payload, model_name=model_name)


def tokenize(text: str) -> List[str]:
    """Tokenize text into simple lexical units."""
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text or "")]


def overlap_score(query: str, text: str) -> float:
    """Compute a light lexical overlap score."""
    query_tokens = set(tokenize(query))
    text_tokens = set(tokenize(text))
    if not query_tokens or not text_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens)
    score = float(overlap) / float(len(query_tokens))
    if query.strip() and query.strip().lower() in (text or "").lower():
        score += 0.5
    return score


def slugify(text: str, prefix: str = "item") -> str:
    """Create an ASCII slug when possible, with a hashed fallback."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if slug:
        return slug[:64].strip("-")
    digest = hashlib.sha1((text or prefix).encode("utf-8")).hexdigest()[:10]
    return "%s-%s" % (prefix, digest)


def deterministic_slug(title: str, summary: str, prefix: str = "item") -> str:
    """Create a stable slug from title and summary."""
    base = slugify(title, prefix=prefix)
    digest = hashlib.sha1((title + "|" + summary).encode("utf-8")).hexdigest()[:6]
    if base.endswith(digest):
        return base
    return "%s-%s" % (base, digest)


def jaccard_similarity(left: str, right: str) -> float:
    """Compute token Jaccard similarity."""
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return float(len(left_tokens & right_tokens)) / float(len(left_tokens | right_tokens))


def bullet_list(lines: Iterable[str]) -> str:
    """Render lines as markdown bullets."""
    rendered = ["- %s" % line for line in lines if line]
    return "\n".join(rendered)
