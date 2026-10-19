import math
import struct
from datetime import datetime
from typing import Any

from backend.core.database import get_db

EMBED_MODEL = "nomic-embed-text:latest"


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


class Embedder:
    def __init__(self, backend: Any):
        self._backend = backend
        self._model = EMBED_MODEL

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        client = self._backend.client()
        resp = await client.post(
            "/api/embed",
            json={"model": self._model, "input": texts},
            timeout=300,
        )
        if resp.status_code == 200:
            embeddings = resp.json().get("embeddings", [])
            if len(embeddings) == len(texts):
                return embeddings
        result: list[list[float]] = []
        for text in texts:
            r = await client.post(
                "/api/embeddings",
                json={"model": self._model, "prompt": text[:4000]},
                timeout=120,
            )
            r.raise_for_status()
            result.append(r.json()["embedding"])
        return result


def cosine(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def store_document(filename: str, size_bytes: int, chunks: list[str], vectors: list[list[float]]) -> int:
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    doc_id = db.execute(
        "INSERT INTO documents (filename, size_bytes, chunk_count, created_at) VALUES (?,?,?,?)",
        (filename, size_bytes, len(chunks), now),
    )
    for pos, (content, vec) in enumerate(zip(chunks, vectors)):
        db.execute(
            "INSERT INTO document_chunks (document_id, position, content, embedding) VALUES (?,?,?,?)",
            (doc_id, pos, content, _pack(vec)),
        )
    return doc_id


def search(query_vector: list[float], top_k: int = 5) -> list[dict]:
    db = get_db()
    rows = db.query_all(
        "SELECT c.id, c.document_id, d.filename, c.position, c.content, c.embedding"
        " FROM document_chunks c JOIN documents d ON d.id = c.document_id"
    )
    scored = []
    for row in rows:
        if not row["embedding"]:
            continue
        score = cosine(query_vector, _unpack(row["embedding"]))
        scored.append(
            {
                "document_id": row["document_id"],
                "filename": row["filename"],
                "position": row["position"],
                "content": row["content"],
                "score": round(score, 4),
            }
        )
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:top_k]


def list_documents() -> list[dict]:
    return get_db().query_all("SELECT id, filename, size_bytes, chunk_count, created_at FROM documents ORDER BY id DESC")


def delete_document(doc_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
