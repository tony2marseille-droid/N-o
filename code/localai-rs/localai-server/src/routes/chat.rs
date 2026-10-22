use std::sync::Arc;

use axum::{
    extract::State,
    response::sse::{Event, KeepAlive, Sse},
    routing::{get, post},
    Json, Router,
};
use futures_util::{Stream, StreamExt};
use localai_core::db::now_iso;
use localai_core::ollama::{Msg, DEFAULT_NUM_CTX};
use crate::error::ApiError;
use localai_core::AppError;
use serde::Deserialize;
use serde_json::json;
use std::convert::Infallible;
use tokio::sync::mpsc;

use crate::tools;
use crate::AppState;

mod vision;

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/api/chat", post(chat))
        .route("/api/user-active", get(user_active))
        .layer(vision::body_limit())
}

/// Marqueur d'activite utilisateur : les jobs de fond (boucle agents,
/// veille, conseil...) passent background=true et ne le mettent PAS a jour.
/// Ils consultent GET /api/user-active pour ceder la file d'attente GPU
/// a l'utilisateur interactif (priorite Neo).
async fn user_active(State(st): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let last: i64 = st.db.get_setting("user_active_at", "").parse().unwrap_or(0);
    let now = crate::routes::finetune::now_unix() as i64;
    Json(json!({ "seconds_since_user": if last > 0 { now - last } else { -1i64 } }))
}

#[derive(Debug, Deserialize, Clone, Default)]
pub struct ChatBody {
    pub conversation_id: Option<i64>,
    pub model: Option<String>,
    pub message: String,
    pub temperature: Option<f32>,
    pub regenerate: Option<bool>,
    pub agent_id: Option<String>,
    pub tools_enabled: Option<bool>,
    pub rag_enabled: Option<bool>,
    pub images: Option<Vec<String>>,
    pub web_enabled: Option<bool>,
    /// true = requete de fond (boucle agents, scripts auto) : ne met pas
    /// a jour le marqueur d'activite utilisateur (priorite file d'attente).
    pub background: Option<bool>,
}

fn sse_data(v: serde_json::Value) -> String {
    format!("data: {}\n\n", v)
}

/// Chantier 4 : contexte modele (num_ctx du Modelfile v9.2 actuel = 8192).
const NUM_CTX: usize = DEFAULT_NUM_CTX;
/// Troncature des sorties d'outils avant reinjection (le complet reste en base).
const TOOL_OUT_LIMIT: usize = 4000;

/// Estimation grossiere du nombre de tokens (FR + code : ~3 car./token).
fn est_tokens(messages: &[Msg]) -> usize {
    messages
        .iter()
        .map(|m| m.content.chars().count() / 3)
        .sum::<usize>()
        + messages.len() * 8
}

/// Messages pour Ollama : images jointes au DERNIER message user (vision).
fn messages_json(history: &[Msg], images: &Option<Vec<String>>) -> serde_json::Value {
    let mut arr: Vec<serde_json::Value> = history
        .iter()
        .map(|m| json!({ "role": m.role, "content": m.content }))
        .collect();
    if let Some(imgs) = images {
        if !imgs.is_empty() {
            for m in arr.iter_mut().rev() {
                if m.get("role").and_then(|r| r.as_str()) == Some("user") {
                    m["images"] = json!(imgs);
                    break;
                }
            }
        }
    }
    serde_json::Value::Array(arr)
}

fn is_vision_model(tag: &str) -> bool {
    let t = tag.to_lowercase();
    t.contains("vision") || t.contains("vl") || t.contains("llava") || t.contains("qwen2.5vl")
}

