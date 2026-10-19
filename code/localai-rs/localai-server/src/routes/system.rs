use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use serde_json::Value;

use crate::error::ApiResult;
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/system/info", get(info))
        .route("/api/system/gpu", get(gpu))
        .route("/api/system/bridges", get(bridges))
}

async fn info(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    // Contrat exact : on appelle le handler Python lui-meme.
    let v = pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.api.routes_system import system_info; print(json.dumps(asyncio.run(system_info()), default=str))").await?;
    Ok(Json(v))
}

async fn gpu() -> ApiResult<Json<Value>> {
    let out = tokio::process::Command::new("nvidia-smi")
        .args(["--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
               "--format=csv,noheader"])
        .output()
        .await
        .map_err(|e| crate::error::ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    Ok(Json(serde_json::json!({ "nvidia_smi": text })))
}

/// Sante des ponts Python (venv + modules critiques). 503 si le venv est casse.
async fn bridges(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let py = st.settings.root_dir.join(".venv").join("Scripts").join("python.exe");
    let mut out = serde_json::json!({
        "venv_python": py.is_file(),
        "torch_cuda": false,
        "modules": {},
    });
    if !py.is_file() {
        return Ok(Json(out));
    }
    // Un seul appel Python pour tout (rapide, timeout court).
    // NOTE : pas de `import json` ici (le pont le prepend) et indentation
    // Python stricte (pas d'espaces parasites) — une seule ligne Rust.
    let code = "r = {'torch_cuda': False, 'modules': {}}\ntry:\n import torch\n r['torch_cuda'] = bool(torch.cuda.is_available())\nexcept Exception as e:\n r['torch_error'] = str(e)[:120]\nfor m in ['backend.tts.engine', 'backend.core.stt', 'backend.core.sdgen', 'backend.tools.executor', 'pypdf', 'docx', 'openpyxl', 'pyttsx3']:\n try:\n  __import__(m)\n  r['modules'][m] = True\n except Exception as e:\n  r['modules'][m] = str(e)[:120]\nprint(json.dumps(r))";
    match crate::pybridge::py_json_timeout(
        &st.settings.root_dir,
        code,
        std::time::Duration::from_secs(120),
    )
    .await
    {
        Ok(v) => {
            if let Some(o) = v.as_object() {
                let mut map = out.as_object().cloned().unwrap_or_default();
                for (k, val) in o {
                    map.insert(k.clone(), val.clone());
                }
                out = Value::Object(map);
            }
        }
        Err(e) => {
            out["pont_error"] = serde_json::Value::from(e.0.message());
        }
    }
    Ok(Json(out))
}
