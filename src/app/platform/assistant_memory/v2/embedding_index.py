"""Memory v2 embedding index: vectors for the search index entries of a space.

``sync`` (run after each search index rebuild, in the background convergence
worker) embeds texts that have no vector yet and deletes vectors whose text
is gone, so forgotten memories lose their embeddings at the next projection.
``nearest`` embeds a question and returns the closest entries; the vectors of
a space are cached per index revision, since a few thousand 384-dimension
vectors compare in milliseconds.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from app.persistence.database import PostgresDatabase

from .contracts import MemorySpaceKey
from .embeddings import DIMENSIONS, MODEL_ID, E5Embedder, default_embedder

logger = logging.getLogger(__name__)
_BATCH = 32


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _space_values(space: MemorySpaceKey) -> tuple[str, str, str]:
    return space.principal_id, space.owner_type, space.owner_id


class PostgresMemoryV2EmbeddingIndex:
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        embedder_provider: Callable[[], E5Embedder | None] = default_embedder,
    ) -> None:
        self.database = database
        self.embedder_provider = embedder_provider
        self._cache: dict[tuple[Any, ...], tuple[list[str], Any]] = {}

    def sync(self, space: MemorySpaceKey) -> int:
        """Embed new texts of the space's search index and drop vectors of removed ones."""
        embedder = self.embedder_provider()
        if embedder is None:
            return 0
        import numpy as np

        values = _space_values(space)
        with self.database.transaction() as connection:
            texts = {
                content_digest(str(row[0])): str(row[0])
                for row in connection.execute(
                    """SELECT DISTINCT content FROM omnix_memory_v2_search_index_entries
                        WHERE principal_id = %s AND owner_type = %s AND owner_id = %s""",
                    values,
                ).fetchall()
            }
            stored = {
                str(row[0])
                for row in connection.execute(
                    """SELECT content_digest FROM omnix_memory_v2_embeddings
                        WHERE principal_id = %s AND owner_type = %s AND owner_id = %s AND model_id = %s""",
                    (*values, MODEL_ID),
                ).fetchall()
            }
        missing = [digest for digest in texts if digest not in stored]
        vectors: list[tuple[str, Any]] = []
        for start in range(0, len(missing), _BATCH):
            batch = missing[start:start + _BATCH]
            vectors.extend(zip(batch, embedder.embed([texts[digest] for digest in batch], kind="passage")))
        with self.database.transaction() as connection:
            for digest, vector in vectors:
                connection.execute(
                    """INSERT INTO omnix_memory_v2_embeddings
                           (principal_id, owner_type, owner_id, model_id, content_digest, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT DO NOTHING""",
                    (*values, MODEL_ID, digest, np.asarray(vector, dtype="<f4").tobytes()),
                )
            connection.execute(
                """DELETE FROM omnix_memory_v2_embeddings
                    WHERE principal_id = %s AND owner_type = %s AND owner_id = %s AND model_id = %s
                      AND NOT (content_digest = ANY(%s))""",
                (*values, MODEL_ID, list(texts)),
            )
        return len(vectors)

    def nearest(self, space: MemorySpaceKey, text: str, *, limit: int) -> list[tuple[str, float]]:
        """The closest search index entries to ``text`` as ``(ref_id, cosine)``, best first."""
        embedder = self.embedder_provider()
        if embedder is None or not text.strip():
            return []
        import numpy as np

        ref_ids, matrix = self._load(space)
        if not ref_ids:
            return []
        query = embedder.embed([text], kind="query")[0]
        similarities = matrix @ query
        order = np.argsort(-similarities)[: max(1, int(limit))]
        return [(ref_ids[index], float(similarities[index])) for index in order]

    def _load(self, space: MemorySpaceKey) -> tuple[list[str], Any]:
        import numpy as np

        values = _space_values(space)
        with self.database.transaction() as connection:
            revision = connection.execute(
                """SELECT index_graph_revision, entry_count FROM omnix_memory_v2_search_index_state
                    WHERE principal_id = %s AND owner_type = %s AND owner_id = %s""",
                values,
            ).fetchone()
            if revision is None:
                return [], np.zeros((0, DIMENSIONS), dtype=np.float32)
            key = (values, int(revision[0]), int(revision[1]), MODEL_ID)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            rows = connection.execute(
                """SELECT i.ref_id, i.content FROM omnix_memory_v2_search_index_entries AS i
                    WHERE i.principal_id = %s AND i.owner_type = %s AND i.owner_id = %s""",
                values,
            ).fetchall()
            vectors = {
                str(row[0]): bytes(row[1])
                for row in connection.execute(
                    """SELECT content_digest, embedding FROM omnix_memory_v2_embeddings
                        WHERE principal_id = %s AND owner_type = %s AND owner_id = %s AND model_id = %s""",
                    (*values, MODEL_ID),
                ).fetchall()
            }
        ref_ids, rows_out = [], []
        for ref_id, content in rows:
            blob = vectors.get(content_digest(str(content)))
            if blob is not None:
                ref_ids.append(str(ref_id))
                rows_out.append(np.frombuffer(blob, dtype="<f4"))
        matrix = np.vstack(rows_out) if rows_out else np.zeros((0, DIMENSIONS), dtype=np.float32)
        # One space at a time per retriever in practice; keep the newest few revisions.
        if len(self._cache) >= 16:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (ref_ids, matrix)
        return ref_ids, matrix


__all__ = ["PostgresMemoryV2EmbeddingIndex", "content_digest"]
