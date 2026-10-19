use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Path, State},
    http::{header, StatusCode},
    response::Response,
    routing::{delete, get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/knowledge/stats", get(kstats))
        .route("/api/knowledge", delete(kclear))
        .route("/api/knowledge/search", post(ksearch))
        .route("/api/distill/stats", get(dstats))
        .route("/api/distill/export", get(dexport))
        .route("/api/distill/export-traces", get(dexport_traces))
        .route("/api/distill/export-dpo", get(dexport_dpo))
        .route("/api/distill/upgrade", post(dupgrade))
        .route("/api/prompts", get(pllist).post(plcreate))
        .route("/api/prompts/:pid", delete(pldelete))
        .route("/api/stats/usage", get(usage))
        .route("/api/conversations/:cid/export", get(export_conv))
}

async fn kstats(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.knowledge import stats as knowledge_stats; print(json.dumps(knowledge_stats()))").await?;
    Ok(Json(v))
}

async fn kclear(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.knowledge import clear as knowledge_clear; print(json.dumps({'deleted': knowledge_clear()}))").await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct KQuery {
    pub query: String,
}

async fn ksearch(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<KQuery>,
) -> ApiResult<Json<Value>> {
    // Recherche textuelle simple (le RAG vectoriel passe par /api/documents/search).
    let q = format!("%{}%", b.query.trim());
    let rows = st.db.query_all_json(
        "SELECT id, content FROM memories WHERE content LIKE ? ORDER BY id DESC LIMIT 10",
        &[&q.as_str()],
    )?;
    Ok(Json(json!({ "results": rows })))
}

async fn dstats(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.distiller import teacher_review_stats; print(json.dumps(teacher_review_stats()))").await?;
    Ok(Json(v))
}

async fn dexport(State(st): State<Arc<crate::AppState>>) -> ApiResult<Response> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.distiller import export_sft_dataset; c,n = export_sft_dataset(); print(json.dumps({'content': c or '', 'count': n}))").await?;
    let content = v.get("content").and_then(|x| x.as_str()).unwrap_or("").to_string();
    let count = v.get("count").and_then(|x| x.as_i64()).unwrap_or(0);
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/jsonl")
        .header("Content-Disposition", "attachment; filename=\"localai_sft_dataset.jsonl\"")
        .header("X-Dataset-Count", count.to_string())
        .body(Body::from(content))
        .unwrap())
}

async fn dexport_traces(State(st): State<Arc<crate::AppState>>) -> ApiResult<Response> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.distiller import export_tool_traces; c,n = export_tool_traces(); print(json.dumps({'content': c or '', 'count': n}))").await?;
    let content = v.get("content").and_then(|x| x.as_str()).unwrap_or("").to_string();
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/jsonl")
        .header("Content-Disposition", "attachment; filename=\"localai_agent_traces.jsonl\"")
        .body(Body::from(content))
        .unwrap())
}

async fn dexport_dpo(State(st): State<Arc<crate::AppState>>) -> ApiResult<Response> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core.distiller import export_dpo_pairs; c,n = export_dpo_pairs(); print(json.dumps({'content': c or '', 'count': n}))").await?;
    let content = v.get("content").and_then(|x| x.as_str()).unwrap_or("").to_string();
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/jsonl")
        .header("Content-Disposition", "attachment; filename=\"localai_dpo_pairs.jsonl\"")
        .body(Body::from(content))
        .unwrap())
}

async fn dupgrade(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "import asyncio; from backend.core.distiller import upgrade_weak_answers; from backend.inference import get_active_backend; print(json.dumps(asyncio.run(upgrade_weak_answers(get_active_backend()))))").await?;
    Ok(Json(v))
}

async fn pllist(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let rows = st.db.query_all_json(
        "SELECT id, name, content, created_at FROM prompts ORDER BY name", &[])?;
    Ok(Json(json!({ "prompts": rows })))
}

#[derive(Debug, Deserialize)]
pub struct PromptBody {
    pub name: String,
    pub content: String,
}

