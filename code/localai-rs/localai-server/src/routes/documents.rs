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
        .route("/api/documents/fiches", get(fiches))
        .route("/api/documents/classify", post(classify))
        .route("/api/documents/describe", post(describe))
        .route("/api/documents/apprentissage", post(apprentissage))
        .route("/api/documents/apprentissage/status", get(apprentissage_status))
        .route("/api/documents/:id", delete(remove))
        .route("/api/documents/neo-files", get(neo_files))
}

const MAX_FILE_SIZE: usize = 30 * 1024 * 1024;
const EMBED_MODEL: &str = "nomic-embed-text:latest";

const CATEGORIES: &[&str] = &[
    "programmation", "ia", "impression3d", "bricolage",
    "cuisine", "sante", "finances", "loisirs", "science", "autre",
];

static CATEGORY_OK: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn ensure_category(st: &Arc<crate::AppState>) {
    if CATEGORY_OK.load(std::sync::atomic::Ordering::Relaxed) {
        return;
    }
    let _ = st.db.execute("ALTER TABLE documents ADD COLUMN category TEXT DEFAULT ''", &[]);
    let _ = st.db.execute("ALTER TABLE documents ADD COLUMN description TEXT DEFAULT ''", &[]);
    // Colonne system : 1 = document d'entrainement / fichier du logiciel ->
    // invisible dans l'UI (consigne : jamais supprime, juste masque).
    let _ = st.db.execute("ALTER TABLE documents ADD COLUMN system INTEGER DEFAULT 0", &[]);
    marquer_docs_systeme(st);
    CATEGORY_OK.store(true, std::sync::atomic::Ordering::Relaxed);
}

/// Marque comme systeme les documents d'entrainement de Nco et les
/// fichiers sources du logiciel (restent en base/disque pour le RAG et
/// l'apprentissage, mais n'apparaissent plus dans l'interface).
/// Les lecons apprises (apprentissage_*) et les notes personnelles de
/// l'utilisateur RESTENT visibles.
fn marquer_docs_systeme(st: &Arc<crate::AppState>) {
    let fichiers = [
        // Corpus / fiches d'entrainement
        "sft.txt", "sft_rlvr.txt", "dpo_pairs.txt", "agent_traces.txt",
        "_rlvr_full.py", "_corpus_prog.py", "train_lora.py", "lora_prep.py",
        "superviseur_train.py", "pipeline.py", "gen_corpus_v7.py", "gen_corpus_v92.py",
        "autobench.py", "score_corpus.py", "score_corpus2.py", "score_corpus3.py",
        "promote.py", "post_train_v9.py", "post_train_v7.py", "post_train_v8.py",
        "verif_v92.py", "smoke_latest.py", "test_teacher.py", "test_teacher2.py",
        "check_corpus.py", "check_dataset_v9.py", "compare_eval_v9.py",
        "eval_battery_v9.py", "gen_amelioration_v9.py", "dump_corpus.py", "dump_smoke.py",
        "collect_stats.py", "api_test_v6.json", "tags.json",
        // Fichiers sources du logiciel
        "main.py", "engine.py", "executor.py", "__init__.py", "ollama.py",
        "llamacpp.py", "base.py", "vector_store.py", "security.py", "sdgen.py",
        "router.py", "rlvr.py", "rag.py", "orchestrator.py", "knowledge.py",
        "interests.py", "hardware.py", "documents.py", "distiller.py",
        "database.py", "config.py", "catalog.py", "autotune.py", "agents.py",
        "schemas.py", "run_dev.py", "launcher.py", "requirements.txt",
        "requirements-dev.txt", "package.json", "package-lock.json",
        "tsconfig.json", "tsconfig.node.json", "tsconfig.app.json",
        ".oxlintrc.json", "security_rules.json", "hardware_profile.json",
        "models.json", "manifest.json", "README.md", "CONTEXT.md",
        "SETUP-PC-PUISSANT.md", "MASTER_PLAN.md", "GUIDE_PUISSANCE.md",
        "PROMPT_OPENCODE.md", "FIX_BENCH.md", "FIX_COMMANDES_OPENCODE.md",
        "mem.bat", "mem.py", "vite.config.ts", "run_dev.ts",
        "docker-compose.yml", "Dockerfile", ".gitignore", "AGENTS.md",
        "INSTALL.bat", "INSTALLER.bat", "INSTALLER.ps1", "launcher_localai.bat",
        "PUBLIER.md", "app.py", "server.py", "client.py",
    ];
    let liste: Vec<String> = fichiers.iter().map(|f| format!("'{f}'")).collect();
    let sql = format!(
        "UPDATE documents SET system=1 WHERE (system IS NULL OR system=0) AND (          filename IN ({}) OR filename LIKE 'routes%' OR substr(filename,1,1)='_'          OR description LIKE '%%entrain%%' OR description LIKE '%%exercice%%'          OR description LIKE '%%logiciel%%' OR description LIKE '%%plateforme d%%'          OR description LIKE '%%corpus%%')",
        liste.join(",")
    );
    // NB : notes_projet.txt, doc_*.txt, codes_postaux*, apprentissage_*
    // restent visibles : ce sont des documents/fiches utilisateur.
    let _ = st.db.execute(&sql, &[]);
}

