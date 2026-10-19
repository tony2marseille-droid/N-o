"""Distillation des modeles : export SFT + re-distillation des reponses faibles.

Deux mecanismes :
1. export_sft_dataset() : construit un dataset JSONL (format OpenAI chat)
   a partir de la memoire apprise et des reponses bien notees -> utilisable
   pour un futur fine-tuning LoRA hors ligne.
2. upgrade_weak_answers() : re-genere avec le GROS modele les reponses
   notees en dessous du seuil, puis les stocke dans la memoire SFT pour
   que le petit modele y accede instantanement (distillage comportemental).
"""

import json

from backend.core.database import get_db
from backend.core.knowledge import is_substantive, remember as knowledge_remember
from backend.core.vector_store import Embedder

WEAK_THRESHOLD = 55


def export_sft_dataset() -> tuple[str, int]:
    """Construit le dataset JSONL. Retourne (contenu, nombre d'exemples)."""
    db = get_db()
    seen: set[str] = set()
    examples: list[dict] = []

    for row in db.query_all("SELECT question, answer FROM learned_knowledge"):
        q = (row["question"] or "").strip()
        a = (row["answer"] or "").strip()
        if not q or not a:
            continue
        key = q.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        examples.append(
            {"messages": [{"role": "user", "content": q}, {"role": "assistant", "content": a}]}
        )

    scored = db.query_all(
        "SELECT m.id, m.conversation_id, m.content FROM messages m "
        "WHERE m.role='assistant' AND m.quality_score >= 70 ORDER BY m.id DESC LIMIT 400"
    )
    try:
        from backend.core.interests import trier_par_priorite

        scored = trier_par_priorite(scored)
    except Exception:
        pass
    for row in scored:
        user_msg = db.query_one(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' "
            "AND id<? ORDER BY id DESC LIMIT 1",
            (row["conversation_id"], row["id"]),
        )
        if not user_msg:
            continue
        q = user_msg["content"].strip()
        a = row["content"].strip()
        if len(a) < 40:
            continue
        key = q.lower()[:120]
        if key in seen or not q:
            continue
        seen.add(key)
        examples.append(
            {"messages": [{"role": "user", "content": q}, {"role": "assistant", "content": a}]}
        )

    lines = [json.dumps(e, ensure_ascii=False) for e in examples]
    return "\n".join(lines), len(examples)


def distill_stats() -> dict:
    db = get_db()
    total = db.query_one(
        "SELECT COUNT(*) AS n FROM messages WHERE role='assistant' AND quality_score IS NOT NULL"
    )
    weak = db.query_one(
        f"SELECT COUNT(*) AS n FROM messages WHERE role='assistant' AND quality_score < {WEAK_THRESHOLD}"
    )
    good = db.query_one(
        "SELECT COUNT(*) AS n FROM messages WHERE role='assistant' AND quality_score >= 70"
    )
    learned = db.query_one("SELECT COUNT(*) AS n FROM learned_knowledge")
    return {
        "scored_answers": total["n"] if total else 0,
        "weak_answers": weak["n"] if weak else 0,
        "good_answers": good["n"] if good else 0,
        "learned_entries": learned["n"] if learned else 0,
        "weak_threshold": WEAK_THRESHOLD,
    }


async def upgrade_weak_answers(backend, limit: int = 10) -> dict:
    """Re-genere les reponses faibles avec le plus gros modele installe.

    Les nouvelles reponses alimentent la memoire SFT : le routage servira
    ensuite la version distillee instantanement.
    """
    db = get_db()
    rows = db.query_all(
        "SELECT id, conversation_id, content FROM messages "
        f"WHERE role='assistant' AND quality_score < {WEAK_THRESHOLD} "
        "ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 50)),),
    )
    embedder = Embedder(backend)

    installed = await backend.list_installed()
    from backend.core.router import route_model

    catalog = {}
    try:
        from backend.core.catalog import load_local_catalog

        catalog = {e.tag: e.capabilities for e in load_local_catalog()}
    except Exception:
        pass
    candidates = [
        {
            "tag": m.tag,
            "capabilities": catalog.get(m.tag, []),
            "parameter_size": m.parameter_size,
        }
        for m in installed
        if not m.family.lower().startswith("bert") and "embed" not in m.name.lower()
    ]
    big_model = None
    try:
        from backend.core.hardware import get_role_model

        big_model = get_role_model("teacher")
    except Exception:
        pass
    if not big_model:
        from backend.core.router import route_model

        big_model, _ = route_model(
            "explique moi ce qu'est l'intelligence artificielle en detail", candidates
        )

    upgraded = 0
    skipped = 0
    for row in rows:
        user_msg = db.query_one(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' "
            "AND id<? ORDER BY id DESC LIMIT 1",
            (row["conversation_id"], row["id"]),
        )
        if not user_msg or not big_model:
            skipped += 1
            continue
        question = user_msg["content"].strip()
        parts: list[str] = []
        try:
            async for event in backend.chat_stream(
                big_model,
                [{"role": "user", "content": question}],
                {"temperature": 0.4},
            ):
                if event.type == "token":
                    parts.append(event.content)
                elif event.type == "error":
                    break
        except Exception:
            skipped += 1
            continue
        answer = "".join(parts).strip()
        if answer and is_substantive(answer):
            await knowledge_remember(embedder, question, answer)
            upgraded += 1
        else:
            skipped += 1

    return {"upgraded": upgraded, "skipped": skipped, "model_used": big_model, "candidates": len(rows)}


