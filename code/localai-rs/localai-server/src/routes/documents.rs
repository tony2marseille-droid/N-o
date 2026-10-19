use std::sync::Arc;

use axum::{
    extract::{Multipart, Path, State},
    routing::{delete, get, post},
    Json, Router,
};
use localai_core::db::now_iso;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/documents", get(list))
        .route("/api/documents/upload", post(upload))
        .route("/api/documents/search", post(search))
        .route("/api/documents/:id", delete(remove))
}

const MAX_FILE_SIZE: usize = 30 * 1024 * 1024;
const EMBED_MODEL: &str = "nomic-embed-text:latest";

async fn list(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let rows = st.db.query_all_json(
        "SELECT id, filename, size_bytes, chunk_count, created_at FROM documents ORDER BY id DESC",
        &[],
    )?;
    Ok(Json(json!({ "documents": rows })))
}

fn unpack_f32le(blob: &[u8]) -> Vec<f32> {
    let mut out = Vec::with_capacity(blob.len() / 4);
    let mut i = 0;
    while i + 4 <= blob.len() {
        out.push(f32::from_le_bytes([blob[i], blob[i + 1], blob[i + 2], blob[i + 3]]));
        i += 4;
    }
    out
}

fn pack_f32le(v: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(v.len() * 4);
    for f in v {
        out.extend_from_slice(&f.to_le_bytes());
    }
    out
}

fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let (mut dot, mut na, mut nb) = (0.0f64, 0.0f64, 0.0f64);
    for (x, y) in a.iter().zip(b.iter()) {
        dot += (*x as f64) * (*y as f64);
        na += (*x as f64) * (*x as f64);
        nb += (*y as f64) * (*y as f64);
    }
    if na == 0.0 || nb == 0.0 {
        return 0.0;
    }
    (dot / (na.sqrt() * nb.sqrt())) as f32
}

fn chunk_text(text: &str) -> Vec<String> {
    // Fenetres de 800 caracteres avec recouvrement de 100 (proche du Python).
    const WIN: usize = 800;
    const OVERLAP: usize = 100;
    let chars: Vec<char> = text.chars().collect();
    if chars.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut start = 0;
    while start < chars.len() {
        let end = (start + WIN).min(chars.len());
        let s: String = chars[start..end].iter().collect();
        let s = s.trim().to_string();
        if !s.is_empty() {
            out.push(s);
        }
        if end == chars.len() {
            break;
        }
        start = end.saturating_sub(OVERLAP);
    }
    out
}

async fn extract_via_python(
    root: &std::path::Path,
    path: &std::path::Path,
    suffix: &str,
) -> Result<String, ApiError> {
    let py = root.join(".venv").join("Scripts").join("python.exe");
    let code = match suffix {
        ".pdf" => "from pypdf import PdfReader; import sys; r=PdfReader(sys.argv[1]); print('\\n'.join((p.extract_text() or '') for p in r.pages))",
        ".docx" => "from docx import Document; import sys; d=Document(sys.argv[1]); print('\\n'.join(p.text for p in d.paragraphs))",
        ".xlsx" | ".xlsm" => "from openpyxl import load_workbook; import sys; wb=load_workbook(sys.argv[1], read_only=True, data_only=True); print('\\n'.join(' '.join(str(c.value) for c in row if c.value) for ws in wb.worksheets for row in ws.rows))",
        _ => return Err(ApiError(localai_core::AppError::BadRequest(format!("Format non supporte: {suffix}")))),
    };
    let out = tokio::process::Command::new(&py)
        .args(["-c", code, &path.to_string_lossy()])
        .current_dir(root)
        .output()
        .await
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("extraction: {e}"))))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(ApiError(localai_core::AppError::Internal(format!("extraction {suffix}: {err}"))));
    }
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

