import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.api.routes_settings import get_setting
from backend.api.schemas import ChatRequest, ConversationCreate, TitleUpdate
from backend.core.agents import get_agent
from backend.core.config import settings
from backend.core.catalog import load_local_catalog
from backend.core.database import get_db
from backend.core.knowledge import lookup as knowledge_lookup, remember as knowledge_remember
from backend.core.router import route_agent, route_model, wants_web
from backend.core.vector_store import Embedder, search as vector_search
from backend.inference import get_active_backend
from backend.inference.ollama import OllamaError
from backend.tools.executor import (
    MAX_TOOL_ROUNDS,
    PermissionManager,
    execute_tool,
    ollama_tool_specs,
)

router = APIRouter(prefix="/api", tags=["chat"])

MAX_HISTORY = 40


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _build_system_prompt(
    base: str | None,
    memories: list[dict],
    summary: str,
    rag_block: str,
) -> str:
    parts = [base] if base else []
    if memories:
        lines = "\n".join(f"- {m['content']}" for m in memories)
        parts.append(
            "Informations memorisees a propos de l'utilisateur:\n" + lines
        )
    if summary:
        parts.append(f"Resume des echanges precedents:\n{summary}")
    if rag_block:
        parts.append(rag_block)
    try:
        from backend.core.style import style_block

        adaptatif = style_block()
        if adaptatif:
            parts.append(adaptatif)
    except Exception:
        adaptatif = ""
    style = (
        "Style de reponse obligatoire : sois direct, factuel et informatif. "
        "Adapte la longueur a la question (2 a 6 phrases pour une question ouverte). "
        "Aucun poeme, aucune histoire ni format creatif sauf demande explicite."
    )
    parts.append(style)
    return "\n\n".join(p for p in parts if p)


def _resolve_doc_path(document_id: int) -> str:
    docs_dir = settings.data_dir / "documents"
    for candidate in sorted(docs_dir.glob(f"doc_{document_id}.*")):
        return str(candidate)
    return ""


def _file_url(path: str) -> str:
    if not path:
        return ""
    return Path(path).as_uri()


def _rag_block_from_results(results: list[dict], seuil: float = 0.3) -> tuple[str, list[dict]]:
    good = [r for r in results if r["score"] >= seuil]
    if not good:
        return "", []
    blocks = []
    sources = []
    for r in good:
        path = _resolve_doc_path(r.get("document_id", 0))
        url = _file_url(path)
        label = f"[Source: {r['filename']}]"
        if url:
            label += f" (lien local: {url})"
        blocks.append(label + f"\n{r['content']}")
        sources.append(
            {
                "filename": r["filename"],
                "score": r["score"],
                "excerpt": r["content"][:160],
                "url": url,
            }
        )
    context = (
        "Contexte documentaire extrait des documents de l'utilisateur:\n\n"
        + "\n\n---\n\n".join(blocks)
        + "\n\nConsigne sources : si l'utilisateur demande d'ouvrir ou de citer tes "
        "sources, liste-les sous forme de liens markdown [nom du fichier](lien local)."
    )
    return context, sources


async def _search_rag(backend, query: str, k: int = 4) -> tuple[str, list[dict]]:
    try:
        from backend.core.rag import search_hybrid

        resultats, _ = await search_hybrid(backend, query, k=max(k, 5))
        return _rag_block_from_results(resultats, seuil=0.05)
    except Exception:
        pass
    try:
        query_vec = (await backend.embed_batch([query]))[0]
    except OllamaError:
        return "", []
    return _rag_block_from_results(vector_search(query_vec, top_k=k))