/// Extraction des chemins d'images cites dans un texte, sans crate regex.
/// Reconnait les absolus [A-Za-z]:\...\x.(png|jpe?g|webp) et les relatifs
/// data\...|data/...|workspace\...|workspace/.... Une frontiere est exigee
/// avant le debut (evite les faux positifs "https://...", "metadata\...").
fn chemins_images(texte: &str) -> Vec<String> {
    let chars: Vec<char> = texte.chars().collect();
    let n = chars.len();
    // "jpeg" avant "jpg" : sinon ".jpeg" serait tronque en ".jpg".
    let exts = ["png", "jpeg", "jpg", "webp"];
    let stop = |c: char| {
        c.is_whitespace()
            || matches!(
                c,
                '<' | '>' | '"' | '\'' | '`' | ')' | ']' | '}' | '(' | '[' | '*' | '!'
                    | ',' | ';' | '|'
            )
    };
    let mut res: Vec<String> = Vec::new();
    let mut i = 0usize;
    while i < n {
        let prev_ok = i == 0
            || !(chars[i - 1].is_alphanumeric()
                || chars[i - 1] == '_'
                || chars[i - 1] == '\\'
                || chars[i - 1] == '/'
                || chars[i - 1] == '.');
        if !prev_ok {
            i += 1;
            continue;
        }
        let debut_abs = chars[i].is_ascii_alphabetic()
            && i + 2 < n
            && chars[i + 1] == ':'
            && (chars[i + 2] == '\\' || chars[i + 2] == '/');
        let fenetre: String = chars[i..n.min(i + 12)]
            .iter()
            .collect::<String>()
            .to_lowercase();
        let debut_rel = fenetre.starts_with("data\\")
            || fenetre.starts_with("data/")
            || fenetre.starts_with("workspace\\")
            || fenetre.starts_with("workspace/");
        if !(debut_abs || debut_rel) {
            i += 1;
            continue;
        }
        let mut j = i;
        let mut trouve = false;
        while j < n {
            let c = chars[j];
            if stop(c) {
                break;
            }
            if c == '.' && j + 1 < n {
                let suite: String = chars[(j + 1)..n.min(j + 6)]
                    .iter()
                    .collect::<String>()
                    .to_lowercase();
                for ext in exts {
                    if suite.starts_with(ext) {
                        let k = j + 1 + ext.len();
                        if k >= n || !chars[k].is_alphanumeric() {
                            j = k;
                            trouve = true;
                            break;
                        }
                    }
                }
                if trouve {
                    break;
                }
            }
            j += 1;
        }
        if trouve && j > i {
            let chemin: String = chars[i..j].iter().collect();
            if !res.contains(&chemin) {
                res.push(chemin);
            }
            i = j;
        } else {
            i += 1;
        }
    }
    res
}

fn chemin_existe(root: &std::path::Path, brut: &str) -> bool {
    let p = std::path::Path::new(brut);
    if p.is_absolute() {
        p.is_file()
    } else {
        root.join(p).is_file()
    }
}

/// Vrais fichiers images produits par les outils de CE tour, extraits des
/// traces (MEILLEURE IMAGE: x, Image generee: x, Image telechargee: x...).
fn vivants_depuis_rows(rows: &[serde_json::Value], root: &std::path::Path) -> Vec<String> {
    let mut vivants: Vec<String> = Vec::new();
    for r in rows {
        let Some(preview) = r.get("result_preview").and_then(|v| v.as_str()) else {
            continue;
        };
        for p in chemins_images(preview) {
            if !vivants.contains(&p) && chemin_existe(root, &p) {
                vivants.push(p);
            }
        }
    }
    vivants
}

/// Garde-fou anti-hallucination : chaque chemin d'image cite dans la
/// reponse doit exister sur disque. Les chemins morts sont remplaces par
/// les vrais fichiers produits par les outils de CE tour, sinon retires
/// avec un avertissement visible. Le modele fine-tune invente parfois des
/// reponses au format "image generee" sans appeler les outils : ceci
/// empeche l'affichage de liens morts dans le chat.
fn corrige_chemins_image(full: &str, root: &std::path::Path, vivants: &[String]) -> String {
    let cites = chemins_images(full);
    let morts: Vec<&String> = cites.iter().filter(|c| !chemin_existe(root, c)).collect();
    if morts.is_empty() {
        return full.to_string();
    }
    let mut out = full.to_string();
    let mut vi = 0usize;
    for m in &morts {
        if vi < vivants.len() {
            out = out.replace(m.as_str(), vivants[vi].as_str());
            vi += 1;
        } else {
            out = out.replace(m.as_str(), "");
        }
    }
    if vi == 0 {
        out.push_str(
            "\n\n(Attention : aucune image n'a reellement ete generee ni telechargee durant ce tour ; les chemins cites etaient invalides et ont ete retires. Reformule ta demande pour que les outils soient appeles.)",
        );
    } else {
        out.push_str(
            "\n\n(Correction automatique : des chemins d'images invalides ont ete remplaces par les fichiers reellement produits.)",
        );
    }
    out
}

