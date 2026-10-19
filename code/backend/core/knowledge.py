"""Apprentissage continu : cache de connaissances (question -> reponse).

Chaque echange substantiel est memorise sous forme vectorielle. Lorsqu'une
nouvelle question ressemble fortement a une connaissance deja apprise, la
reponse est servie instantanement sans rappeler le LLM (chemin rapide type SFT).
"""

import asyncio
import re
from datetime import datetime

from backend.core.database import get_db
from backend.core.vector_store import Embedder, cosine

SIMILARITY_ANSWER = 0.90
SIMILARITY_MERGE = 0.94

_GAP_RE = re.compile(
    r"je ne sais pas|je n'ai pas|je ne dispose|aucune information|"
    r"ne peux pas|impossible de|aucun resultat|n'ai pas trouv|"
    r"i don't know|i do not know|i don't have",
    re.IGNORECASE,
)

_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_substantive(answer: str) -> bool:
    if len(answer.strip()) < 5:
        return False
    head = answer[:400]
    if _GAP_RE.search(head) and len(answer) < 300:
        return False
    return True


async def remember(embedder: Embedder, question: str, answer: str) -> bool:
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not is_substantive(answer):
        return False
    async with _lock:
        try:
            vec = (await embedder.embed_batch([question[:2000]]))[0]
        except Exception:
            return False
        db = get_db()
        best_id = None
        for row in db.query_all("SELECT id, q_embedding FROM learned_knowledge"):
            if not row["q_embedding"]:
                continue
            if cosine(vec, _to_list(row["q_embedding"])) >= SIMILARITY_MERGE:
                best_id = row["id"]
                break
        packed = _pack(vec)
        if best_id is not None:
            db.execute(
                "UPDATE learned_knowledge SET answer=?, q_embedding=?, updated_at=? WHERE id=?",
                (answer[:8000], packed, _now(), best_id),
            )
            return True
        db.execute(
            "INSERT INTO learned_knowledge (question, answer, q_embedding, created_at,"
            " updated_at) VALUES (?,?,?,?,?)",
            (question[:500], answer[:8000], packed, _now(), _now()),
        )
        return True


def _pack(vec: list[float]) -> bytes:
    import struct

    return struct.pack(f"<{len(vec)}f", *vec)


def _to_list(blob: bytes) -> list[float]:
    import struct

    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


async def lookup(embedder: Embedder, question: str) -> dict | None:
    question = (question or "").strip()
    if not question:
        return None
    rows = get_db().query_all(
        "SELECT id, question, answer, hits, q_embedding FROM learned_knowledge"
    )
    rows = [r for r in rows if r["q_embedding"]]
    if not rows:
        return None
    try:
        vec = (await embedder.embed_batch([question[:2000]]))[0]
    except Exception:
        return None
    best = None
    best_score = 0.0
    for row in rows:
        score = cosine(vec, _to_list(row["q_embedding"]))
        if score > best_score:
            best_score = score
            best = row
    if best is None or best_score < SIMILARITY_ANSWER:
        return None
    get_db().execute(
        "UPDATE learned_knowledge SET hits=hits+1 WHERE id=?", (best["id"],)
    )
    return {
        "id": best["id"],
        "question": best["question"],
        "answer": best["answer"],
        "score": round(best_score, 4),
    }


def stats() -> dict:
    row = get_db().query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(hits),0) AS h FROM learned_knowledge"
    )
    recent = get_db().query_all(
        "SELECT id, question, hits, created_at FROM learned_knowledge"
        " ORDER BY id DESC LIMIT 8"
    )
    return {"count": row["n"], "reuse_total": row["h"], "recent": recent}


def clear() -> int:
    db = get_db()
    n = db.query_one("SELECT COUNT(*) AS n FROM learned_knowledge")["n"]
    db.execute("DELETE FROM learned_knowledge")
    return n


async def search_preview(embedder: Embedder, query: str, top_k: int = 5) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    try:
        vec = (await embedder.embed_batch([query[:2000]]))[0]
    except Exception:
        return []
    scored = []
    for row in get_db().query_all(
        "SELECT id, question, answer, hits, q_embedding FROM learned_knowledge"
    ):
        if not row["q_embedding"]:
            continue
        s = cosine(vec, _to_list(row["q_embedding"]))
        scored.append(
            {
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"][:300],
                "hits": row["hits"],
                "score": round(s, 4),
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
