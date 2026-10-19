//! Pont vers le backend Python pour la logique ML/fichiers complexe.
//! Le Rust possede le HTTP, le Python execute la fonction et rend du JSON.
//! Timeouts stricts : un Python bloque ne doit jamais figer une requete HTTP.
use std::path::Path;
use std::time::Duration;

use crate::error::{ApiError, ApiResult};

/// Timeout par defaut : 5 min (whisper/SD peuvent etre lents au 1er appel).
pub const PY_TIMEOUT: Duration = Duration::from_secs(300);

async fn run_with_timeout(
    root: &Path,
    args: &[&str],
    timeout: Duration,
) -> ApiResult<std::process::Output> {
    let py = root.join(".venv").join("Scripts").join("python.exe");
    if !py.is_file() {
        return Err(ApiError(localai_core::AppError::Internal(
            "venv python absent (pont coupe)".into(),
        )));
    }
    let mut cmd = tokio::process::Command::new(&py);
    cmd.args(args).current_dir(root);
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    // Tue l'enfant si on abandonne l'attente (timeout) : pas de Python orphelin.
    cmd.kill_on_drop(true);
    let child = cmd.spawn().map_err(|e| {
        ApiError(localai_core::AppError::Internal(format!("pont spawn: {e}")))
    })?;
    match tokio::time::timeout(timeout, child.wait_with_output()).await {
        Ok(Ok(out)) => Ok(out),
        Ok(Err(e)) => Err(ApiError(localai_core::AppError::Internal(format!(
            "pont io: {e}"
        )))),
        Err(_) => Err(ApiError(localai_core::AppError::Internal(format!(
            "pont python : delai depasse ({} s), process tue",
            timeout.as_secs()
        )))),
    }
}

pub async fn py_json(root: &Path, code: &str) -> ApiResult<serde_json::Value> {
    py_json_timeout(root, code, PY_TIMEOUT).await
}

pub async fn py_json_timeout(
    root: &Path,
    code: &str,
    timeout: Duration,
) -> ApiResult<serde_json::Value> {
    let wrapped = format!("import json\n{code}");
    let out = run_with_timeout(root, &["-c", &wrapped], timeout).await?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let err: String = err.chars().take(300).collect();
        return Err(ApiError(localai_core::AppError::Internal(format!(
            "pont python: {err}"
        ))));
    }
    serde_json::from_slice(&out.stdout).map_err(|e| {
        ApiError(localai_core::AppError::Internal(format!("pont JSON: {e}")))
    })
}

pub async fn py_text_timeout(
    root: &Path,
    args: &[&str],
    timeout: std::time::Duration,
) -> ApiResult<String> {
    let out = run_with_timeout(root, args, timeout).await?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        let err: String = err.chars().take(300).collect();
        return Err(ApiError(localai_core::AppError::Internal(format!(
            "pont python: {err}"
        ))));
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}