async def _update_summary(backend, conversation_id: int, model: str) -> None:
    try:
        rows = get_db().query_all(
            "SELECT role, content FROM messages WHERE conversation_id=? AND role IN"
            " ('user','assistant') ORDER BY id DESC LIMIT 16",
            (conversation_id,),
        )
        if len(rows) < 8:
            return
        transcript = "\n".join(f"{r['role']}: {r['content'][:800]}" for r in reversed(rows))
        previous = get_db().query_one(
            "SELECT summary FROM conversations WHERE id=?", (conversation_id,)
        )
        prev_note = previous["summary"] if previous and previous["summary"] else ""
        prompt = (
            "Redige un resume concis (maximum 6 phrases) de cet echange, en conservant "
            "les faits, decisions et preferences utiles pour la suite.\n\n"
            f"{('Resume precedent: ' + prev_note + chr(10)) if prev_note else ''}"
            "Echange:\n" + transcript
        )
        parts: list[str] = []
        async for event in backend.chat_stream(model, [{"role": "user", "content": prompt}], {"temperature": 0.2}):
            if event.type == "token":
                parts.append(event.content)
            elif event.type == "error":
                return
        summary = "".join(parts).strip()[:2000]
        if summary:
            get_db().execute(
                "UPDATE conversations SET summary=? WHERE id=?", (summary, conversation_id)
            )
    except Exception:
        pass


async def _auto_title(backend, conversation_id: int, first_message: str) -> None:
    try:
        prompt = (
            "Donne un titre tres court (3 a 6 mots, sans ponctuation finale, sans "
            "guillemets) resume ce sujet de conversation :\n" + first_message[:400]
        )
        parts: list[str] = []
        async for event in backend.chat_stream(
            "gemma3:4b", [{"role": "user", "content": prompt}], {"temperature": 0.1}
        ):
            if event.type == "token":
                parts.append(event.content)
            elif event.type == "error":
                return
        title = "".join(parts).strip().strip('"').splitlines()[0][:60]
        if title:
            get_db().execute(
                "UPDATE conversations SET title=? WHERE id=?", (title, conversation_id)
            )
    except Exception:
        pass


@router.get("/conversations")
async def list_conversations() -> dict:
    return {
        "conversations": get_db().query_all(
            "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id)"
            " AS message_count FROM conversations c ORDER BY c.updated_at DESC"
        )
    }


@router.post("/conversations")
async def create_conversation(req: ConversationCreate) -> dict:
    cid = get_db().execute(
        "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?,?,?,?)",
        (req.title or "Nouvelle conversation", req.model, _now(), _now()),
    )
    return {"id": cid}


@router.get("/conversations/{cid}/messages")
async def get_messages(cid: int) -> dict:
    conv = get_db().query_one("SELECT * FROM conversations WHERE id=?", (cid,))
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    msgs = get_db().query_all(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (cid,)
    )
    return {"conversation": conv, "messages": msgs}


@router.patch("/conversations/{cid}")
async def rename_conversation(cid: int, body: TitleUpdate) -> dict:
    get_db().execute(
        "UPDATE conversations SET title=? WHERE id=?", (body.title.strip()[:120], cid)
    )
    return {"ok": True}


@router.delete("/conversations/{cid}")
async def delete_conversation(cid: int) -> dict:
    get_db().execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
    get_db().execute("DELETE FROM conversations WHERE id=?", (cid,))
    return {"deleted": cid}