async fn classify_text(st: &Arc<crate::AppState>, filename: &str, excerpt: &str) -> String {
    let model = st.db.get_setting("model_rapide", "qwen3:8b").to_string();
    let prompt = format!(
        "Classe ce document dans exactement une de ces categories : {}. \
Reponds UNIQUEMENT le nom de la categorie, un seul mot, sans ponctuation, sans phrase.\n\
Nom du fichier : {}\nDebut du contenu :\n{}",
        CATEGORIES.join(", "), filename, excerpt
    );
    let rep = match st
        .ollama
        .chat(
            &model,
            vec![localai_core::ollama::Msg { role: "user".into(), content: prompt }],
            0.0,
            12,
        )
        .await
    {
        Ok(r) => r,
        Err(_) => return "autre".into(),
    };
    let low = rep.to_lowercase();
    CATEGORIES
        .iter()
        .filter(|c| low.contains(**c))
        .max_by_key(|c| c.len())
        .map(|c| (*c).to_string())
        .unwrap_or_else(|| "autre".into())
}

fn illustration_prompt(cat: &str) -> String {
    let sujet = match cat {
        "programmation" => "code brackets and a terminal cursor",
        "ia" => "a brain made of connected glowing nodes",
        "impression3d" => "a 3d printer printing a small object, layer by layer",
        "bricolage" => "a hammer and a screwdriver crossed",
        "cuisine" => "a cooking pot with rising steam",
        "sante" => "a heart with a pulse line",
        "finances" => "stacked coins and a rising chart",
        "loisirs" => "an open book and a game controller",
        "science" => "an atom and a laboratory flask",
        "neo" => "a glowing green wireframe head silhouette with data streams",
        _ => "a folder full of documents",
    };
    format!(
        "minimalist flat vector icon of {sujet}, dark background, subtle green \
accent, clean simple shapes, no text, centered composition"
    )
}

static ILLUSTRATION_RUNNING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn spawn_illustrations(st: &Arc<crate::AppState>, missing: Vec<String>) {
    if ILLUSTRATION_RUNNING.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return;
    }
    let root = st.settings.root_dir.clone();
    let dir = st.settings.data_dir.join("fiches");
    tokio::spawn(async move {
        for cat in missing {
            let png = dir.join(format!("fiche_{cat}.png"));
            let jpg = dir.join(format!("fiche_{cat}.jpg"));
            if jpg.is_file() {
                continue;
            }
            let prompt = illustration_prompt(&cat).replace('\'', "");
            let out = png.to_string_lossy().replace('\'', "");
            let jpeg = jpg.to_string_lossy().replace('\'', "");
            let code = format!(
                "from backend.core.sdgen import generer; import os, json; \
from PIL import Image; m = generer('{}', r'{}', '', 512, 512, 4, -1); \
ok = os.path.exists(r'{}'); \
im = Image.open(r'{}').convert('RGB') if ok else None; \
im.save(r'{}', 'JPEG', quality=85, optimize=True) if im else None; \
os.remove(r'{}') if ok else None; \
print(json.dumps({{'ok': os.path.exists(r'{}')}}))",
                prompt, out, out, out, jpeg, out, jpeg
            );
            let _ = crate::pybridge::py_json_timeout(
                &root,
                &code,
                std::time::Duration::from_secs(900),
            )
            .await;
        }
        ILLUSTRATION_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
    });
}