async fn upload(
    State(st): State<Arc<crate::AppState>>,
    mut mp: Multipart,
) -> ApiResult<Json<Value>> {
    let upload_dir = st.settings.data_dir.join("documents");
    let _ = std::fs::create_dir_all(&upload_dir);
    // Un seul fichier par requete (comme le Python : UploadFile unique).
    if let Some(field) = mp
        .next_field()
        .await
        .map_err(|e| ApiError(localai_core::AppError::BadRequest(e.to_string())))?
    {
        let filename = field.file_name().unwrap_or("document.txt").to_string();
        let suffix = std::path::Path::new(&filename)
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| format!(".{e}").to_lowercase())
            .unwrap_or_default();
        const ALLOWED: &[&str] = &[".pdf", ".docx", ".xlsx", ".xlsm", ".txt", ".md", ".csv", ".json", ".py", ".log"];
        if !ALLOWED.contains(&suffix.as_str()) {
            return Err(ApiError(localai_core::AppError::BadRequest(format!("Format non supporte: {suffix}"))));
        }
        let raw = field
            .bytes()
            .await
            .map_err(|e| ApiError(localai_core::AppError::BadRequest(e.to_string())))?;
        if raw.len() > MAX_FILE_SIZE {
            return Err(ApiError(localai_core::AppError::BadRequest("Fichier trop volumineux (max 30 Mo)".into())));
        }
        let text = match suffix.as_str() {
            ".pdf" | ".docx" | ".xlsx" | ".xlsm" => {
                let tmp = upload_dir.join(format!("tmp_{}.{}", chrono::Local::now().format("%Y%m%d%H%M%S%f"), &suffix[1..]));
                std::fs::write(&tmp, &raw)
                    .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
                let t = extract_via_python(&st.settings.root_dir, &tmp, &suffix).await;
                let _ = std::fs::remove_file(&tmp);
                t?
            }
            _ => String::from_utf8_lossy(&raw).to_string(),
        };
        let chunks = chunk_text(&text);
        if chunks.is_empty() {
            return Err(ApiError(localai_core::AppError::BadRequest("Aucun texte extractible".into())));
        }
        // Embeddings par lots de 16.
        let mut vectors: Vec<Vec<f32>> = Vec::new();
        for batch in chunks.chunks(16) {
            let vs = st
                .ollama
                .embed(EMBED_MODEL, batch.iter().map(|s| s.to_string()).collect())
                .await
                .map_err(|e| ApiError(localai_core::AppError::Ollama(format!("Embedding impossible: {e}"))))?;
            vectors.extend(vs);
        }
        let now = now_iso();
        let doc_id = st.db.execute(
            "INSERT INTO documents (filename, size_bytes, chunk_count, created_at) VALUES (?,?,?,?)",
            &[&filename.as_str(), &(raw.len() as i64), &(chunks.len() as i64), &now.as_str()],
        )?;
        for (pos, (content, vec)) in chunks.iter().zip(vectors.iter()).enumerate() {
            let blob = pack_f32le(vec);
            // rusqlite ToSql pour &[u8] via Value Blob.
            let blob_val = rusqlite::types::Value::Blob(blob);
            st.db.execute(
                "INSERT INTO document_chunks (document_id, position, content, embedding) VALUES (?,?,?,?)",
                &[&doc_id, &(pos as i64), &content.as_str(), &blob_val],
            )?;
        }
        let final_path = upload_dir.join(format!("doc_{doc_id}{suffix}"));
        std::fs::write(&final_path, &raw)
            .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
        return Ok(Json(json!({ "id": doc_id, "filename": filename, "chunks": chunks.len() })));
    }
    Err(ApiError(localai_core::AppError::BadRequest("aucun fichier".into())))
}

async fn remove(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    st.db.execute("DELETE FROM document_chunks WHERE document_id=?", &[&id])?;
    st.db.execute("DELETE FROM documents WHERE id=?", &[&id])?;
    let upload_dir = st.settings.data_dir.join("documents");
    if let Ok(entries) = std::fs::read_dir(&upload_dir) {
        for e in entries.flatten() {
            if let Some(n) = e.file_name().to_str() {
                if n.starts_with(&format!("doc_{id}.")) {
                    let _ = std::fs::remove_file(e.path());
                }
            }
        }
    }
    Ok(Json(json!({ "deleted": id })))
}

