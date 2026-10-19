"""RAG v2 : recherche hybride (vecteurs + BM25 leger) fusionnees par RRF,
expansion des chunks voisins, modele d'embed resolu selon le palier materiel.

Garde la compatibilite avec l'ancienne API : search() par vecteur seul reste
disponible ; search_hybrid() est la nouvelle voie principale.
"""

import math
import re
from collections import Counter

from backend.core.database import get_db
from backend.core.vector_store import Embedder, _unpack

_STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "a", "au",
    "aux", "en", "dans", "sur", "pour", "avec", "sans", "que", "qui", "quoi",
    "est", "sont", "the", "of", "to", "and", "is", "are", "for", "with", "on",
    "in", "it", "je", "tu", "il", "nous", "vous", "ils", "me", "moi", "te",
    "se", "ce", "cette", "ces", "son", "sa", "ses", "mon", "ma", "mes",
}


def _tokens(texte: str) -> list[str]:
    return [
        t
        for t in re.findall(r"[a-z0-9]{2,}", (texte or "").lower())
        if t not in _STOPWORDS
    ]


def embed_model() -> str:
    """Modele d'embedding du palier (mxbai en T3), sinon nomic."""
    try:
        from backend.core.hardware import get_role_model

        m = get_role_model("embed")
        if m:
            return m
    except Exception:
        pass
    return "nomic-embed-text:latest"


def _chunks_bdd() -> list[dict]:
    return get_db().query_all(
        "SELECT c.id, c.document_id, d.filename, c.position, c.content, c.embedding"
        " FROM document_chunks c JOIN documents d ON d.id = c.document_id"
    )


