import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.core.agents import list_agents
from backend.core.config import settings
from backend.inference import get_active_backend
from backend.inference.ollama import OllamaError

router = APIRouter(prefix="/api", tags=["agents"])


class BenchmarkRequest(BaseModel):
    tags: list[str] | None = None


@router.get("/agents")
async def get_agents() -> dict:
    return {"agents": list_agents()}


BENCHMARK_TESTS = [
    ("maths_1", "Calcule 17*24-89. Reponds uniquement par le nombre, sans explication.", "319"),
    ("maths_2", "Combien font 13*13 ? Reponds uniquement par le nombre.", "169"),
    ("maths_3", "Quel est le plus grand nombre premier inferieur a 20 ? Reponds par le chiffre.", "19"),
    ("maths_4", "Calcule 5+3*4. Reponds uniquement par le nombre.", "17"),
    ("maths_5", "Quel est le reste de la division entiere de 100 par 7 ? Reponds par le chiffre.", "2"),
    (
        "code_1",
        "Ecris une fonction Python fizzbuzz(n) qui affiche les nombres de 1 a n. "
        "Ton code doit contenir le mot fizzbuzz.",
        "fizzbuzz",
    ),
    (
        "code_2",
        "Ecris une fonction Python qui inverse une chaine de caracteres. "
        "Ton code doit contenir le mot def.",
        "def",
    ),
    (
        "code_3",
        "Ecris une fonction Python qui retourne la somme d'une liste de nombres. "
        "Ton code doit contenir le mot return.",
        "return",
    ),
    (
        "code_4",
        "Ecris une fonction Python qui verifie si un nombre est pair. "
        "Ton code doit contenir le mot modulo (utilise %).",
        "%",
    ),
    (
        "code_5",
        "Ecris une fonction Python qui calcule le factoriel. Ton code doit contenir le mot def.",
        "def",
    ),
    (
        "raisonnement_1",
        "Si tous les A sont B, et qu'aucun B n'est C, peut-on conclure qu'aucun A n'est C ? "
        "Reponds d'abord par oui ou par non.",
        "oui",
    ),
    (
        "raisonnement_2",
        "Si Pierre est plus grand que Paul, et Paul est plus grand que Jacques, "
        "qui est le plus petit ? Reponds par un prenom.",
        "jacques",
    ),
    (
        "raisonnement_3",
        "Un train roule a 60 km/h pendant 2 heures puis a 90 km/h pendant 1 heure. "
        "Quelle distance a-t-il parcourue au total en km ? Reponds par le nombre.",
        "210",
    ),
    (
        "raisonnement_4",
        "Si 3 pommes coutent 6 euros, combien coutent 5 pommes ? Reponds par le nombre.",
        "10",
    ),
    (
        "raisonnement_5",
        "Quel nombre vient apres 1, 2, 3, 5, 8, 13 ? Reponds par le nombre.",
        "21",
    ),
    ("francais_1", "Quel est le pluriel du mot cheval ? Reponds par un seul mot.", "chevaux"),
    ("francais_2", "Quel est le feminin du mot acteur ? Reponds par un seul mot.", "actrice"),
    ("francais_3", "Quel est le contraire du mot chaud ? Reponds par un seul mot.", "froid"),
    ("francais_4", "Quel est le synonyme du mot grand ? Reponds par un seul mot.", "haut"),
    ("francais_5", "Quel est le nombre de lettres du mot soleil ? Reponds par le chiffre.", "6"),
    ("general_1", "Quelle est la capitale de la France ? Reponds par un seul mot.", "paris"),
    ("general_2", "Combien de continents y a-t-il sur Terre ? Reponds par le nombre.", "7"),
    ("general_3", "Quel est le symbole chimique de l'eau ? Reponds en une seule formule.", "h2o"),
    ("general_4", "Combien de cotes a un triangle ? Reponds par le nombre.", "3"),
    ("general_5", "Quel animal produit le miel ? Reponds par un seul mot.", "abeille"),
]


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sans_chat(tag: str, family: str) -> str | None:
    """Renvoie une raison de non-test si le modele n'est pas testable en chat."""
    low = tag.lower()
    if any(k in low for k in ("nomic-embed", "all-minilm", "mxbai", "-embed", "embed-text")):
        return "modele d'embedding (pas de chat)"
    if any(low.startswith(p) for p in ("qwen2.5vl:", "qwen2vl:", "llava:", "qwen2.5-vl")):
        return "modele vision-only (pas de chat)"
    return None