#[derive(Debug, Deserialize)]
pub struct SearchBody {
    pub query: String,
    pub top_k: Option<i64>,
}

async fn search(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<SearchBody>,
) -> ApiResult<Json<Value>> {
    let k = b.top_k.unwrap_or(5).clamp(1, 20) as usize;
    let vecs = st
        .ollama
        .embed(EMBED_MODEL, vec![b.query.clone()])
        .await
        .map_err(|e| ApiError(localai_core::AppError::Ollama(e.to_string())))?;
    let q = vecs.into_iter().next().unwrap_or_default();
    // Lecture brute des blobs (rusqlite ne rend pas le BLOB en JSON).
    // On passe par une connexion directe ici pour garder les octets.
    let db_path = st.settings.db_path();
    let conn = rusqlite::Connection::open(&db_path)
        .map_err(localai_core::AppError::from)?;
    let mut stmt = conn
        .prepare("SELECT c.document_id, d.filename, c.position, c.content, c.embedding FROM document_chunks c JOIN documents d ON d.id = c.document_id")
        .map_err(localai_core::AppError::from)?;
    let mut scored: Vec<(f32, i64, String, i64, String)> = Vec::new();
    let rows = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, Vec<u8>>(4).unwrap_or_default(),
            ))
        })
        .map_err(localai_core::AppError::from)?;
    for r in rows.flatten() {
        if r.4.is_empty() {
            continue;
        }
        let v = unpack_f32le(&r.4);
        scored.push((cosine(&q, &v), r.0, r.1, r.2, r.3));
    }
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    let results: Vec<Value> = scored
        .into_iter()
        .take(k)
        .map(|(s, doc_id, filename, position, content)| {
            json!({ "document_id": doc_id, "filename": filename, "position": position,
                    "content": content, "score": (s * 10000.0).round() / 10000.0 })
        })
        .collect();
    Ok(Json(json!({ "results": results })))
}

/// Bloc de contexte RAG pour le chat : recherche + formatte ( reutilise par chat.rs ).
pub(crate) async fn rag_block(
    db_path: &std::path::Path,
    ollama: &localai_core::ollama::Ollama,
    query: &str,
    k: usize,
) -> String {
    let vecs = match ollama.embed(EMBED_MODEL, vec![query.to_string()]).await {
        Ok(v) => v,
        Err(_) => return String::new(),
    };
    let q = vecs.into_iter().next().unwrap_or_default();
    if q.is_empty() {
        return String::new();
    }
    let conn = match rusqlite::Connection::open(db_path) {
        Ok(c) => c,
        Err(_) => return String::new(),
    };
    let mut stmt = match conn.prepare(
        "SELECT d.filename, c.position, c.content, c.embedding FROM document_chunks c JOIN documents d ON d.id = c.document_id") {
        Ok(s) => s,
        Err(_) => return String::new(),
    };
    let mut scored: Vec<(f32, String, String)> = Vec::new();
    if let Ok(rows) = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(2)?,
            row.get::<_, Vec<u8>>(3).unwrap_or_default(),
        ))
    }) {
        for r in rows.flatten() {
            if r.2.is_empty() {
                continue;
            }
            let s = cosine(&q, &unpack_f32le(&r.2));
            if s >= 0.05 {
                scored.push((s, r.0, r.1));
            }
        }
    }
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    let top: Vec<String> = scored
        .into_iter()
        .take(k)
        .map(|(s, f, c)| format!("[Source: {f} (score {s:.2})]\n{c}"))
        .collect();
    if top.is_empty() {
        return String::new();
    }
    format!("Contexte documentaire extrait des documents de l'utilisateur:\n\n{}", top.join("\n\n---\n\n"))
}