async fn plcreate(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<PromptBody>,
) -> ApiResult<Json<Value>> {
    let now = localai_core::db::now_iso();
    let id = st.db.execute(
        "INSERT INTO prompts (name, content, created_at) VALUES (?,?,?)",
        &[&b.name.trim(), &b.content.trim(), &now.as_str()],
    )?;
    Ok(Json(json!({ "id": id })))
}

async fn pldelete(
    State(st): State<Arc<crate::AppState>>,
    Path(pid): Path<i64>,
) -> ApiResult<Json<Value>> {
    st.db.execute("DELETE FROM prompts WHERE id=?", &[&pid])?;
    Ok(Json(json!({ "deleted": pid })))
}

async fn usage(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let totals = st.db.query_one_json(
        "SELECT COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS pt, COALESCE(SUM(completion_tokens),0) AS ct FROM messages WHERE role='assistant'", &[])?;
    let by_model = st.db.query_all_json(
        "SELECT model, COUNT(*) AS n, SUM(completion_tokens) AS tokens FROM messages WHERE role='assistant' AND model != '' GROUP BY model ORDER BY n DESC LIMIT 6", &[])?;
    let conv_count = st.db.query_one_json("SELECT COUNT(*) AS n FROM conversations", &[])?;
    let doc_count = st.db.query_one_json("SELECT COUNT(*) AS n FROM documents", &[])?;
    let since = chrono::Local::now().format("%Y-%m-%d").to_string();
    let since_7 = (chrono::Local::now() - chrono::Duration::days(7)).format("%Y-%m-%d").to_string();
    let _ = since;
    let per_day = st.db.query_all_json(
        "SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n, SUM(completion_tokens) AS tokens FROM messages WHERE role='assistant' AND created_at >= ? GROUP BY day ORDER BY day",
        &[&since_7.as_str()],
    ).unwrap_or_default();
    Ok(Json(json!({
        "assistant_messages": totals.as_ref().and_then(|t| t.get("n")).cloned().unwrap_or(json!(0)),
        "prompt_tokens": totals.as_ref().and_then(|t| t.get("pt")).cloned().unwrap_or(json!(0)),
        "completion_tokens": totals.as_ref().and_then(|t| t.get("ct")).cloned().unwrap_or(json!(0)),
        "conversations": conv_count.and_then(|t| t.get("n").cloned()).unwrap_or(json!(0)),
        "documents": doc_count.and_then(|t| t.get("n").cloned()).unwrap_or(json!(0)),
        "by_model": by_model,
        "per_day": per_day,
    })))
}

async fn export_conv(
    State(st): State<Arc<crate::AppState>>,
    Path(cid): Path<i64>,
) -> ApiResult<Response> {
    let conv = st.db.query_one_json("SELECT * FROM conversations WHERE id=?", &[&cid])?;
    let conv = conv.ok_or_else(|| ApiError(localai_core::AppError::NotFound("Conversation introuvable".into())))?;
    let msgs = st.db.query_all_json(
        "SELECT role, content, model, created_at FROM messages WHERE conversation_id=? ORDER BY id", &[&cid])?;
    let title = conv.get("title").and_then(|t| t.as_str()).unwrap_or("conversation");
    let mut md = format!("# {title}\n\n*Exporte LocalAI*\n\n");
    for m in msgs {
        let role = m.get("role").and_then(|r| r.as_str()).unwrap_or("");
        let content = m.get("content").and_then(|c| c.as_str()).unwrap_or("");
        let model = m.get("model").and_then(|x| x.as_str()).unwrap_or("auto");
        let who = if role == "user" { "**Vous**".to_string() } else { format!("**LocalAI** ({model})") };
        md.push_str(&format!("### {who}\n\n{content}\n\n"));
    }
    let safe: String = title.chars().filter(|c| c.is_alphanumeric() || " -_".contains(*c)).take(40).collect();
    let safe = if safe.is_empty() { "conversation".to_string() } else { safe };
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "text/markdown; charset=utf-8")
        .header("Content-Disposition", format!("attachment; filename=\"{safe}.md\""))
        .body(Body::from(md))
        .unwrap())
}
