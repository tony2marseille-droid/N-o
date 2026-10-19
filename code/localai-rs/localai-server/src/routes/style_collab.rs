use std::sync::Arc;

use axum::{extract::State, routing::{get, post}, Json, Router};
use serde::Deserialize;
use serde_json::Value;

use crate::error::{ApiError, ApiResult};
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/style", get(status))
        .route("/api/style/refresh", post(refresh))
        .route("/api/style/reset", post(reset))
        .route("/api/collab/workflows", get(workflows))
        .route("/api/collab/run", post(run))
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.style import style_enabled, get_style_profile; print(json.dumps({'enabled': style_enabled(), 'profile': get_style_profile()}))").await?;
    Ok(Json(v))
}

async fn refresh(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    // Analyse longue (40 echantillons + LLM) : lancee en detache, retour immediat.
    let py = st.settings.root_dir.join(".venv").join("Scripts").join("python.exe");
    let code = "import asyncio; from backend.core.style import analyze_style; from backend.inference import get_active_backend; print(asyncio.run(analyze_style(get_active_backend())))";
    let _ = tokio::process::Command::new(&py)
        .args(["-c", code])
        .current_dir(&st.settings.root_dir)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    Ok(Json(serde_json::json!({ "ok": true, "started": true })))
}

async fn reset(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.style import save_style_profile, get_style_profile; save_style_profile({}); print(json.dumps({'ok': True, 'profile': get_style_profile()}))").await?;
    Ok(Json(v))
}

async fn workflows(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.orchestrator import WORKFLOWS; print(json.dumps({'workflows': [{'id': w.id, 'name': w.name, 'description': w.description, 'stages': len(w.stages)} for w in WORKFLOWS.values()]}))").await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct RunBody {
    pub task: String,
    pub workflow: Option<String>,
    pub model: Option<String>,
    pub temperature: Option<f32>,
}

async fn run(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<RunBody>,
) -> ApiResult<Json<Value>> {
    // Le stream SSE multi-etapes reste Python (orchestrateur) ; ici on collecte.
    // V1 : execution synchrone bornee (les workflows courts repondent vite).
    if b.task.trim().is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("tache vide".into())));
    }
    let wf = b.workflow.unwrap_or_else(|| "brainstorm".into()).replace('\'', "");
    let task = b.task.replace('\'', "");
    let model = b
        .model
        .unwrap_or_else(|| "localai-prog-latest".into())
        .replace('\'', "");
    let temp = b.temperature.unwrap_or(0.7).clamp(0.0, 2.0);
    let code = format!(
        "import asyncio, json\nfrom backend.core.orchestrator import run_workflow_stream\nevents = []\nasync def main():\n    async for ev in run_workflow_stream('{}', '{}', '{}', {}):\n        events.append(ev)\nasyncio.run(main())\nprint(json.dumps({{'events': events}}))",
        wf, task, model, temp);
    let v = pybridge::py_json_timeout(
        &st.settings.root_dir,
        &code,
        std::time::Duration::from_secs(600),
    )
    .await?;
    Ok(Json(v))
}