async fn list(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    ensure_category(&st);
    // Consigne : les documents d'entrainement/logiciel restent en base
    // mais sont invisibles dans l'UI (system=0 requis).
    let rows = st.db.query_all_json(
        "SELECT id, filename, size_bytes, chunk_count, created_at, category, description          FROM documents WHERE COALESCE(system,0)=0 ORDER BY id DESC",
        &[],
    )?;
    Ok(Json(json!({ "documents": rows })))
}

async fn describe_text(st: &Arc<crate::AppState>, filename: &str, excerpt: &str) -> String {
    let model = st.db.get_setting("model_rapide", "qwen3:8b").to_string();
    let prompt = format!(
        "Decris en UNE phrase tres courte (15 mots maximum), en francais SIMPLE \
comprehensible par une personne qui ne fait PAS du tout d informatique, ce que \
represente ce document (exemples de tournures : exercices qui ont servi a \
entrainer l IA, notes personnelles, liste de donnees, programme du logiciel, \
texte de reference). Evite les mots techniques et le jargon. Reponds \
uniquement la phrase, sans introduction.\n\
Nom du fichier : {}\nDebut du contenu :\n{}",
        filename, excerpt
    );
    let rep = match st
        .ollama
        .chat(
            &model,
            vec![localai_core::ollama::Msg { role: "user".into(), content: prompt }],
            0.0,
            60,
        )
        .await
    {
        Ok(r) => r,
        Err(_) => return String::new(),
    };
    let d = rep.trim().trim_matches(|c| c == '"' || c == '«' || c == '»').to_string();
    let d = d.trim().to_string();
    let d: String = d.chars().take(140).collect();
    d.trim_end_matches('.').trim().to_string()
}