BENCH_STATE_FILE = settings.data_dir / "bench_last.json"


def _save_bench_state(state: dict) -> None:
    try:
        BENCH_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _load_bench_state() -> dict | None:
    try:
        if BENCH_STATE_FILE.exists():
            return json.loads(BENCH_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


@router.get("/benchmark/latest")
async def latest_benchmark() -> dict:
    state = _load_bench_state()
    if state is None:
        return {"note": "Aucun benchmark persiste; lancez-en un."}
    return state


@router.post("/benchmark/run")
async def run_benchmark(req: BenchmarkRequest) -> StreamingResponse:
    backend = get_active_backend()
    installed = await backend.list_installed()
    installed_by_name = {m.name: m for m in installed}

    requested = req.tags or sorted(installed_by_name)
    tags = []
    skipped = []
    for t in requested:
        m = installed_by_name.get(t)
        if m is None:
            continue
        reason = _sans_chat(m.name, m.family)
        if reason:
            skipped.append({"tag": t, "reason": reason})
        else:
            tags.append(t)

    async def stream() -> AsyncIterator[str]:
        results = []
        run_started = datetime.now(timezone.utc).isoformat()
        _save_bench_state({"status": "running", "started": run_started, "results": [], "done": False})
        try:
            for skip in skipped:
                yield _sse({"type": "skipped", **skip})
            for tag in tags:
                model_results = []
                status = "ok"
                yield _sse({"type": "model_start", "tag": tag})
                for test_name, prompt, expected in BENCHMARK_TESTS:
                    yield _sse({"type": "test_start", "tag": tag, "test": test_name})
                    answer_parts: list[str] = []
                    stats = None
                    started = time.monotonic()
                    error = ""
                    try:
                        async for event in backend.chat_stream(
                            tag,
                            [{"role": "user", "content": prompt}],
                            {"temperature": 0.0},
                        ):
                            if event.type == "token":
                                answer_parts.append(event.content)
                            elif event.type == "done":
                                stats = event.stats
                            elif event.type == "error":
                                error = event.content
                                break
                    except OllamaError as exc:
                        error = str(exc)
                    latency_ms = int((time.monotonic() - started) * 1000)
                    answer = "".join(answer_parts)
                    correct = expected.lower() in answer.lower() and not error
                    tps = stats.tokens_per_second if stats else 0.0
                    item = {
                        "tag": tag,
                        "test": test_name,
                        "correct": correct,
                        "latency_ms": latency_ms,
                        "tokens_per_second": tps,
                        "answer_snippet": answer.strip()[:120],
                        "error": error,
                    }
                    model_results.append(item)
                    yield _sse({"type": "test_done", **item})
                    if error:
                        status = "unavailable"
                try:
                    await backend.unload(tag)
                except OllamaError:
                    pass
                correct_count = sum(1 for r in model_results if r["correct"])
                total = len(model_results) or 1
                score = round(correct_count / total * 100)
                avg_tps = round(
                    sum(r["tokens_per_second"] for r in model_results) / total, 1
                )
                avg_latency = int(sum(r["latency_ms"] for r in model_results) / total)
                if status == "unavailable":
                    score = None
                summary = {
                    "tag": tag,
                    "status": status,
                    "score_percent": score,
                    "avg_tokens_per_second": avg_tps,
                    "avg_latency_ms": avg_latency,
                }
                results.append(summary)
                yield _sse({"type": "model_done", **summary})
                _save_bench_state({
                    "status": "running",
                    "started": run_started,
                    "finished": datetime.now(timezone.utc).isoformat(),
                    "results": list(results),
                    "done": False,
                })
            yield _sse({"type": "done", "results": results})
        except GeneratorExit:
            raise
        finally:
            _save_bench_state({
                "status": "ok",
                "started": run_started,
                "finished": datetime.now(timezone.utc).isoformat(),
                "results": list(results),
                "done": True,
            })

    return StreamingResponse(stream(), media_type="text/event-stream")
