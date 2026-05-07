"""Optional vector index for structured knowledge conclusions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:  # pragma: no cover - import availability depends on runtime profile.
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    HTTPError = URLError = None
    Request = urlopen = None

from moonshine.moonshine_constants import MoonshinePaths
from moonshine.utils import ensure_directory, tokenize


def _unit_vector(vector: Sequence[float]) -> List[float]:
    """Return a normalized copy of a vector."""
    norm = math.sqrt(sum(float(item) * float(item) for item in vector))
    if norm <= 0:
        return [0.0 for _ in vector]
    return [float(item) / norm for item in vector]


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Compute cosine similarity without external numeric dependencies."""
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(float(item) * float(item) for item in left))
    right_norm = math.sqrt(sum(float(item) * float(item) for item in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    return dot / (left_norm * right_norm)


def _tag_filter_matches(item_tags: Sequence[str], required_tags: Sequence[str]) -> bool:
    """Return True when all required tags are present."""
    if not required_tags:
        return True
    normalized = {str(item).strip().lower() for item in item_tags if str(item).strip()}
    required = {str(item).strip().lower() for item in required_tags if str(item).strip()}
    return required.issubset(normalized)


def render_conclusion_embedding_text(item: Dict[str, object]) -> str:
    """Render a conclusion into the text embedded by the vector index."""
    tags = item.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return "\n".join(
        [
            "Title: %s" % str(item.get("title", "") or "").strip(),
            "Statement: %s" % str(item.get("statement", "") or "").strip(),
            "Proof sketch: %s" % str(item.get("proof_sketch", "") or "").strip(),
            "Status: %s" % str(item.get("status", "") or "").strip(),
            "Tags: %s" % ", ".join(str(tag).strip() for tag in tags if str(tag).strip()),
        ]
    ).strip()


@dataclass
class VectorSearchHit:
    """One vector-search hit."""

    conclusion_id: str
    score: float
    backend: str
    metadata: Dict[str, object]


class HashingEmbeddingProvider(object):
    """Deterministic local embedding fallback.

    This is intentionally dependency-free. It is not a replacement for a real
    semantic embedding model, but it keeps the vector-index pipeline available
    in offline tests and local development.
    """

    name = "local-hashing"

    def __init__(self, dimension: int = 384):
        self.dimension = max(32, int(dimension))

    def _token_weight(self, token: str) -> Tuple[int, float]:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % self.dimension
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        return index, sign

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed texts into deterministic normalized vectors."""
        vectors = []
        for text in texts:
            vector = [0.0 for _ in range(self.dimension)]
            tokens = tokenize(text)
            for token in tokens:
                index, sign = self._token_weight(token)
                vector[index] += sign
            vectors.append(_unit_vector(vector))
        return vectors


class OpenAIEmbeddingProvider(object):
    """OpenAI-compatible embeddings API client."""

    def __init__(self, *, model: str, base_url: str, api_key_env: str, timeout_seconds: int):
        self.name = "openai-compatible:%s" % model
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_seconds = int(timeout_seconds)

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """Create embeddings through an OpenAI-compatible endpoint."""
        if Request is None or urlopen is None:
            raise RuntimeError("urllib request support is unavailable")
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError("missing API key environment variable %s" % self.api_key_env)
        payload = json.dumps({"model": self.model, "input": list(texts)}).encode("utf-8")
        request = Request(
            self.base_url + "/embeddings",
            data=payload,
            headers={
                "Authorization": "Bearer %s" % api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, ValueError) as exc:
            raise RuntimeError("embedding request failed: %s" % exc)
        data = sorted(parsed.get("data", []), key=lambda item: int(item.get("index", 0)))
        vectors = [list(map(float, item["embedding"])) for item in data]
        if len(vectors) != len(texts):
            raise RuntimeError("embedding response count mismatch")
        return vectors


class SQLiteVectorBackend(object):
    """Portable SQLite vector backend used as a stable fallback."""

    name = "sqlite"

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths
        self.db_path = paths.knowledge_vector_sqlite_db
        ensure_directory(self.db_path.parent)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_vectors (
                    id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    text TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    embedding_provider TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_knowledge_vectors_project
                ON knowledge_vectors(project_slug)
                """
            )

    def upsert(self, item: Dict[str, object], vector: Sequence[float], embedding_provider: str) -> None:
        """Insert or replace a vector row."""
        tags = list(item.get("tags") or [])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_vectors
                (id, project_slug, title, status, tags_json, text, vector_json, embedding_provider, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    project_slug=excluded.project_slug,
                    title=excluded.title,
                    status=excluded.status,
                    tags_json=excluded.tags_json,
                    text=excluded.text,
                    vector_json=excluded.vector_json,
                    embedding_provider=excluded.embedding_provider,
                    updated_at=excluded.updated_at
                """,
                (
                    str(item["id"]),
                    str(item.get("project_slug", "") or "general"),
                    str(item.get("title", "") or ""),
                    str(item.get("status", "") or ""),
                    json.dumps(tags, ensure_ascii=False),
                    str(item.get("text", "") or ""),
                    json.dumps([float(value) for value in vector]),
                    str(embedding_provider),
                    str(item.get("updated_at", "") or ""),
                ),
            )

    def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        project_slug: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> List[VectorSearchHit]:
        """Search vectors with cosine similarity."""
        sql = "SELECT * FROM knowledge_vectors"
        params: List[object] = []
        if project_slug:
            sql += " WHERE project_slug = ?"
            params.append(project_slug)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()

        hits = []
        for row in rows:
            item_tags = json.loads(row["tags_json"])
            if not _tag_filter_matches(item_tags, list(tags or [])):
                continue
            try:
                vector = json.loads(row["vector_json"])
            except ValueError:
                continue
            score = _cosine_similarity(query_vector, vector)
            if score <= 0:
                continue
            hits.append(
                VectorSearchHit(
                    conclusion_id=row["id"],
                    score=float(score),
                    backend=self.name,
                    metadata={
                        "project_slug": row["project_slug"],
                        "title": row["title"],
                        "tags": item_tags,
                        "embedding_provider": row["embedding_provider"],
                    },
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:limit]

    def count(self) -> int:
        """Return indexed row count."""
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM knowledge_vectors").fetchone()[0])


class LanceDBVectorBackend(object):
    """LanceDB vector backend when the optional dependency is installed."""

    name = "lancedb"

    def __init__(self, paths: MoonshinePaths):
        import lancedb  # type: ignore

        self.paths = paths
        self.db = lancedb.connect(str(paths.knowledge_lancedb_dir))
        self.table_name = "knowledge_conclusions"

    def _table_exists(self) -> bool:
        return self.table_name in set(self.db.table_names())

    def _open_table(self):
        return self.db.open_table(self.table_name)

    def _row(self, item: Dict[str, object], vector: Sequence[float], embedding_provider: str) -> Dict[str, object]:
        tags = list(item.get("tags") or [])
        return {
            "id": str(item["id"]),
            "project_slug": str(item.get("project_slug", "") or "general"),
            "title": str(item.get("title", "") or ""),
            "status": str(item.get("status", "") or ""),
            "tags_json": json.dumps(tags, ensure_ascii=False),
            "text": str(item.get("text", "") or ""),
            "vector": [float(value) for value in vector],
            "embedding_provider": str(embedding_provider),
            "updated_at": str(item.get("updated_at", "") or ""),
        }

    def upsert(self, item: Dict[str, object], vector: Sequence[float], embedding_provider: str) -> None:
        """Insert or replace a LanceDB row."""
        row = self._row(item, vector, embedding_provider)
        if not self._table_exists():
            self.db.create_table(self.table_name, data=[row])
            return
        table = self._open_table()
        safe_id = row["id"].replace("'", "''")
        try:
            table.delete("id = '%s'" % safe_id)
        except Exception:
            pass
        table.add([row])

    def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        project_slug: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> List[VectorSearchHit]:
        """Search LanceDB vectors."""
        if not self._table_exists():
            return []
        table = self._open_table()
        rows = table.search([float(value) for value in query_vector]).limit(max(limit * 4, limit)).to_list()
        hits = []
        for row in rows:
            if project_slug and row.get("project_slug") != project_slug:
                continue
            item_tags = json.loads(row.get("tags_json") or "[]")
            if not _tag_filter_matches(item_tags, list(tags or [])):
                continue
            distance = float(row.get("_distance", row.get("distance", 0.0)) or 0.0)
            score = 1.0 / (1.0 + max(0.0, distance))
            hits.append(
                VectorSearchHit(
                    conclusion_id=str(row["id"]),
                    score=score,
                    backend=self.name,
                    metadata={
                        "project_slug": row.get("project_slug", ""),
                        "title": row.get("title", ""),
                        "tags": item_tags,
                        "embedding_provider": row.get("embedding_provider", ""),
                    },
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def count(self) -> int:
        """Return indexed row count."""
        if not self._table_exists():
            return 0
        table = self._open_table()
        try:
            return int(table.count_rows())
        except Exception:
            return len(table.to_list())


class ChromaDBVectorBackend(object):
    """ChromaDB vector backend when the optional dependency is installed."""

    name = "chromadb"

    def __init__(self, paths: MoonshinePaths):
        import chromadb  # type: ignore

        self.paths = paths
        self.client = chromadb.PersistentClient(path=str(paths.knowledge_chromadb_dir))
        self.collection = self.client.get_or_create_collection(
            name="knowledge_conclusions",
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, item: Dict[str, object], vector: Sequence[float], embedding_provider: str) -> None:
        """Insert or replace a ChromaDB row."""
        tags = list(item.get("tags") or [])
        self.collection.upsert(
            ids=[str(item["id"])],
            embeddings=[[float(value) for value in vector]],
            documents=[str(item.get("text", "") or "")],
            metadatas=[
                {
                    "project_slug": str(item.get("project_slug", "") or "general"),
                    "title": str(item.get("title", "") or ""),
                    "status": str(item.get("status", "") or ""),
                    "tags_json": json.dumps(tags, ensure_ascii=False),
                    "embedding_provider": str(embedding_provider),
                    "updated_at": str(item.get("updated_at", "") or ""),
                }
            ],
        )

    def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int,
        project_slug: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> List[VectorSearchHit]:
        """Search ChromaDB vectors."""
        where = {"project_slug": project_slug} if project_slug else None
        result = self.collection.query(
            query_embeddings=[[float(value) for value in query_vector]],
            n_results=max(limit * 4, limit),
            where=where,
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        hits = []
        for index, conclusion_id in enumerate(ids):
            metadata = dict(metadatas[index] or {})
            item_tags = json.loads(metadata.get("tags_json") or "[]")
            if not _tag_filter_matches(item_tags, list(tags or [])):
                continue
            distance = float(distances[index] if index < len(distances) else 0.0)
            score = max(0.0, 1.0 - distance)
            hits.append(
                VectorSearchHit(
                    conclusion_id=str(conclusion_id),
                    score=score,
                    backend=self.name,
                    metadata={
                        "project_slug": metadata.get("project_slug", ""),
                        "title": metadata.get("title", ""),
                        "tags": item_tags,
                        "embedding_provider": metadata.get("embedding_provider", ""),
                    },
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def count(self) -> int:
        """Return indexed row count."""
        return int(self.collection.count())


def build_embedding_provider(config) -> object:
    """Build the configured embedding provider."""
    provider_name = str(getattr(config, "knowledge_embedding_provider", "hashing") or "hashing").strip().lower()
    if provider_name in {"openai", "openai_compatible", "openai-compatible"}:
        api_key_env = str(getattr(config, "knowledge_embedding_api_key_env", "OPENAI_API_KEY") or "OPENAI_API_KEY")
        if os.environ.get(api_key_env):
            return OpenAIEmbeddingProvider(
                model=str(getattr(config, "knowledge_embedding_model", "text-embedding-3-small") or "text-embedding-3-small"),
                base_url=str(getattr(config, "knowledge_embedding_base_url", "https://api.openai.com/v1") or "https://api.openai.com/v1"),
                api_key_env=api_key_env,
                timeout_seconds=int(getattr(config, "knowledge_embedding_timeout_seconds", 60) or 60),
            )
    return HashingEmbeddingProvider(
        dimension=int(getattr(config, "knowledge_embedding_dimension", 384) or 384),
    )


def build_vector_backend(paths: MoonshinePaths, config) -> object:
    """Build the configured vector backend with graceful fallback."""
    backend_name = str(getattr(config, "knowledge_vector_backend", "auto") or "auto").strip().lower()
    if backend_name in {"disabled", "none", "off"}:
        return None
    if backend_name in {"auto", "lancedb"}:
        try:
            return LanceDBVectorBackend(paths)
        except Exception:
            pass
    if backend_name in {"auto", "chromadb"}:
        try:
            return ChromaDBVectorBackend(paths)
        except Exception:
            pass
    return SQLiteVectorBackend(paths)


class KnowledgeVectorIndex(object):
    """High-level vector index for knowledge conclusions."""

    def __init__(self, paths: MoonshinePaths, config):
        self.paths = paths
        self.config = config
        self.enabled = bool(getattr(config, "knowledge_vector_enabled", True))
        self.embedding_provider = build_embedding_provider(config) if self.enabled else None
        self.backend = build_vector_backend(paths, config) if self.enabled else None

    @property
    def backend_name(self) -> str:
        """Return the active backend name."""
        if self.backend is None:
            return "disabled"
        return str(getattr(self.backend, "name", "unknown"))

    @property
    def embedding_provider_name(self) -> str:
        """Return the active embedding provider name."""
        if self.embedding_provider is None:
            return "disabled"
        return str(getattr(self.embedding_provider, "name", "unknown"))

    def count(self) -> int:
        """Return indexed row count."""
        if not self.enabled or self.backend is None:
            return 0
        return int(self.backend.count())

    def upsert_conclusion(self, item: Dict[str, object]) -> None:
        """Embed and upsert a conclusion."""
        if not self.enabled or self.backend is None or self.embedding_provider is None:
            return
        payload = dict(item)
        payload["text"] = render_conclusion_embedding_text(payload)
        vector = self.embedding_provider.embed_texts([payload["text"]])[0]
        self.backend.upsert(payload, vector, self.embedding_provider_name)

    def search(
        self,
        query: str,
        *,
        limit: int,
        project_slug: Optional[str] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> List[VectorSearchHit]:
        """Search the vector index."""
        if not self.enabled or self.backend is None or self.embedding_provider is None:
            return []
        if not str(query or "").strip():
            return []
        query_vector = self.embedding_provider.embed_texts([query])[0]
        return self.backend.search(
            query_vector,
            limit=limit,
            project_slug=project_slug,
            tags=tags,
        )