fn doc_extrait(st: &Arc<crate::AppState>, id: i64) -> (String, String) {
    let rows = st.db
        .query_all_json("SELECT filename FROM documents WHERE id=?", &[&id])
        .unwrap_or_default();
    let filename = rows
        .first()
        .and_then(|r| r.get("filename"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let chunks = st.db
        .query_all_json(
            "SELECT content FROM document_chunks WHERE document_id=? ORDER BY position LIMIT 1",
            &[&id],
        )
        .unwrap_or_default();
    let extrait: String = chunks
        .first()
        .and_then(|c| c.get("content"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .chars()
        .take(400)
        .collect();
    (filename, extrait)
}

async fn fiches(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    ensure_category(&st);
    let dir = st.settings.data_dir.join("fiches");
    let _ = std::fs::create_dir_all(&dir);
    // Comptage des fiches : uniquement les documents VISIBLES (les
    // documents systeme masques ne comptent pas dans les fiches).
    let rows = st.db.query_all_json(
        "SELECT category, COUNT(*) AS n FROM documents WHERE COALESCE(system,0)=0 GROUP BY category",
        &[],
    )?;
    let mut counts: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    let mut non_classes: i64 = 0;
    for r in rows {
        let c = r.get("category").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let n = r.get("n").and_then(|v| v.as_i64()).unwrap_or(0);
        if c.is_empty() {
            non_classes = n;
        } else {
            counts.insert(c, n);
        }
    }
    let mut fiches: Vec<Value> = Vec::new();
    let mut missing: Vec<String> = Vec::new();
    for c in CATEGORIES {
        let has = dir.join(format!("fiche_{c}.jpg")).is_file();
        if !has {
            missing.push((*c).to_string());
        }
        fiches.push(json!({
            "category": c,
            "count": counts.get(*c).copied().unwrap_or(0),
            "illustration": has,
        }));
    }
    // Categorie "Neo" : fichiers generes pendant le chat (photos 4K, STL...).
    {
        let has = dir.join("fiche_neo.jpg").is_file();
        if !has {
            missing.push("neo".to_string());
        }
        fiches.push(json!({
            "category": "neo",
            "count": neo_fichiers(&st.settings.root_dir).len() as i64,
            "illustration": has,
        }));
    }
    if !missing.is_empty() {
        spawn_illustrations(&st, missing);
    }
    Ok(Json(json!({ "fiches": fiches, "non_classes": non_classes })))
}

#[derive(Debug, Deserialize)]
pub struct ClassifyBody {
    pub limit: Option<i64>,
}

async fn classify(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<ClassifyBody>,
) -> ApiResult<Json<Value>> {
    ensure_category(&st);
    let limit = b.limit.unwrap_or(8).clamp(1, 20);
    let rows = st.db.query_all_json(
        "SELECT id, filename FROM documents WHERE category IS NULL OR category='' ORDER BY id LIMIT ?",
        &[&limit],
    )?;
    let mut done = 0;
    for r in rows {
        let id = r.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
        let filename = r.get("filename").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let chunks = st.db.query_all_json(
            "SELECT content FROM document_chunks WHERE document_id=? ORDER BY position LIMIT 1",
            &[&id],
        )?;
        let excerpt: String = chunks
            .first()
            .and_then(|c| c.get("content"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .chars()
            .take(400)
            .collect();
        let cat = classify_text(&st, &filename, &excerpt).await;
        let _ = st.db.execute("UPDATE documents SET category=? WHERE id=?", &[&cat.as_str(), &id]);
        done += 1;
    }
    let rem = st.db.query_all_json(
        "SELECT COUNT(*) AS n FROM documents WHERE category IS NULL OR category=''",
        &[],
    )?;
    let remaining = rem
        .first()
        .and_then(|r| r.get("n"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    Ok(Json(json!({ "classified": done, "remaining": remaining })))
}

#[derive(Debug, Deserialize)]
pub struct DescribeBody {
    pub limit: Option<i64>,
}

async fn describe(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<DescribeBody>,
) -> ApiResult<Json<Value>> {
    ensure_category(&st);
    let limit = b.limit.unwrap_or(8).clamp(1, 20);
    let rows = st.db.query_all_json(
        "SELECT id FROM documents WHERE description IS NULL OR description='' ORDER BY id LIMIT ?",
        &[&limit],
    )?;
    let mut done = 0;
    for r in rows {
        let id = r.get("id").and_then(|v| v.as_i64()).unwrap_or(0);
        let (filename, extrait) = doc_extrait(&st, id);
        let d = describe_text(&st, &filename, &extrait).await;
        if !d.is_empty() {
            let _ = st.db.execute(
                "UPDATE documents SET description=? WHERE id=?",
                &[&d.as_str(), &id],
            );
            done += 1;
        }
    }
    let rem = st.db.query_all_json(
        "SELECT COUNT(*) AS n FROM documents WHERE description IS NULL OR description=''",
        &[],
    )?;
    let remaining = rem
        .first()
        .and_then(|r| r.get("n"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    Ok(Json(json!({ "described": done, "remaining": remaining })))
}

static APPRENTISSAGE_RUNNING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

#[derive(Debug, Deserialize)]
pub struct ApprentissageBody {
    pub category: String,
}

fn apprentissage_pid(st: &Arc<crate::AppState>) -> u32 {
    std::fs::read_to_string(st.settings.data_dir.join("apprentissage.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|m| m.get("pid").and_then(|v| v.as_u64()))
        .unwrap_or(0) as u32
}

async fn apprentissage(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<ApprentissageBody>,
) -> ApiResult<Json<Value>> {
    let cat = b.category.trim().to_lowercase();
    if !CATEGORIES.contains(&cat.as_str()) {
        return Err(ApiError(localai_core::AppError::BadRequest("categorie inconnue".into())));
    }
    if APPRENTISSAGE_RUNNING.load(std::sync::atomic::Ordering::SeqCst) {
        let ancien = apprentissage_pid(&st);
        let vivant = ancien > 0 && crate::routes::finetune::pid_alive(ancien).await;
        if vivant {
            return Ok(Json(json!({ "ok": false, "detail": "un apprentissage est deja en cours" })));
        }
        APPRENTISSAGE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
    }
    APPRENTISSAGE_RUNNING.store(true, std::sync::atomic::Ordering::SeqCst);
    let root = st.settings.root_dir.clone();
    let py = root.join(".venv").join("Scripts").join("python.exe");
    if !py.is_file() {
        APPRENTISSAGE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
        return Err(ApiError(localai_core::AppError::Internal("venv python absent".into())));
    }
    let logs = root.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    let log = logs.join(format!("apprentissage_{cat}.out"));
    let pid = match crate::routes::finetune::spawn_detached(&py, &["-u", "apprentissage.py", &cat], &root, &log)
        .await
    {
        Ok(p) => p,
        Err(e) => {
            APPRENTISSAGE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
            return Err(ApiError(localai_core::AppError::Internal(format!("lancement apprentissage: {e}"))));
        }
    };
    let meta = json!({ "pid": pid, "category": cat, "started": crate::routes::finetune::now_unix() });
    let _ = std::fs::write(st.settings.data_dir.join("apprentissage.json"), meta.to_string());
    Ok(Json(json!({ "ok": true, "category": cat, "pid": pid })))
}

async fn apprentissage_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let pid = apprentissage_pid(&st);
    let meta: Value = std::fs::read_to_string(st.settings.data_dir.join("apprentissage.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(json!({}));
    let cat = meta.get("category").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if pid == 0 {
        return Ok(Json(json!({ "running": false, "category": "", "etape": "" })));
    }
    let running = crate::routes::finetune::pid_alive(pid).await;
    if !running {
        APPRENTISSAGE_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
    }
    let log = st.settings.root_dir.join("logs").join(format!("apprentissage_{cat}.out"));
    let etape = std::fs::read_to_string(&log)
        .map(|t| {
            t.lines()
                .rev()
                .find(|l| !l.trim().is_empty())
                .unwrap_or("")
                .trim()
                .trim_start_matches("[apprentissage] ")
                .chars()
                .take(160)
                .collect::<String>()
        })
        .unwrap_or_default();
    Ok(Json(json!({ "running": running, "category": cat, "etape": etape })))
}

/// Liste les fichiers generes par le chat (photos, meshes, rendus) dans
/// workspace/ et data/img_gen : classement automatique en categorie "Neo".
/// Retourne (chemin relatif a la racine, taille octets, modifie unix sec).
fn neo_fichiers(root: &std::path::Path) -> Vec<(String, u64, i64)> {
    let mut out: Vec<(String, u64, i64)> = Vec::new();
    let exts = ["png", "jpg", "jpeg", "webp", "tif", "tiff", "exr",
        "stl", "scad", "gcode", "obj"];
    for base in ["workspace", "data/img_gen"] {
        let dir = root.join(base);
        let mut pile: Vec<std::path::PathBuf> = vec![dir];
        let mut garde = 0usize;
        while let Some(d) = pile.pop() {
            garde += 1;
            if garde > 64 {
                break;
            }
            let Ok(entries) = std::fs::read_dir(&d) else { continue };
            for e in entries.flatten() {
                let p = e.path();
                if p.is_dir() {
                    if p.components().count() < 6 {
                        pile.push(p);
                    }
                    continue;
                }
                let Some(ext) = p.extension().and_then(|x| x.to_str()) else { continue };
                if !exts.contains(&ext.to_ascii_lowercase().as_str()) {
                    continue;
                }
                // Aperçus JPEG côté à côté des masters TIFF/EXR : servis
                // à l'affichage mais jamais listés comme fichiers Néo.
                if let Some(nom) = p.file_name().and_then(|n| n.to_str()) {
                    if nom.to_ascii_lowercase().ends_with("_apercu.jpg") {
                        continue;
                    }
                }
                let Ok(meta) = std::fs::metadata(&p) else { continue };
                let taille = meta.len();
                let modifie = meta
                    .modified()
                    .ok()
                    .and_then(|m| m.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_secs() as i64)
                    .unwrap_or(0);
                let relatif = p
                    .strip_prefix(root)
                    .map(|r| r.to_string_lossy().to_string())
                    .unwrap_or_else(|_| p.to_string_lossy().to_string());
                out.push((relatif, taille, modifie));
            }
        }
    }
    out.sort_by(|a, b| b.2.cmp(&a.2));
    out.truncate(300);
    out
}

/// GET /api/documents/neo-files : fichiers de la categorie "Neo".
async fn neo_files(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let fichiers = neo_fichiers(&st.settings.root_dir);
    let arr: Vec<Value> = fichiers
        .into_iter()
        .map(|(chemin, taille, modifie)| {
            let nom = std::path::Path::new(&chemin)
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| chemin.clone());
            json!({ "name": nom, "path": chemin, "size_bytes": taille, "modified": modifie })
        })
        .collect();
    Ok(Json(json!({ "files": arr })))
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
        ensure_category(&st);
        let excerpt: String = text.chars().take(400).collect();
        let cat = classify_text(&st, &filename, &excerpt).await;
        let desc = describe_text(&st, &filename, &excerpt).await;
        let _ = st.db.execute(
            "UPDATE documents SET category=?, description=? WHERE id=?",
            &[&cat.as_str(), &desc.as_str(), &doc_id],
        );
        return Ok(Json(json!({ "id": doc_id, "filename": filename, "chunks": chunks.len(), "category": cat, "description": desc })));
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