def _bm25_scores(query: str, rows: list[dict]) -> dict[int, float]:
    """BM25 simplifie (k1=1.5, b=0.75) sur les chunks en base."""
    q_tokens = _tokens(query)
    if not q_tokens:
        return {}
    docs_tokens = {r["id"]: _tokens(r["content"]) for r in rows}
    n_docs = max(len(rows), 1)
    avgdl = sum(len(t) for t in docs_tokens.values()) / n_docs or 1.0
    df: Counter = Counter()
    for toks in docs_tokens.values():
        for term in set(toks):
            df[term] += 1
    scores: dict[int, float] = {}
    k1, b = 1.5, 0.75
    for rid, toks in docs_tokens.items():
        tf = Counter(toks)
        dl = len(toks) or 1
        s = 0.0
        for term in q_tokens:
            if term not in tf:
                continue
            idf = math.log((n_docs - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
            s += idf * (tf[term] * (k1 + 1)) / (
                tf[term] + k1 * (1 - b + b * dl / avgdl)
            )
        if s > 0:
            scores[rid] = s
    return scores


def _rrf(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion sur plusieurs classements d'ids de chunks."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for pos, rid in enumerate(ranking):
            fused[rid] = fused.get(rid, 0.0) + 1.0 / (k + pos + 1)
    return fused


def _expand_voisins(rows: list[dict], top_ids: list[int], limite: int) -> list[dict]:
    """Ajoute les chunks voisins (position +/-1, meme document) aux tops."""
    by_id = {r["id"]: r for r in rows}
    voisins_par_id: dict[int, int] = {}
    for rid in top_ids:
        r = by_id.get(rid)
        if not r:
            continue
        for v in rows:
            if v["document_id"] == r["document_id"] and abs(v["position"] - r["position"]) == 1:
                voisins_par_id.setdefault(v["id"], rid)
    fusionnes: dict[int, dict] = {}
    ordre: list[int] = []
    for rid in top_ids[:limite] + [v for v in voisins_par_id if v not in top_ids][:limite]:
        if rid in fusionnes or rid not in by_id:
            continue
        fusionnes[rid] = by_id[rid]
        ordre.append(rid)
    # fusion du texte des voisins contigus dans un meme bloc par chunk principal
    resultats: list[dict] = []
    vus: set[int] = set()
    for rid in ordre:
        if rid in vus:
            continue
        r = dict(by_id[rid])
        morceaux = [r["content"]]
        vus.add(rid)
        for vid, principal in voisins_par_id.items():
            if principal != rid or vid in vus:
                continue
            v = by_id.get(vid)
            if v and v["document_id"] == r["document_id"]:
                morceaux.append(v["content"])
                vus.add(vid)
        contenu = "\n".join(m for m in morceaux if m)
        if len(contenu) > 2400:
            contenu = contenu[:2400]
        r["content"] = contenu
        resultats.append(r)
    return resultats


def _rerank_sync(query: str, contenus: list[str]) -> list[float]:
    """Rerank croise : le petit modele note la pertinence de chaque chunk (0-10).

    Un seul appel LLM pour tous les chunks. Best effort : echec = scores neutres.
    """
    import httpx
    import os

    blocs = "\n".join(f"[{i}] {c[:600]}" for i, c in enumerate(contenus))
    try:
        r = httpx.post(
            f"{os.environ.get('LOCALAI_OLLAMA_URL', 'http://127.0.0.1:11434')}/api/generate",
            json={
                "model": "gemma3:4b",
                "prompt": (
                    "Question: " + query[:300] + "\n\n"
                    "Fragments:\n" + blocs + "\n\n"
                    "Note la pertinence de chaque fragment pour repondre a la "
                    "question. Reponds UNIQUEMENT par un tableau JSON d'entiers "
                    "0-10 dans l'ordre, ex: [7, 2, 9]"
                ),
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 100},
            },
            timeout=25,
        )
        import json as _json

        m = re.search(r"\[[0-9,\s]+\]", r.json().get("response", ""))
        if not m:
            return [1.0] * len(contenus)
        notes = _json.loads(m.group(0))
        if not isinstance(notes, list) or len(notes) != len(contenus):
            return [1.0] * len(contenus)
        return [max(0.0, min(10.0, float(n))) for n in notes]
    except Exception:
        return [1.0] * len(contenus)


def _variantes_requete_sync(query: str) -> list[str]:
    """Genere 2 paraphrases de la requete via le petit modele local (best effort)."""
    import httpx
    import os

    base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
    try:
        r = httpx.post(
            f"{base}/api/generate",
            json={
                "model": "gemma3:4b",
                "prompt": (
                    "Reformule cette recherche documentaire en 2 variantes tres "
                    "courtes (une par ligne, sans numerotation), meme langue :\n"
                    + query[:300]
                ),
                "stream": False,
                "options": {"temperature": 0.5, "num_predict": 80},
            },
            timeout=10,
        )
        lignes = [
            l.strip("-•0123456789. )")
            for l in r.json().get("response", "").strip().splitlines()
            if len(l.strip()) > 8
        ]
        return lignes[:2]
    except Exception:
        return []


async def search_hybrid(
    backend,
    query: str,
    k: int = 5,
    seuil: float = 0.25,
    multi_query: bool = True,
    rerank: bool | None = None,
) -> tuple[list[dict], str]:
    """Retourne (resultats fusionnes, modele_embed_utilise).

    multi_query : complete la requete par 2 paraphrases generees localement,
    toutes fusionnees par RRF (robustesse aux formulations).
    """
    rows = _chunks_bdd()
    if not rows:
        return [], embed_model()
    model = embed_model()

    requetes = [query]
    if multi_query:
        try:
            from backend.core.autotune import get_param

            multi_query = bool(get_param("rag_multi_query", False))
        except Exception:
            multi_query = False
    if multi_query:
        import asyncio

        try:
            variantes = await asyncio.to_thread(_variantes_requete_sync, query)
        except Exception:
            variantes = []
        requetes += [v for v in variantes if v and v.lower() != query.lower()][:2]

    try:
        vecteurs = await Embedder(backend).embed_batch(requetes)
    except Exception:
        vecteurs = []

    # garde de compatibilite : dimensions d'embedding differentes = comparaison invalide
    premier_blob = next((r.get("embedding") for r in rows if r.get("embedding")), None)
    if vecteurs and premier_blob is not None:
        dim_bdd = len(_unpack(premier_blob))
        if len(vecteurs[0]) != dim_bdd:
            vecteurs = []

    classements: list[list[int]] = []
    bm_global: dict[int, float] = {}
    scores_vec_par_id: dict[int, float] = {}
    for vec in vecteurs:
        vect = []
        for r in rows:
            blob = r.get("embedding")
            if blob:
                s = _cos(vec, _unpack(blob))
                r["_score_vec"] = max(scores_vec_par_id.get(r["id"], 0.0), s)
                vect.append(r)
            else:
                r["_score_vec"] = 0.0
        for r in vect:
            scores_vec_par_id[r["id"]] = r["_score_vec"]
        vect.sort(key=lambda r: -r["_score_vec"])
        classements.append([r["id"] for r in vect])
    if not classements:
        return [], model
    for q in requetes[1:]:
        pass
    bm = _bm25_scores(query, rows)
    bm_global.update(bm)
    for q in requetes[1:]:
        bm_global.update(_bm25_scores(q, rows))
    if bm_global:
        classements.append([rid for rid, _ in sorted(bm_global.items(), key=lambda x: -x[1])])

    fusion = _rrf(classements)
    tries = sorted(fusion.items(), key=lambda x: -x[1])[: k * 2]

    def score_vec(rid: int) -> float:
        return scores_vec_par_id.get(rid, 0.0)

    bons = [rid for rid, _ in tries if score_vec(rid) >= seuil or rid in bm_global]
    if not bons:
        bons = [rid for rid, _ in tries[:k]]
    resultats = _expand_voisins(rows, bons, limite=k)
    rerank_actif = rerank
    if rerank_actif is None:
        try:
            from backend.core.autotune import get_param

            rerank_actif = bool(get_param("rag_rerank", False))
        except Exception:
            rerank_actif = False
    if rerank_actif and len(resultats) > 1:
        import asyncio as _asyncio

        notes = await _asyncio.to_thread(
            _rerank_sync, query, [r.get("content", "") for r in resultats]
        )
        for r, note in zip(resultats, notes):
            r["rerank"] = note
        resultats.sort(key=lambda r: -r["rerank"])
    for i, r in enumerate(resultats):
        r["score"] = round(max(fusion.get(r["id"], 0.0), 0.0) * 4, 4)
        r["filename"] = r.get("filename", "")
        r["document_id"] = r.get("document_id")
        r["position"] = r.get("position")
        r.pop("embedding", None)
        r.pop("_score_vec", None)
    return resultats[:k], model


def _cos(a: list[float], b: list[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / math.sqrt(na * nb)