async def _grade_answer(
    backend,
    question: str,
    answer: str,
    used_web: bool,
) -> dict | None:
    """Validation croisee : un modele rapide note la reponse (0-100)."""
    if len(answer.strip()) < 40:
        return None
    try:
        grader = "gemma3:4b"
        prompt = (
            "Tu es un evaluateur strict. Note de 0 a 100 la qualite de la reponse "
            "suivante : pertinence par rapport a la question, exactitude apparente, "
            "absence d'invention. Reponds UNIQUEMENT avec ce JSON exact :\n"
            '{"score": <entier 0-100>, "avis": "<10 mots max>"}\n\n'
            f"Question: {question[:600]}\n\n"
            + ("Des sources web ont ete utilisees.\n" if used_web else "")
            + f"Reponse: {answer[:2500]}"
        )
        parts: list[str] = []
        async for event in backend.chat_stream(
            grader, [{"role": "user", "content": prompt}], {"temperature": 0.1}
        ):
            if event.type == "token":
                parts.append(event.content)
            elif event.type == "error":
                return None
        raw = "".join(parts)
        m = re.search(r'\{[^{}]*"score"[^{}]*\}', raw, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        score = int(data.get("score", 0))
        return {
            "score": max(0, min(100, score)),
            "avis": str(data.get("avis", ""))[:120],
        }
    except Exception:
        return None


def _parse_text_tool_calls(text: str) -> list[dict] | None:
    """Secours : certains modeles emettent l'appel d'outil en JSON texte brut."""
    m = re.search(
        r'\{\s*"name"\s*:\s*"?([\w.-]+)"?\s*,\s*"arguments"\s*:\s*(\{.*\})\s*\}',
        text.strip(),
        re.S,
    )
    if not m:
        return None
    try:
        args = json.loads(m.group(2))
    except json.JSONDecodeError:
        return None
    return [{"function": {"name": m.group(1), "arguments": args}}]


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    db = get_db()
    agent = get_agent(req.agent_id)
    requested_model = req.model or "auto"
    route_reason = ""
    if not req.agent_id or req.agent_id == "auto":
        auto_agent_id, auto_agent_reason = route_agent(req.message)
        if auto_agent_id != "general":
            agent = get_agent(auto_agent_id)
            route_reason = auto_agent_reason
    if requested_model == "auto":
        backend = get_active_backend()
        installed = await backend.list_installed()
        catalog = {e.tag: e.capabilities for e in load_local_catalog()}
        candidates = [
            {
                "tag": m.tag,
                "capabilities": catalog.get(m.tag, []),
                "parameter_size": m.parameter_size,
            }
            for m in installed
            if not m.family.lower().startswith("bert")
            and "embed" not in m.name.lower()
        ]
        if not candidates:
            raise HTTPException(status_code=503, detail="Aucun modele installe")
        if req.images:
            vision = [c for c in candidates if "vision" in c["capabilities"]]
            if vision:
                candidates = vision
                route_reason = "image jointe ? modele vision"
        elif agent and agent.tools:
            modele_dedie = agent.model
            if not modele_dedie and agent.role:
                from backend.core.hardware import get_role_model

                modele_dedie = get_role_model(agent.role)
            if modele_dedie:
                prefer = [c for c in candidates if c["tag"] == modele_dedie]
                if prefer:
                    requested_model, route_reason = modele_dedie, (
                        f"agent {agent.id} ? modele dedie"
                        if agent.model
                        else f"agent {agent.id} ? plan materiel ({agent.role})"
                    )
            tool_capable = [
                c
                for c in candidates
                if any(f in c["tag"].lower() for f in ("localai-prog", "qwen2.5", "mistral", "llama3.1", "command-r"))
                and "qwen3" not in c["tag"].lower()
            ]
            if tool_capable and requested_model == "auto":
                candidates = tool_capable
                route_reason = route_reason or "agent actif ? modele a outils natifs"
        if requested_model == "auto":
            default_model = get_setting("default_model")
            if default_model:
                pref = [c for c in candidates if c["tag"] == default_model]
                if pref:
                    requested_model, auto_reason = default_model, "modele par defaut"
            if requested_model == "auto":
                requested_model, auto_reason = route_model(req.message, candidates)
                route_reason = route_reason or auto_reason

    conversation_id = req.conversation_id
    title = None
    if conversation_id is None:
        title = req.message.strip()[:60] or "Nouvelle conversation"
        conversation_id = db.execute(
            "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?,?,?,?)",
            (title, requested_model, _now(), _now()),
        )
        auto_title_pending = True
    else:
        conv = db.query_one("SELECT id, summary FROM conversations WHERE id=?", (conversation_id,))
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation introuvable")
        auto_title_pending = False
        db.execute(
            "UPDATE conversations SET updated_at=?, model=? WHERE id=?",
            (_now(), requested_model, conversation_id),
        )

    if req.regenerate:
        last_assistant = db.query_one(
            "SELECT id FROM messages WHERE conversation_id=? AND role='assistant'"
            " ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        )
        if last_assistant:
            db.execute("DELETE FROM messages WHERE id=?", (last_assistant["id"],))
        last_user = db.query_one(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user'"
            " ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        )
        if last_user and last_user["content"] == req.message + (" [image]" if req.images else ""):
            skip_user_insert = True
        else:
            skip_user_insert = False
    else:
        skip_user_insert = False

    if not skip_user_insert:
        db.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
            (
                conversation_id,
                "user",
                req.message + (" [image]" if req.images else ""),
                _now(),
            ),
        )

    active_backend = get_active_backend()

    if agent and agent.id == "cad3d":
        recent = db.query_all(
            "SELECT content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 10",
            (conversation_id,),
        )
        history_text = "\n".join(m["content"] for m in reversed(recent))
        found_paths = re.findall(r"[A-Za-z]:[\\/][^\s\"']*", history_text)
        if not found_paths:
            async def cad_ask_folder() -> AsyncIterator[str]:
                question = (
                    "Quel est le dossier de destination pour les fichiers du projet 3D ?\n\n"
                    "(exemple : `D:\\Impression3D\\cube20`) — je génèrerai ensuite le "
                    "script OpenSCAD, le STL et le G-code dans ce dossier, en autonomie."
                )
                yield _sse(
                    {
                        "type": "meta",
                        "conversation_id": conversation_id,
                        "model": requested_model,
                        "route_reason": "agent Conception 3D — dossier requis",
                        "rag_sources": [],
                        "tools_active": True,
                    }
                )
                step = max(1, len(question) // 4)
                for i in range(0, len(question), step):
                    yield _sse({"type": "token", "content": question[i : i + step]})
                assistant_id = db.execute(
                    "INSERT INTO messages (conversation_id, role, content, model, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (conversation_id, "assistant", question, requested_model, _now()),
                )
                db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conversation_id))
                yield _sse({"type": "done", "conversation_id": conversation_id, "assistant_message_id": assistant_id})
            return StreamingResponse(cad_ask_folder(), media_type="text/event-stream")
        workdir = found_paths[-1].rstrip(".,;:)")

    memory_hit = None
    if (
        not req.images
        and not (agent and agent.id == "cad3d")
        and get_setting("auto_learn", "1") != "0"
    ):
        try:
            memory_hit = await knowledge_lookup(Embedder(active_backend), req.message)
        except Exception:
            memory_hit = None

    if memory_hit:
        async def memory_stream() -> AsyncIterator[str]:
            started_ms = int((datetime.now() - datetime.now()).total_seconds() * 1000)
            yield _sse(
                {
                    "type": "meta",
                    "conversation_id": conversation_id,
                    "model": "Mémoire interne",
                    "route_reason": f"connaissance apprise (score {memory_hit['score']})",
                    "rag_sources": [],
                    "tools_active": False,
                    "learned": True,
                }
            )
            answer = memory_hit["answer"]
            step = max(1, len(answer) // 4)
            for i in range(0, len(answer), step):
                yield _sse({"type": "token", "content": answer[i : i + step]})
            assistant_id = db.execute(
                "INSERT INTO messages (conversation_id, role, content, model,"
                " prompt_tokens, completion_tokens, duration_ms, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (conversation_id, "assistant", answer, "memoire", 0, 0, 5, _now()),
            )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (_now(), conversation_id),
            )
            yield _sse(
                {
                    "type": "done",
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_id,
                    "duration_ms": 5,
                    "learned": True,
                }
            )

        return StreamingResponse(memory_stream(), media_type="text/event-stream")

    rows = db.query_all(
        "SELECT role, content FROM messages WHERE conversation_id=? AND role IN"
        " ('user','assistant') ORDER BY id",
        (conversation_id,),
    )
    history = [{"role": r["role"], "content": r["content"]} for r in rows][-MAX_HISTORY:]
    if req.images:
        clean = [m for m in history if m["role"] == "user"]
        if clean:
            clean[-1]["images"] = [i.split(",")[-1] for i in req.images]

    memories = db.query_all("SELECT content FROM memories ORDER BY id DESC LIMIT 30")

    conv_row = db.query_one("SELECT summary FROM conversations WHERE id=?", (conversation_id,))
    summary = conv_row["summary"] if conv_row else ""

    use_rag = req.rag_enabled or bool(agent and agent.rag)
    rag_sources: list[dict] = []
    rag_block = ""
    if use_rag:
        requete_rag = req.message
        if len(req.message) < 60 and history:
            precedents = [
                m["content"]
                for m in history
                if m.get("role") == "user" and m["content"] != req.message
            ]
            if precedents:
                requete_rag = f"{precedents[-1][:200]} {req.message}"
        rag_block, rag_sources = await _search_rag(active_backend, requete_rag)

    system_content = _build_system_prompt(
        agent.system_prompt if agent else None,
        memories,
        summary,
        rag_block,
    )
    if agent and agent.id == "cad3d" and workdir:
        system_content = (
            f"### ÉTAT ACTUEL\nDossier de travail CONFIRMÉ : {workdir}. Ne demande "
            "JAMAIS le dossier. Commence IMMÉDIATEMENT par find_reference_image, "
            "puis conçois, valide et tranche dans ce dossier.\n\n"
        ) + system_content
    if system_content:
        history.insert(0, {"role": "system", "content": system_content})

    web_active = req.web_enabled or (not agent and wants_web(req.message))
    is_general = agent is None or agent.id in ("general", "auto") or not agent.tools
    agentic = bool(agent and agent.tools and not is_general)
    full_general_tools = [
        "calculator",
        "get_datetime",
        "list_dir",
        "read_file",
        "check_code",
        "deep_research",
    ]
    if agentic:
        tool_names = list(agent.tools)
        use_tools = True
    elif req.tools_enabled:
        tool_names = list(full_general_tools)
        use_tools = True
    else:
        tool_names = []
        use_tools = False
    if web_active:
        tool_names = list(dict.fromkeys(tool_names + ["web_search", "web_fetch"]))
        use_tools = True
    permissions = PermissionManager(get_setting("tool_level", "USER"))
    tool_specs = ollama_tool_specs(tool_names) if use_tools and tool_names else []

    backend = active_backend
    started = datetime.now()

    async def stream() -> AsyncIterator[str]:
        full_parts: list[str] = []
        stats_payload: dict = {}
        yield _sse(
            {
                "type": "meta",
                "conversation_id": conversation_id,
                "model": requested_model,
                "agent": agent.id if agent else "general",
                "route_reason": route_reason,
                "rag_sources": rag_sources,
                "tools_active": bool(tool_specs),
                "web_active": web_active,
            }
        )
        messages = history
        try:
            from backend.core.autotune import get_param

            max_rounds = int(get_param("max_tool_rounds", MAX_TOOL_ROUNDS))
            ctx_agent = int(get_param("ctx_agent", 8192))
            for round_index in range(max_rounds + 1):
                round_tokens: list[str] = []
                collected_stats = None
                pending_calls = None
                async for event in backend.chat_stream(
                    requested_model,
                    messages,
                    {"temperature": req.temperature, "num_ctx": ctx_agent, "think": False},
                    tools=tool_specs if round_index < max_rounds else None,
                ):
                    if event.type == "token":
                        round_tokens.append(event.content)
                        full_parts.append(event.content)
                        yield _sse({"type": "token", "content": event.content})
                    elif event.type == "done":
                        collected_stats = event.stats
                        pending_calls = event.tool_calls
                    elif event.type == "error":
                        yield _sse({"type": "error", "error": event.content})
                        return
                if collected_stats:
                    s = collected_stats
                    stats_payload = {
                        "prompt_tokens": s.prompt_tokens,
                        "completion_tokens": s.completion_tokens,
                        "total_duration_ms": s.total_duration_ms,
                        "tokens_per_second": s.tokens_per_second,
                    }
                if not pending_calls and round_tokens:
                    pending_calls = _parse_text_tool_calls("".join(round_tokens))
                if not pending_calls or round_index >= max_rounds:
                    break
                assistant_msg = {
                    "role": "assistant",
                    "content": "".join(round_tokens),
                    "tool_calls": pending_calls,
                }
                messages = messages + [assistant_msg]
                for call in pending_calls:
                    fn = call.get("function", {}) or {}
                    name = fn.get("name", "")
                    raw_args = fn.get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError:
                            args = {}
                    else:
                        args = raw_args or {}
                    result = execute_tool(name, args, permissions)
                    denied = result.startswith("PERMISSION REFUSEE")
                    yield _sse(
                        {
                            "type": "tool_exec",
                            "name": name,
                            "args": args,
                            "denied": denied,
                            "preview": result[:280],
                        }
                    )
                    try:
                        db.execute(
                            "INSERT INTO tool_traces (conversation_id, seq, tool_name,"
                            " arguments, result_preview, created_at) VALUES (?,?,?,?,?,?)",
                            (
                                conversation_id,
                                round_index,
                                name,
                                json.dumps(args, ensure_ascii=False)[:2000],
                                result[:600],
                                _now(),
                            ),
                        )
                    except Exception:
                        pass
                    messages = messages + [
                        {"role": "tool", "content": result, "tool_name": name}
                    ]

            if (
                not tool_specs
                and get_setting("reflection_mode", "off") == "on"
                and len(req.message) >= 80
                and full_parts
            ):
                yield _sse({"type": "reflection", "stage": "start"})
                try:
                    async for event in backend.chat_stream(
                        requested_model,
                        [
                            {
                                "role": "system",
                                "content": (
                                    "Tu es un relecteur rigoureux. On te donne une QUESTION "
                                    "et un BROUILLON de reponse. Verifie les affirmations, "
                                    "corrige les erreurs factuelles et de logique, supprime "
                                    "les redondances, puis renvoie UNIQUEMENT la version "
                                    "finale amelioree, sans commentaire sur ta revision."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"QUESTION :\n{req.message}\n\nBROUILLON :\n"
                                    + "".join(full_parts)
                                ),
                            },
                        ],
                        {"temperature": 0.3},
                    ):
                        if event.type == "token":
                            full_parts.append(event.content)
                            yield _sse({"type": "token", "content": event.content})
                        elif event.type == "error":
                            break
                    yield _sse({"type": "reflection", "stage": "done"})
                except Exception:
                    yield _sse({"type": "reflection", "stage": "skipped"})
            content = "".join(full_parts)
            duration_ms = int((datetime.now() - started).total_seconds() * 1000)
            assistant_id = db.execute(
                "INSERT INTO messages (conversation_id, role, content, model,"
                " prompt_tokens, completion_tokens, duration_ms, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    conversation_id,
                    "assistant",
                    content,
                    requested_model,
                    stats_payload.get("prompt_tokens", 0),
                    stats_payload.get("completion_tokens", 0),
                    duration_ms,
                    _now(),
                ),
            )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (_now(), conversation_id),
            )
            yield _sse(
                {
                    "type": "done",
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_id,
                    "duration_ms": duration_ms,
                    **stats_payload,
                }
            )
            grade = await _grade_answer(backend, req.message, content, web_active)
            if grade:
                try:
                    db.execute(
                        "UPDATE messages SET quality_score=? WHERE id=?",
                        (grade["score"], assistant_id),
                    )
                except Exception:
                    pass
                yield _sse({"type": "score", **grade})
            import asyncio

            count = db.query_one(
                "SELECT COUNT(*) AS c FROM messages WHERE conversation_id=? AND role IN ('user','assistant')",
                (conversation_id,),
            )["c"]
            if count and int(count) % 8 == 0:
                asyncio.create_task(
                    _update_summary(backend, conversation_id, requested_model)
                )
            if get_setting("auto_learn", "1") != "0":
                asyncio.create_task(
                    knowledge_remember(Embedder(backend), req.message, content)
                )
            if auto_title_pending:
                asyncio.create_task(_auto_title(backend, conversation_id, req.message))
            if get_setting("tts_followup", "0") == "1":
                import random

                phrasings = [
                    "Est-ce que cette réponse vous convient ?",
                    "Cette réponse vous satisfait-elle, ou souhaitez-vous des précisions ?",
                    "Dites-moi si cette réponse vous convient, je peux l'améliorer.",
                    "Êtes-vous satisfait de cette réponse ? Je peux approfondir si besoin.",
                ]
                yield _sse(
                    {
                        "type": "tts_followup",
                        "text": random.choice(phrasings),
                    }
                )
        except OllamaError as exc:
            yield _sse({"type": "error", "error": str(exc)})
        except Exception as exc:
            try:
                with open(settings.log_dir / "chat_err.log", "a", encoding="utf-8") as f:
                    f.write(f"[{_now()}] model={requested_model} err={exc!r}\n")
            except Exception:
                pass
            yield _sse({"type": "error", "error": f"erreur interne: {exc!r}"})

    return StreamingResponse(stream(), media_type="text/event-stream")
