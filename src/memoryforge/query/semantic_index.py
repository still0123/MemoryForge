from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Optional


class SemanticIndex:
    def __init__(self, path: Path, *, repository_id: str) -> None:
        self._path = Path(path)
        self._repository_id = repository_id
        self._ids: list[str] = []
        self._hashes: list[str] = []
        self._vectors: list[list[float]] = []
        self._index_id: Optional[str] = None
        self._built = False

        try:
            import fastembed  # noqa: F401

            self._backend_available = True
        except ImportError:
            try:
                import sentence_transformers  # noqa: F401

                self._backend_available = True
            except ImportError:
                self._backend_available = False

    def available(self) -> bool:
        return self._backend_available

    def build(self, pages: list[dict[str, Any]], symbols: list[dict[str, Any]]) -> None:
        ids: list[str] = []
        hashes: list[str] = []
        texts: list[str] = []

        for page in pages:
            page_path = page["page_path"]
            content_hash = str(page.get("content_hash", ""))
            text = str(page.get("text", ""))
            obj_id = f"page:{page_path}"
            ids.append(obj_id)
            hashes.append(content_hash)
            texts.append(text)

        for sym in symbols:
            symbol_id = sym["symbol_id"]
            qualified_name = str(sym.get("qualified_name", ""))
            obj_id = f"symbol:{symbol_id}"
            ids.append(obj_id)
            h = hashlib.sha256(qualified_name.encode("utf-8")).hexdigest()
            hashes.append(h)
            texts.append(qualified_name)

        combined_text = "\n".join(texts)
        text_hash = hashlib.sha256(combined_text.encode("utf-8")).hexdigest()
        self._index_id = hashlib.sha256(
            f"{self._repository_id}\0{text_hash}".encode("utf-8")
        ).hexdigest()

        self._ids = ids
        self._hashes = hashes

        if self._backend_available:
            try:
                self._vectors = self._embed_batch(texts)
            except Exception:
                self._vectors = []
        else:
            self._vectors = []

        self._built = True

    def search(self, query_text: str, k: int = 20) -> list[tuple[str, float]]:
        if not self._built or not self._backend_available:
            return []
        if not self._vectors:
            return []

        try:
            q_vec = self._embed_one(query_text)
        except Exception:
            return []

        scores: list[tuple[str, float]] = []
        for obj_id, vec in zip(self._ids, self._vectors):
            sim = self._cosine(q_vec, vec)
            scores.append((obj_id, sim))

        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores[: max(0, k)]

    @property
    def index_id(self) -> Optional[str]:
        return self._index_id

    @property
    def object_ids(self) -> tuple[str, ...]:
        return tuple(self._ids)

    @property
    def object_hashes(self) -> tuple[str, ...]:
        return tuple(self._hashes)

    def _embed_one(self, text: str) -> list[float]:
        vectors = self._embed_batch([text])
        if vectors:
            return vectors[0]
        return self._fallback_embed(text)

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            import fastembed

            model = fastembed.TextEmbedding()
            vectors: list[list[float]] = []
            for vec in model.embed(texts):
                if hasattr(vec, "tolist"):
                    vectors.append([float(x) for x in vec.tolist()])
                else:
                    vectors.append([float(x) for x in vec])
            return vectors
        except ImportError:
            pass
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer("all-MiniLM-L6-v2")
            embeddings = model.encode(texts)
            vectors = []
            for vec in embeddings:
                if hasattr(vec, "tolist"):
                    vectors.append([float(x) for x in vec.tolist()])
                else:
                    vectors.append([float(x) for x in vec])
            return vectors
        except ImportError:
            pass
        return [self._fallback_embed(t) for t in texts]

    @staticmethod
    def _fallback_embed(text: str) -> list[float]:
        dim = 64
        vec = [0.0] * dim
        h = hashlib.sha256(text.encode("utf-8")).digest()
        for i in range(dim):
            b = h[i % len(h)]
            vec[i] = (b / 255.0 - 0.5) * 2.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)
