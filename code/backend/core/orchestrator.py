"""Orchestrateur multi-agents : plusieurs IA collaborent sur une tâche."""
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from backend.inference import get_active_backend


@dataclass
class Stage:
    agent: str
    label: str
    instruction: str


@dataclass
class Workflow:
    id: str
    name: str
    description: str
    stages: list[Stage] = field(default_factory=list)


WORKFLOWS: dict[str, Workflow] = {
    "brainstorm": Workflow(
        id="brainstorm",
        name="Brainstorming",
        description="Trois perspectives proposent des idées, puis une synthèse fusionne le meilleur.",
        stages=[
            Stage("pragmatique", "Perspective pragmatique",
                  "Tu es un ingénieur pragmatique. Propose 3 idées concrètes et réalisables pour la tâche suivante. Format: liste courte."),
            Stage("creatif", "Perspective créative",
                  "Tu es un créatif hors des sentiers battus. Propose 3 idées originales et audacieuses pour la tâche suivante. Format: liste courte."),
            Stage("critique", "Perspective critique",
                  "Tu es un analyste exigeant. Identifie les risques, pièges et points de vigilance de la tâche suivante. Format: liste courte."),
            Stage("synthese", "Synthèse finale",
                  "Tu es un chef de projet. Fusionne les contributions précédentes en un plan d'action unique, clair et priorisé (max 10 lignes)."),
        ],
    ),
    "review": Workflow(
        id="review",
        name="Revue de code",
        description="Un auteur écrit, un critique relit, un réviseur applique les corrections.",
        stages=[
            Stage("auteur", "Rédaction",
                  "Tu es un développeur senior. Écris le code demandé, propre et commenté. Réponds avec le code uniquement."),
            Stage("critique", "Critique",
                  "Tu es un relecteur de code impitoyable mais constructif. Liste les bugs, failles et améliorations du code fourni. Max 8 points."),
            Stage("reviseur", "Version corrigée",
                  "Tu es un développeur senior. Applique toutes les corrections listées et produis la version finale du code. Code seul."),
        ],
    ),
    "debate": Workflow(
        id="debate",
        name="Débat contradictoire",
        description="Pour, contre, puis arbitrage motivé.",
        stages=[
            Stage("pour", "Camp pour",
                  "Tu défends avec conviction les avantages de la proposition suivante. 3 arguments forts max."),
            Stage("contre", "Camp contre",
                  "Tu attaques avec rigueur les faiblesses de la proposition suivante. 3 arguments forts max."),
            Stage("arbitre", "Arbitrage",
                  "Tu es un arbitre neutre et lucide. Compare les deux camps et rends un verdict nuancé en 5 lignes max."),
        ],
    ),
}


def _transcript(history: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"--- {agent} ---\n{text}" for agent, text in history)


async def run_workflow_stream(
    workflow_id: str,
    task: str,
    model: str,
    temperature: float = 0.7,
) -> AsyncIterator[dict]:
    """Exécute un workflow multi-agents, émet des events SSE-serializables."""
    wf = WORKFLOWS.get(workflow_id)
    if wf is None:
        yield {"type": "error", "error": f"Workflow inconnu: {workflow_id}"}
        return
    backend = get_active_backend()
    history: list[tuple[str, str]] = []
    started = time.time()
    yield {"type": "meta", "workflow": wf.id, "name": wf.name, "stages": len(wf.stages)}
    for stage in wf.stages:
        yield {"type": "stage", "agent": stage.agent, "label": stage.label}
        context = (
            f"Tache utilisateur:\n{task}\n\n"
            if not history
            else f"Tache utilisateur:\n{task}\n\nContributions precedentes des autres agents:\n{_transcript(history)}\n\n"
        )
        prompt = context + "\nTa mission:\n" + stage.instruction
        parts: list[str] = []
        try:
            async for event in backend.chat_stream(
                model,
                [{"role": "user", "content": prompt}],
                {"temperature": temperature},
            ):
                if event.type == "token":
                    parts.append(event.content)
                    yield {"type": "stage_token", "agent": stage.agent, "content": event.content}
                elif event.type == "error":
                    yield {"type": "error", "error": event.content}
                    return
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "error": str(exc)}
            return
        text = "".join(parts).strip()[:4000]
        history.append((stage.agent, text))
        yield {"type": "stage_done", "agent": stage.agent, "chars": len(text)}
    final_agent, final_text = history[-1]
    yield {
        "type": "done",
        "result": final_text,
        "duration_s": round(time.time() - started, 1),
        "stages_completed": len(history),
    }