def export_tool_traces(min_score: int = 70) -> tuple[str, int]:
    """Exporte les sessions d'agents REUSSIES en dataset multi-tours avec outils.

    Une conversation est retenue si sa reponse finale est bien notee OU si ses
    traces contiennent un verdict de controle qualite positif. Format : dialogue
    user -> assistant (appel d'outil JSON) -> outil (resultat) -> ... -> final.
    C'est le dataset d'apprentissage du COMPORTEMENT agentique.
    """
    db = get_db()
    convos = db.query_all(
        "SELECT DISTINCT conversation_id FROM tool_traces ORDER BY conversation_id DESC LIMIT 200"
    )
    examples: list[dict] = []
    for row in convos:
        cid = row["conversation_id"]
        final = db.query_one(
            "SELECT content, quality_score FROM messages "
            "WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
            (cid,),
        )
        if not final:
            continue
        verdict_ok = False
        traces = db.query_all(
            "SELECT seq, tool_name, arguments, result_preview FROM tool_traces "
            "WHERE conversation_id=? ORDER BY id",
            (cid,),
        )
        if not traces:
            continue
        for t in traces:
            if "VERDICT: OUI" in (t["result_preview"] or "") or "DECISION FINALE: OUI" in (
                t["result_preview"] or ""
            ):
                verdict_ok = True
                break
        score_ok = (final["quality_score"] or 0) >= min_score
        if not (verdict_ok or score_ok):
            continue
        first_user = db.query_one(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY id LIMIT 1",
            (cid,),
        )
        if not first_user:
            continue
        convo: list[dict] = [{"role": "user", "content": first_user["content"]}]
        derniere_seq = -1
        for t in traces:
            if t["seq"] != derniere_seq:
                convo.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": t["tool_name"], "arguments": t["arguments"]}}
                        ],
                    }
                )
                derniere_seq = t["seq"]
            convo.append({"role": "tool", "content": t["result_preview"], "tool_name": t["tool_name"]})
        convo.append({"role": "assistant", "content": final["content"]})
        examples.append({"messages": convo})
    lines = [json.dumps(e, ensure_ascii=False) for e in examples]
    return "\n".join(lines), len(examples)


def export_dpo_pairs() -> tuple[str, int]:
    """Construit des paires DPO (choisi/rejete) : questions dont la premiere
    reponse etait faible ET qui ont depuis une version amelioree en memoire.
    Format par ligne : {prompt, chosen, rejected}.
    """
    db = get_db()
    weak_rows = db.query_all(
        "SELECT m.id, m.conversation_id, m.content FROM messages m "
        f"WHERE m.role='assistant' AND m.quality_score < {WEAK_THRESHOLD} "
        "ORDER BY m.id DESC LIMIT 200"
    )
    embedder = None
    pairs: list[dict] = []
    for row in weak_rows:
        user_msg = db.query_one(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' "
            "AND id<? ORDER BY id DESC LIMIT 1",
            (row["conversation_id"], row["id"]),
        )
        if not user_msg:
            continue
        q = user_msg["content"].strip()
        if len(q) < 10:
            continue
        chosen = db.query_one(
            "SELECT answer FROM learned_knowledge WHERE question LIKE ? ORDER BY hits DESC LIMIT 1",
            (q[:60] + "%",),
        )
        if not chosen or len(chosen["answer"]) < 80:
            continue
        pairs.append(
            {
                "prompt": q,
                "chosen": chosen["answer"].strip(),
                "rejected": row["content"].strip(),
            }
        )
        if len(pairs) >= 300:
            break
    _ = embedder
    lines = [json.dumps(p, ensure_ascii=False) for p in pairs]
    return "\n".join(lines), len(pairs)


def teacher_review_stats() -> dict:
    """Stats completes pour piloter la boucle de distillation."""
    base = distill_stats()
    db = get_db()
    traces = db.query_one("SELECT COUNT(DISTINCT conversation_id) AS n FROM tool_traces")
    base["agent_sessions_traced"] = traces["n"] if traces else 0
    return base
