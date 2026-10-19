import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.core.orchestrator import WORKFLOWS, run_workflow_stream

router = APIRouter(prefix="/api/collab", tags=["collab"])


class CollabRequest(BaseModel):
    task: str = Field(min_length=1)
    workflow: str = "brainstorm"
    model: str = "auto"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/workflows")
async def list_workflows() -> dict:
    return {
        "workflows": [
            {"id": w.id, "name": w.name, "description": w.description, "stages": len(w.stages)}
            for w in WORKFLOWS.values()
        ]
    }


@router.post("/run")
async def run_collab(req: CollabRequest) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        async for event in run_workflow_stream(
            req.workflow, req.task.strip(), req.model, req.temperature
        ):
            yield _sse(event)

    return StreamingResponse(stream(), media_type="text/event-stream")
