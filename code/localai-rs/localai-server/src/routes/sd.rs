use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Query, State},
    http::{header, StatusCode},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::Value;

use crate::error::{ApiError, ApiResult};
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/sd/status", get(status))
        .route("/api/sd/install", post(install))
        .route("/api/sd/generate", post(generate))
        .route("/api/sd/image-to-3d", post(image_to_3d))
        .route("/api/sd/file", get(file))
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.sdgen import status; print(json.dumps(status()))").await?;
    Ok(Json(v))
}

async fn install(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.sdgen import install; print(json.dumps(install()))").await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize, Default)]
pub struct GenBody {
    pub prompt: String,
    pub negatif: Option<String>,
    pub largeur: Option<i32>,
    pub hauteur: Option<i32>,
    pub steps: Option<i32>,
    pub graine: Option<i64>,
}

async fn generate(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<GenBody>,
) -> ApiResult<Json<Value>> {
    if b.prompt.trim().is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("prompt vide".into())));
    }
    let p = b.prompt.replace('\'', "");
    let n = b.negatif.unwrap_or_default().replace('\'', "");
    let steps = b.steps.unwrap_or(20).clamp(1, 100);
    // Sortie horodatee comme le Python (data/img_gen_<ts>.png).
    let ts = chrono::Local::now().format("%Y%m%d%H%M%S").to_string();
    let out = format!("data/img_gen_{ts}.png");
    let larg = b.largeur.unwrap_or(512);
    let haut = b.hauteur.unwrap_or(512);
    let graine = b.graine.unwrap_or(-1);
    let code = format!(
        "from backend.core.sdgen import generer; m = generer('{}', '{}', '{}', {}, {}, {}, {}); import os; ok = os.path.exists('{}'); print(json.dumps({{'ok': ok, 'message': m, 'path': '{}' if ok else '', 'url': '/api/sd/file?path={}' if ok else ''}}))",
        p, out, n, larg, haut, steps, graine, out, out, out);
    let v = pybridge::py_json_timeout(
        &st.settings.root_dir, &code, std::time::Duration::from_secs(900)).await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize, Default)]
pub struct Img3DBody {
    pub image_path: String,
    pub output_stl: Option<String>,
    pub height_mm: Option<f64>,
    pub max_faces: Option<i64>,
}

async fn image_to_3d(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<Img3DBody>,
) -> ApiResult<Json<Value>> {
    // TripoSR local via l'executor Python (peut durer 10+ min).
    let img = b.image_path.replace('\'', "");
    let stl = b.output_stl.unwrap_or_default().replace('\'', "");
    let h = b.height_mm.unwrap_or(80.0);
    let mf = b.max_faces.unwrap_or(0);
    let code = format!(
        "from backend.tools.executor import tool_generate_mesh_from_image; import os; m = tool_generate_mesh_from_image(r'{}', r'{}', {}, {}); s = r'{}'; ok = bool(s) and os.path.exists(s); print(json.dumps({{'ok': ok, 'message': m, 'path': s}}))",
        img, stl, h, mf, stl);
    let v = pybridge::py_json_timeout(
        &st.settings.root_dir, &code, std::time::Duration::from_secs(1800)).await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct FileQuery {
    pub path: String,
}

async fn file(
    State(st): State<Arc<crate::AppState>>,
    Query(q): Query<FileQuery>,
) -> ApiResult<Response> {
    // Garde-fou : uniquement sous root_dir/data (anti path-traversal).
    let root = st.settings.root_dir.canonicalize()
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let cand = root.join(q.path.trim_start_matches(['/', '\\'].as_slice()));
    let canon = cand.canonicalize()
        .map_err(|_| ApiError(localai_core::AppError::NotFound("fichier introuvable".into())))?;
    if !canon.starts_with(&root) || !canon.is_file() {
        return Err(ApiError(localai_core::AppError::NotFound("fichier introuvable".into())));
    }
    let bytes = std::fs::read(&canon)
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let mime = match canon.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase().as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "glb" => "model/gltf-binary",
        "obj" => "text/plain",
        "stl" => "model/stl",
        _ => "application/octet-stream",
    };
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, mime)
        .body(Body::from(bytes))
        .unwrap())
}
