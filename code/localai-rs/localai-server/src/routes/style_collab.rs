use std::convert::Infallible;
use std::sync::Arc;

use axum::{
    extract::State,
    response::sse::{Event, KeepAlive, Sse},
    routing::{get, post},
    Json, Router,
};
use futures_util::{Stream, StreamExt};
use serde::Deserialize;
use serde_json::Value;
use tokio::sync::mpsc;

use crate::error::{ApiError, ApiResult};
use crate::pybridge;
use crate::AppState;

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
) -> ApiResult<Sse<impl Stream<Item = Result<Event, Infallible>>>> {
    if b.task.trim().is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("tache vide".into())));
    }
    let wf = b.workflow.unwrap_or_else(|| "brainstorm".into());
    let mut model = b.model.unwrap_or_default();
    if model.trim().is_empty() || model.trim() == "auto" {
        model = st
            .db
            .get_setting("model_rapide", "localai-prog-v9.2-q5km:latest")
            .to_string();
    }
    let temp = b.temperature.unwrap_or(0.7).clamp(0.0, 2.0);

    let root = st.settings.root_dir.clone();
    let py = root.join(".venv").join("Scripts").join("python.exe");
    if !py.is_file() {
        return Err(ApiError(localai_core::AppError::Internal("venv python absent".into())));
    }
    let code = format!(
        "import asyncio, json, os\nfrom backend.core.orchestrator import run_workflow_stream\n\
WF = os.environ.get('NEO_COLLAB_WF', 'brainstorm')\n\
TASK = os.environ.get('NEO_COLLAB_TASK', '')\n\
MODEL = os.environ.get('NEO_COLLAB_MODEL', '')\n\
async def main():\n    \
async for ev in run_workflow_stream(WF, TASK, MODEL, {temp}):\n        \
print(json.dumps(ev, default=str), flush=True)\n\
asyncio.run(main())"
    );

    let mut child = tokio::process::Command::new(&py)
        .args(["-u", "-c", &code])
        .env("NEO_COLLAB_WF", &wf)
        .env("NEO_COLLAB_TASK", b.task.trim())
        .env("NEO_COLLAB_MODEL", model.trim())
        .current_dir(&root)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|e| {
            ApiError(localai_core::AppError::Internal(format!("lancement orchestrateur: {e}")))
        })?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| ApiError(localai_core::AppError::Internal("stdout indisponible".into())))?;

    let (tx, rx) = mpsc::unbounded_channel::<String>();
    tokio::spawn(async move {
        use tokio::io::{AsyncBufReadExt, BufReader};
        let mut lignes = BufReader::new(stdout).lines();
        while let Ok(Some(ligne)) = lignes.next_line().await {
            if ligne.trim().is_empty() {
                continue;
            }
            if tx.send(ligne).is_err() {
                break;
            }
        }
        let _ = child.wait().await;
    });

    let flux = tokio_stream::wrappers::UnboundedReceiverStream::new(rx)
        .map(|ligne| Ok::<_, Infallible>(Event::default().data(ligne)));
    Ok(Sse::new(flux).keep_alive(KeepAlive::default()))
}
