"""Routes premium : connaissances apprises, bibliothèque de prompts,
statistiques d'usage, export de conversation."""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.core.database import get_db
from backend.core.knowledge import clear as knowledge_clear
from backend.core.knowledge import search_preview, stats as knowledge_stats
from backend.core.vector_store import Embedder
from backend.inference import get_active_backend

router = APIRouter(prefix="/api", tags=["extras"])


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class PromptCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1)


class KnowledgeQuery(BaseModel):
    query: str = Field(min_length=1)


@router.get("/knowledge/stats")
async def kstats() -> dict:
    return knowledge_stats()


@router.delete("/knowledge")
async def kclear() -> dict:
    return {"deleted": knowledge_clear()}


@router.post("/knowledge/search")
async def ksearch(body: KnowledgeQuery) -> dict:
    embedder = Embedder(get_active_backend())
    results = await search_preview(embedder, body.query)
    return {"results": results}


@router.get("/distill/stats")
async def distill_stats_endpoint() -> dict:
    from backend.core.distiller import teacher_review_stats

    return teacher_review_stats()


@router.get("/distill/export")
async def distill_export() -> Response:
    from backend.core.distiller import export_sft_dataset

    content, count = export_sft_dataset()
    return Response(
        content=content or "",
        media_type="application/jsonl",
        headers={
            "Content-Disposition": 'attachment; filename="localai_sft_dataset.jsonl"',
            "X-Dataset-Count": str(count),
        },
    )


@router.get("/distill/export-traces")
async def distill_export_traces() -> Response:
    """Dataset de COMPORTEMENT AGENT : sessions d'outils reussies."""
    from backend.core.distiller import export_tool_traces

    content, count = export_tool_traces()
    return Response(
        content=content or "",
        media_type="application/jsonl",
        headers={
            "Content-Disposition": 'attachment; filename="localai_agent_traces.jsonl"',
            "X-Dataset-Count": str(count),
        },
    )


@router.get("/distill/export-dpo")
async def distill_export_dpo() -> Response:
    """Paires DPO choisi/rejete pour apprentissage des preferences."""
    from backend.core.distiller import export_dpo_pairs

    content, count = export_dpo_pairs()
    return Response(
        content=content or "",
        media_type="application/jsonl",
        headers={
            "Content-Disposition": 'attachment; filename="localai_dpo_pairs.jsonl"',
            "X-Dataset-Count": str(count),
        },
    )


@router.api_route("/distill/lora/prepare", methods=["GET", "POST"])
async def distill_lora_prepare(tier: str = "") -> dict:
    """Construit l'atelier fine-tuning LoRA complet (datasets + script + README)."""
    from backend.core.lora_prep import preparer

    return preparer(tier.strip())


@router.post("/distill/upgrade")
async def distill_upgrade() -> dict:
    from backend.core.distiller import upgrade_weak_answers

    backend = get_active_backend()
    result = await upgrade_weak_answers(backend)
    return result


@router.get("/prompts")
async def prompts_list() -> dict:
    return {
        "prompts": get_db().query_all(
            "SELECT id, name, content, created_at FROM prompts ORDER BY name"
        )
    }


@router.post("/prompts")
async def prompts_create(body: PromptCreate) -> dict:
    pid = get_db().execute(
        "INSERT INTO prompts (name, content, created_at) VALUES (?,?,?)",
        (body.name.strip(), body.content.strip(), _now()),
    )
    return {"id": pid}


@router.delete("/prompts/{pid}")
async def prompts_delete(pid: int) -> dict:
    get_db().execute("DELETE FROM prompts WHERE id=?", (pid,))
    return {"deleted": pid}


@router.get("/stats/usage")
async def usage_stats() -> dict:
    db = get_db()
    totals = db.query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS pt,"
        " COALESCE(SUM(completion_tokens),0) AS ct FROM messages WHERE role='assistant'"
    )
    by_model = db.query_all(
        "SELECT model, COUNT(*) AS n, SUM(completion_tokens) AS tokens"
        " FROM messages WHERE role='assistant' AND model != ''"
        " GROUP BY model ORDER BY n DESC LIMIT 6"
    )
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    per_day_rows = db.query_all(
        "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n,"
        " SUM(completion_tokens) AS tokens FROM messages"
        " WHERE role='assistant' AND created_at >= ? GROUP BY day ORDER BY day",
        (since,),
    )
    conv_count = db.query_one("SELECT COUNT(*) AS n FROM conversations")["n"]
    doc_count = db.query_one("SELECT COUNT(*) AS n FROM documents")["n"]
    return {
        "assistant_messages": totals["n"],
        "prompt_tokens": totals["pt"],
        "completion_tokens": totals["ct"],
        "conversations": conv_count,
        "documents": doc_count,
        "by_model": by_model,
        "per_day": per_day_rows,
    }


@router.get("/conversations/{cid}/export")
async def export_conversation(cid: int) -> Response:
    conv = get_db().query_one("SELECT * FROM conversations WHERE id=?", (cid,))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    msgs = get_db().query_all(
        "SELECT role, content, model, created_at FROM messages"
        " WHERE conversation_id=? ORDER BY id",
        (cid,),
    )
    lines = [
        f"# {conv['title']}",
        "",
        f"*Exportée le {datetime.now().strftime('%d/%m/%Y %H:%M')} — LocalAI*",
        "",
    ]
    for m in msgs:
        who = "🧑 **Vous**" if m["role"] == "user" else f"🤖 **LocalAI** ({m['model'] or 'auto'})"
        lines += [f"### {who}", "", m["content"], ""]
    md = "\n".join(lines)
    safe = "".join(c for c in conv["title"] if c.isalnum() or c in " -_")[:40] or "conversation"
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}.md"'},
    )