pub async fn chat(
    State(st): State<Arc<AppState>>,
    Json(b): Json<ChatBody>,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ApiError> {
    if b.message.trim().is_empty() {
        return Err(ApiError(AppError::BadRequest("message vide".into())));
    }
    // Priorite file d'attente : trace l'activite utilisateur interactif.
    // Les requetes de fond (background=true) ne touchent pas ce marqueur :
    // elles doivent lui ceder la place (voir GET /api/user-active).
    if !b.background.unwrap_or(false) {
        let _ = st.db.set_setting(
            "user_active_at",
            &crate::routes::finetune::now_unix().to_string(),
        );
    }
    let mut model = if b.model.as_deref().unwrap_or("auto") == "auto" {
        st.settings.default_model.clone()
    } else {
        b.model.clone().unwrap()
    };
    let temperature = b.temperature.unwrap_or(0.2).clamp(0.0, 2.0);

    // Conversation : reuse ou creation (identique v1).
    let now = now_iso();
    let cid: i64 = match b.conversation_id {
        Some(id) => {
            let exists = st
                .db
                .query_one_json("SELECT id FROM conversations WHERE id=?", &[&id])?;
            if exists.is_none() {
                return Err(ApiError(AppError::NotFound("Conversation introuvable".into())));
            }
            if b.regenerate.unwrap_or(false) {
                if let Some(last) = st.db.query_one_json(
                    "SELECT id FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                    &[&id],
                )? {
                    if let Some(mid) = last.get("id").and_then(|v| v.as_i64()) {
                        st.db.execute("DELETE FROM messages WHERE id=?", &[&mid])?;
                    }
                }
            } else {
                st.db.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                    &[&id, &"user", &b.message.as_str(), &now.as_str()],
                )?;
            }
            st.db.execute(
                "UPDATE conversations SET updated_at=?, model=? WHERE id=?",
                &[&now.as_str(), &model.as_str(), &id],
            )?;
            id
        }
        None => {
            let title: String = b.message.trim().chars().take(60).collect();
            let title = if title.is_empty() { "Nouvelle conversation".to_string() } else { title };
            let id = st.db.execute(
                "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?,?,?,?)",
                &[&title.as_str(), &model.as_str(), &now.as_str(), &now.as_str()],
            )?;
            st.db.execute(
                "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                &[&id, &"user", &b.message.as_str(), &now.as_str()],
            )?;
            id
        }
    };

    let rows = st.db.query_all_json(
        "SELECT role, content FROM messages WHERE conversation_id=? AND role IN ('user','assistant') ORDER BY id DESC LIMIT 40",
        &[&cid],
    )?;
    let mut history: Vec<Msg> = rows
        .into_iter()
        .rev()
        .filter_map(|r| {
            Some(Msg {
                role: r.get("role")?.as_str()?.to_string(),
                content: r.get("content")?.as_str()?.to_string(),
            })
        })
        .collect();

    // Outils : agent explicite, detection d'intention, ou activation explicite (+ web).
    // 1) agent explicite (non-auto) -> ses outils.
    // 2) sinon : routeur Python enrichi EN PRIORITE (il distingue
    //    voir_image / generer_image / 3d / dev / docs). L'ancienne
    //    detection Rust court-circuitait sur "photo"/"image" et envoyait
    //    "montre-moi une photo de X" en cad3d (mesh !) ; elle ne sert plus
    //    que de repli, avec des mots-cles 3D STRICTS uniquement.
    let mut tool_names: Vec<String> = Vec::new();
    let mut resolved_agent: Option<String> = None;
    let mut route_reason: String = String::new();
    if let Some(aid) = b.agent_id.as_deref() {
        if !aid.is_empty() && aid != "auto" {
            tool_names = tools::agent_tools(&st.settings.root_dir, aid).await;
            if !tool_names.is_empty() {
                resolved_agent = Some(aid.to_string());
                route_reason = format!("agent explicite: {aid}");
            }
        }
    }
    if tool_names.is_empty() {
        let (mut routed, mut raison) =
            tools::route_agent_full(&st.settings.root_dir, &b.message).await;
        if (routed.is_empty() || routed == "general")
            && routed != "cad3d"
        {
            // Repli Rust uniquement si mots-cles 3D STRICTS (jamais
            // photo/image/dessin : ce sont des demandes d'images 2D).
            let q = b.message.to_lowercase();
            let strict_3d = ["blender", "3d", "stl", "scad", "mesh",
                "imprim", "tranche", "g-code", "gcode", "openscad"]
                .iter().any(|k| q.contains(k));
            if strict_3d {
                routed = "cad3d".to_string();
                raison = "repli Rust: mots-cles 3D stricts".to_string();
            }
        }
        if !routed.is_empty() && routed != "general" {
            tool_names = tools::agent_tools(&st.settings.root_dir, &routed).await;
            if !tool_names.is_empty() {
                resolved_agent = Some(routed.clone());
                route_reason = raison;
            }
        }
    }
    if tool_names.is_empty() && b.tools_enabled.unwrap_or(false) {
        tool_names = tools::default_tools();
    }
    if b.web_enabled.unwrap_or(false) {
        for w in ["web_search", "web_fetch"] {
            if !tool_names.iter().any(|t| t == w) {
                tool_names.push(w.to_string());
            }
        }
    }
    // System prompt combine : agent + memoirs + RAG (miroir _build_system_prompt).
    // Quand des images sont jointes : sanitisation base64 (Ollama exige du brut
    // sans prefixe data:), forçage du modele vision qwen2.5vl:7b et desactivation
    // des outils natifs Ollama (incompatibles : erreur 500 "does not support tools").
    let mut model = model;
    let images_clean: Vec<String> = b
        .images
        .as_ref()
        .map(|v| v.iter().filter_map(|s| vision::clean_b64(s)).collect())
        .unwrap_or_default();
    let has_images = !images_clean.is_empty();
    if has_images {
        model = vision::VISION_MODEL.to_string();
    }
    {
        let mut parts: Vec<String> = Vec::new();
        if has_images {
            parts.push(vision::VISION_SYSTEM.to_string());
        }
        if let Some(agent) = resolved_agent.as_deref() {
            let sys = tools::agent_system(&st.settings.root_dir, agent).await;
            if !sys.trim().is_empty() {
                parts.push(sys);
            }
            // RAG si l'agent l'exige (docs) ou demande explicite.
            let want_rag = b.rag_enabled.unwrap_or(false)
                || tools::agent_rag(&st.settings.root_dir, agent).await;
            if want_rag {
                let block = crate::routes::documents::rag_block(
                    &st.settings.db_path(), &st.ollama, &b.message, 4).await;
                if !block.is_empty() {
                    parts.push(block);
                }
            }
        } else if b.rag_enabled.unwrap_or(false) {
            let block = crate::routes::documents::rag_block(
                &st.settings.db_path(), &st.ollama, &b.message, 4).await;
            if !block.is_empty() {
                parts.push(block);
            }
        }
        // Memoires apprises (30 dernieres).
        if let Ok(mems) = st.db.query_all_json(
            "SELECT content FROM memories ORDER BY id DESC LIMIT 30", &[]) {
            let lines: Vec<String> = mems.iter().filter_map(|m| {
                m.get("content")?.as_str().map(|c| format!("- {c}"))
            }).collect();
            if !lines.is_empty() {
                parts.push(format!("Informations memorisees a propos de l'utilisateur:\n{}", lines.join("\n")));
            }
        }
        if !parts.is_empty() {
            history.insert(0, Msg { role: "system".into(), content: parts.join("\n\n") });
        }
    }

    let (tx, rx) = mpsc::unbounded_channel::<String>();
    let tx_meta = tx.clone();
    let _ = tx_meta.send(sse_data(json!({
        "type": "meta",
        "conversation_id": cid,
        "model": model,
        "agent": resolved_agent.clone(),
        "route_reason": route_reason,
    })));

    if tool_names.is_empty() {
        // Chemin rapide : streaming direct (inchange).
        let db2 = st.db.clone();
        let ollama = st.ollama.clone();
        let model2 = model.clone();
        let root2 = st.settings.root_dir.clone();
        let body = serde_json::json!({
            "messages": messages_json(&history, &Some(images_clean.clone())),
            "think": false,
            "options": {
                "temperature": temperature,
                "num_predict": 2048,
                "num_ctx": NUM_CTX,
            },
        });
        tokio::spawn(async move {
            let started = std::time::Instant::now();
            let txc = tx.clone();
            let res = ollama
                .chat_stream(&model2, body, |tok| {
                    let _ = txc.send(sse_data(json!({ "type": "token", "content": tok })));
                })
                .await;
            match res {
                Ok(mut full) => {
                    let ms = started.elapsed().as_millis() as i64;
                    // Garde-fou : chemins d'images cites mais inexistants ->
                    // retires (aucun outil n'a tourne, donc aucun fichier
                    // reel a substituer).
                    let rootp = std::path::Path::new(&root2);
                    let morts: Vec<String> = chemins_images(&full)
                        .into_iter()
                        .filter(|c| !chemin_existe(rootp, c))
                        .collect();
                    if !morts.is_empty() {
                        let corrige = corrige_chemins_image(&full, rootp, &[]);
                        tracing::warn!(
                            "garde-fou images (chemin rapide): morts={:?}",
                            morts
                        );
                        let _ = tx.send(sse_data(json!({
                            "type": "replace", "content": corrige,
                        })));
                        full = corrige;
                    }
                    let now2 = now_iso();
                    let _ = db2.execute(
                        "INSERT INTO messages (conversation_id, role, content, model, duration_ms, created_at) VALUES (?,?,?,?,?,?)",
                        &[&cid, &"assistant", &full.as_str(), &model2.as_str(), &ms, &now2.as_str()],
                    );
                    let _ = db2.execute(
                        "UPDATE conversations SET updated_at=? WHERE id=?",
                        &[&now2.as_str(), &cid],
                    );
                    let _ = tx.send(sse_data(json!({
                        "type": "done",
                        "conversation_id": cid,
                        "duration_ms": ms,
                    })));
                }
                Err(e) => {
                    let _ = tx.send(sse_data(json!({ "type": "error", "error": e.to_string() })));
                }
            }
        });
    } else {
        // Chemin outils : boucle multi-rounds avec specs + execution.
        let db2 = st.db.clone();
        let ollama = st.ollama.clone();
        let root = st.settings.root_dir.clone();
        let model2 = model.clone();
        let level = tools::tool_level(&st.db);
        let images2 = images_clean.clone();
        tokio::spawn(async move {
            let started = std::time::Instant::now();
            let debut_iso = now_iso();
            // Le modèle vision (qwen2.5vl:7b) ne supporte pas les outils natifs Ollama :
            // si des images sont jointes, on désactive les outils pour cet appel
            // (erreur 500 "does not support tools" sinon). Les outils restent
            // disponibles sur les requêtes sans image.
            let specs = if images2.is_empty() {
                tools::specs(&root, &tool_names).await.unwrap_or_default()
            } else {
                Vec::new()
            };
            let mut messages = history;
            let mut full = String::new();
            let mut tool_pos: Vec<(usize, String, usize)> = Vec::new();
            let seuil = NUM_CTX * 8 / 10;
            for round in 0..tools::MAX_TOOL_ROUNDS {
                let payload = messages_json(&messages, &Some(images2.clone()));
                let (text, calls) = match ollama
                    .chat_tools(&model2, payload, &specs, temperature, 2048)
                    .await
                {
                    Ok(r) => r,
                    Err(e) => {
                        let _ = tx.send(sse_data(json!({ "type": "error", "error": e.to_string() })));
                        return;
                    }
                };
                if !text.is_empty() {
                    full.push_str(&text);
                    let _ = tx.send(sse_data(json!({ "type": "token", "content": text })));
                }
                if calls.is_empty() || round + 1 >= tools::MAX_TOOL_ROUNDS {
                    break;
                }
                // Message assistant avec les appels (pour le contexte du round suivant).
                messages.push(Msg { role: "assistant".into(), content: text.clone() });
                for (seq, (name, args)) in calls.iter().enumerate() {
                    let result = tools::execute(&root, name, args, &level).await;
                    let denied = result.starts_with("PERMISSION REFUSEE")
                        || result.starts_with("refusé")
                        || result.starts_with("ACCES REFUSE")
                        || result.starts_with("COMMANDE BLOQUEE")
                        || result.starts_with("Erreur: timeout sandbox");
                    let reason: String = result
                        .lines()
                        .next()
                        .unwrap_or("")
                        .chars()
                        .take(200)
                        .collect();
                    let preview: String = result.chars().take(280).collect();
                    let _ = tx.send(sse_data(json!({
                        "type": "tool_exec", "name": name, "args": args,
                        "denied": denied, "preview": preview,
                    })));
                    let now3 = now_iso();
                    let args_s = serde_json::to_string(args).unwrap_or_default();
                    let status_s = if denied { "denied" } else { "ok" };
                    let _ = db2.execute(
                        "INSERT INTO tool_traces (conversation_id, seq, tool_name, arguments, result_preview, created_at, status, reason) VALUES (?,?,?,?,?,?,?,?)",
                        &[&cid, &(round as i64), &name.as_str(), &args_s.as_str(), &preview.as_str(), &now3.as_str(), &status_s, &reason.as_str()],
                    );
                    let _ = seq;
                    // Chantier 4 : sortie tronquee a 4000 car. avant reinjection
                    // (le complet reste en base via tool_traces + messages).
                    let result_short: String = result.chars().take(TOOL_OUT_LIMIT).collect();
                    tool_pos.push((messages.len(), name.clone(), round));
                    messages.push(Msg {
                        role: "tool".into(),
                        content: format!("[{name}] {result_short}"),
                    });
                }
                // Chantier 4 : compactage de contexte — au-dela de 80 % de
                // num_ctx, les sorties d'outils anciennes (hors 2 derniers
                // rounds) deviennent un resume d'une ligne, journalise.
                if est_tokens(&messages) > seuil {
                    let now4 = now_iso();
                    for (idx, nom, tround) in tool_pos.iter().cloned() {
                        if tround + 2 <= round {
                            let orig = messages[idx].content.clone();
                            let orig_len = orig.chars().count();
                            if orig_len <= 600 {
                                continue;
                            }
                            let prem_ligne: String = orig
                                .lines()
                                .next()
                                .unwrap_or("")
                                .chars()
                                .take(120)
                                .collect();
                            messages[idx].content =
                                format!("[{nom} — résultat résumé] {prem_ligne}");
                            let note = format!("compacte: {} car. -> resume", orig_len);
                            let reason4 = "compactage_contexte_80pct_numctx";
                            let args4 = "";
                            let _ = db2.execute(
                                "INSERT INTO tool_traces (conversation_id, seq, tool_name, arguments, result_preview, created_at, status, reason) VALUES (?,?,?,?,?,?,?,?)",
                                &[&cid, &(tround as i64), &nom.as_str(), &args4, &note.as_str(), &now4.as_str(), &"compact", &reason4],
                            );
                        }
                    }
                }
            }
            let ms = started.elapsed().as_millis() as i64;
            // Garde-fou anti-hallucination : les chemins d'images cites
            // doivent exister. Un chemin mort est remplace par un vrai
            // fichier produit par les outils de CE tour (traces), sinon
            // retire avec avertissement. Event "replace" -> le frontend
            // remplace le texte deja streamé.
            let mut final_content = full.clone();
            {
                let rootp = std::path::Path::new(&root);
                let morts: Vec<String> = chemins_images(&full)
                    .into_iter()
                    .filter(|c| !chemin_existe(rootp, c))
                    .collect();
                if !morts.is_empty() {
                    let vivants = db2
                        .query_all_json(
                            "SELECT result_preview FROM tool_traces WHERE conversation_id=? \
                             AND created_at>=? AND status='ok' \
                             AND tool_name IN ('generate_image','edit_image','fetch_image') \
                             ORDER BY id DESC LIMIT 12",
                            &[&cid, &debut_iso.as_str()],
                        )
                        .map(|rows| vivants_depuis_rows(&rows, rootp))
                        .unwrap_or_default();
                    tracing::warn!(
                        "garde-fou images: morts={:?} vivants={:?}",
                        morts, vivants
                    );
                    let corrige = corrige_chemins_image(&full, rootp, &vivants);
                    if corrige != full {
                        let _ = tx.send(sse_data(json!({
                            "type": "replace", "content": corrige,
                        })));
                    }
                    final_content = corrige;
                }
            }
            let now2 = now_iso();
            let _ = db2.execute(
                "INSERT INTO messages (conversation_id, role, content, model, duration_ms, created_at) VALUES (?,?,?,?,?,?)",
                &[&cid, &"assistant", &final_content.as_str(), &model2.as_str(), &ms, &now2.as_str()],
            );
            let _ = db2.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                &[&now2.as_str(), &cid],
            );
            let _ = tx.send(sse_data(json!({
                "type": "done",
                "conversation_id": cid,
                "duration_ms": ms,
            })));
        });
    }

    let stream = tokio_stream::wrappers::UnboundedReceiverStream::new(rx);
    let raw = stream.map(|s| {
        let payload = s.trim_start_matches("data: ").trim().to_string();
        Ok::<_, Infallible>(Event::default().data(payload))
    });
    Ok(Sse::new(raw).keep_alive(KeepAlive::default()))
}
