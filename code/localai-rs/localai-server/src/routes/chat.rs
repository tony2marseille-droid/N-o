use std::sync::Arc;

use axum::{
    extract::State,
    response::sse::{Event, KeepAlive, Sse},
    routing::post,
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

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/api/chat", post(chat))
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

/// Estimation de duree pour l'affichage live (meta.eta_ms) : somme des
/// durées typiques par outil (ms) — valeurs calibreées sur l'exploitation
/// reelle (charges de modeles comprises). Defaut 20 s.
fn eta_ms_pour(tools: &[String]) -> u64 {
    const TABLE: &[(&str, u64)] = &[
        ("find_reference_image", 180_000),
        ("generate_image", 420_000),
        ("edit_image", 420_000),
        ("generate_mesh_from_image", 150_000),
        ("repair_mesh", 90_000),
        ("validate_mesh", 120_000),
        ("validate_design", 120_000),
        ("render_scad", 30_000),
        ("slice_model", 60_000),
        ("blender_run", 600_000),
        ("blender_render", 120_000),
        ("render_stl_views", 45_000),
        ("render_preview", 30_000),
        ("deep_research", 240_000),
        ("document_create", 25_000),
        ("web_search", 15_000),
        ("web_fetch", 15_000),
        ("image_search", 15_000),
        ("fetch_image", 15_000),
        ("analyze_image", 45_000),
        ("capture_ecran", 30_000),
        ("meteo", 8_000),
        ("system_stats", 4_000),
        ("volume", 3_000),
        ("open_app", 3_000),
        ("telecharger_fichier", 30_000),
        ("convertir_image", 10_000),
        ("rechercher_fichiers", 5_000),
        ("presse_papiers", 3_000),
        ("memoire_ajouter", 3_000),
        ("notes", 3_000),
        ("check_code", 20_000),
        ("calculator", 500),
        ("get_datetime", 500),
    ];
    let mut total: u64 = 5_000;
    for nom in tools {
        let duree = TABLE
            .iter()
            .find(|(n, _)| n == nom)
            .map(|(_, d)| *d)
            .unwrap_or(20_000);
        total += duree;
    }
    total
}

/// Sauvegarde une image jointe (base64 ou data URL) dans le workspace input.
/// Retourne le chemin absolu (ou None si decodage impossible).
async fn save_attached_image(root: &std::path::Path, data: &str, horodatage: &str, idx: usize) -> Option<String> {
    use base64::Engine;
    let (b64, ext) = if let Some(rest) = data.strip_prefix("data:image/") {
        let (fmt, r2) = rest.split_once(';')?;
        let b64part = r2.strip_prefix("base64,")?;
        (b64part.to_string(), format!(".{}", fmt.split('+').next().unwrap_or("png")))
    } else {
        (data.to_string(), ".png".to_string())
    };
    let octets = base64::engine::general_purpose::STANDARD
        .decode(b64.trim())
        .ok()?;
    if octets.is_empty() {
        return None;
    }
    // Detection du format reel (independante de l'extension).
    let ext2 = if octets.starts_with(&[0xFF, 0xD8, 0xFF]) {
        ".jpg"
    } else if octets.starts_with(&[0x89, 0x50, 0x4E, 0x47]) {
        ".png"
    } else if octets.starts_with(b"RIFF") && octets.get(8..12) == Some(b"WEBP") {
        ".webp"
    } else if octets.starts_with(b"GIF8") {
        ".gif"
    } else {
        ext.as_str()
    };
    let ws = tools::sandbox_root(root);
    let dir = format!("{ws}\\input");
    let _ = std::fs::create_dir_all(&dir);
    let chemin = format!("{dir}\\{horodatage}_{idx}{ext2}");
    if std::fs::write(&chemin, &octets).is_ok() {
        Some(chemin)
    } else {
        None
    }
}

pub async fn chat(
    State(st): State<Arc<AppState>>,
    Json(b): Json<ChatBody>,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ApiError> {
    if b.message.trim().is_empty() {
        return Err(ApiError(AppError::BadRequest("message vide".into())));
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
    // 2) agent auto/absent -> detection 3D/image (le routeur Python rate ces
    //    intentions), sinon pont route_agent Python (comportement d'origine).
    let mut tool_names: Vec<String> = Vec::new();
    let mut resolved_agent: Option<String> = None;
    if let Some(aid) = b.agent_id.as_deref() {
        if !aid.is_empty() && aid != "auto" {
            tool_names = tools::agent_tools(&st.settings.root_dir, aid).await;
            if !tool_names.is_empty() {
                resolved_agent = Some(aid.to_string());
            }
        }
    }
    if tool_names.is_empty() {
        let q = b.message.to_lowercase();
        // Mot entier (frontieres non-alphanumeriques) : evite les faux positifs
        // type « liste » dans « photorealiSte », « son » dans « perSonne »...
        let mot = |m: &str| {
            let Some(p) = q.find(m) else { return false };
            let avant = q[..p].chars().last().map(|c| c.is_alphanumeric()).unwrap_or(false);
            let apres = q[p + m.len()..].chars().next().map(|c| c.is_alphanumeric()).unwrap_or(false);
            !avant && !apres
        };
        // Intentions distinctes : IMAGE 2D (generate_image) vs 3D (pipeline cad3d).
        // Une simple demande d'image ne doit PAS partir dans la chaine 3D lourde.
        let mot_img2d = ["image", "images", "dessin", "illustration", "logo", "affiche",
            "peinture", "poster", "icone", "wallpaper", "croquis",
            "portrait", "paysage", "photo", "picture"].iter().any(|k| mot(k));
        let mot_3d = ["3d", "stl", "scad", "mesh", "imprim", "blender",
            "figurine", "objet", "support", "g-code", "gcode", "tranche",
            "texture", "sculpt", "telephone"].iter().any(|k| mot(k));
        // Verbes de creation visuelle ("genere", "dessine", "cree-moi"...).
        let verbe_image = ["genere", "génère", "generer", "générer", "creer",
            "créer", "cree", "crée", "dessine", "dessiner", "illustre",
            "peins", "peindre", "fais-moi", "fais moi", "montre-moi",
            "montre moi", "imagine"].iter().any(|k| mot(k));
        // Domaines NON visuels : ne doivent PAS partir vers l'agent image.
        let non_image = ["code", "script", "fonction", "programme",
            "algorithme", "python", "javascript", "typescript", "sql",
            "html", "css", "site", "web", "app", "application",
            "resume", "résume", "explique", "traduis", "traduction",
            "poeme", "poème", "email", "mail", "lettre", "calcul",
            "liste", "tableau", "rapport", "document", "texte",
            "paragraphe", "presentation", "musique", "son", "video",
            "vidéo", "chanson", "histoire", "idee", "idée",
            "mot de passe", "password"].iter().any(|k| mot(k));
        // Presence d'un determiner d'objet ("un", "une", "des"...).
        let determiner = [" un ", " une ", " des ", " le ", " la ",
            " les ", "un ", "une ", "des "].iter().any(|k| q.contains(k));
        let is_3d = ["blender", "3d", "stl", "scad", "mesh", "objet", "support",
            "imprim", "tranche", "g-code", "gcode", "telephone", "figurine",
            "image", "photo", "dessin", "genere", "génère", "creer", "créer",
            "rendu", "render", "texture", "sculpt"].iter().any(|k| mot(k));
        // Garde-fou : "creer/generer" seuls (sans objet visuel/3D) ne routent pas.
        let visual = ["blender", "3d", "stl", "scad", "mesh", "imprim", "image",
            "photo", "rendu", "render", "texture", "figurine", "telephone"].iter().any(|k| mot(k));
        // Agent Bureau : documents professionnels (rapport, lettre, CV, tableur).
        let bureau_mot = ["rapport", "lettre", "cv", "curriculum", "excel",
            "xlsx", "word", "docx", "pdf", "compte rendu", "compte-rendu",
            "note de service", "memo", "memoire", "fiche", "synthese",
            "synthèse", "document", "documents", "tableur", "presentation",
            "diaporama", "facture", "devis", "compte-rendu", "compte rendu"];
        let bureau_verbe = ["redige", "rediger", "rédige", "rédiger", "ecris",
            "ecrire", "écris", "écrire", "cree", "creer", "crée", "créer",
            "genere", "generer", "génère", "générer", "fais", "faire",
            "prepare", "preparer", "préparer", "produis", "produire"];
        // Agent Systeme : meteo, stats PC, volume, apps, ecran.
        let sys_mots = ["meteo", "météo", "temperature", "température", "pluie",
            "pleut", "statistiques", "stats", "ram", "cpu", "gpu", "processeur",
            "disque", "ecran", "écran", "erreur", "capture", "volume", "son",
            "notepad", "bloc notes", "calculatrice", "chrome", "firefox", "edge",
            "vlc", "explorateur", "paint", "parametres", "réglages", "reglages"];
        let sys_verbes = ["ouvre", "ouvrir", "lance", "lancer", "demarre",
            "démarrer", "ferme", "fermer", "monte", "monter", "baisse",
            "baisser", "augmente", "augmenter", "diminue", "diminuer", "coupe",
            "couper", "affiche", "montre", "montrer", "donne", "donner",
            "mets", "met", "quel temps", "quelle meteo"];
        let sys_direct = ["meteo", "météo", "quel temps", "temps qu il",
            "temps qu'il", "statistiques du pc", "stats du pc", "etat du pc",
            "état du pc", "usage de la ram", "erreur a l ecran",
            "erreur à l écran", "erreur a l'ecran", "erreur à l'écran"]
            .iter()
            .any(|k| q.contains(k));
        let veut_systeme = sys_direct
            || (sys_mots.iter().any(|k| mot(k))
                && sys_verbes.iter().any(|k| mot(k)));
        // Creation visuelle : nom d'image explicite OU (verbe de creation +
        // determiner d'objet), sans domaine concurrent (code/texte) ni 3D.
        let veut_image = (mot_img2d && verbe_image)
            || (verbe_image && determiner && !non_image);
        if veut_image && !mot_3d && !non_image {
            // Image 2D -> agent dedie ; repli sur les outils directs si l'agent
            // n'est pas defini. generate_image sera force au round 0.
            tool_names = tools::agent_tools(&st.settings.root_dir, "image").await;
            if tool_names.is_empty() {
                tool_names = vec![
                    "generate_image".to_string(),
                    "analyze_image".to_string(),
                ];
            }
            resolved_agent = Some("image".to_string());
        } else if bureau_mot.iter().any(|k| mot(k))
            && bureau_verbe.iter().any(|k| mot(k))
        {
            // Agent Bureau : redaction de documents professionnels.
            tool_names = tools::agent_tools(&st.settings.root_dir, "bureau").await;
            if !tool_names.is_empty() {
                resolved_agent = Some("bureau".to_string());
            }
        } else if is_3d && visual {
            tool_names = tools::agent_tools(&st.settings.root_dir, "cad3d").await;
            if !tool_names.is_empty() {
                resolved_agent = Some("cad3d".to_string());
            }
        } else if veut_systeme && !non_image {
            // Agent Systeme : meteo, stats PC, volume, apps, ecran.
            tool_names = tools::agent_tools(&st.settings.root_dir, "systeme").await;
            if !tool_names.is_empty() {
                resolved_agent = Some("systeme".to_string());
            }
        } else {
            // Fallback : routeur Python (code->dev, etc.).
            let routed = tools::route_agent(&st.settings.root_dir, &b.message).await;
            if !routed.is_empty() && routed != "general" {
                tool_names = tools::agent_tools(&st.settings.root_dir, &routed).await;
                if !tool_names.is_empty() {
                    resolved_agent = Some(routed);
                }
            }
        }
    }
    if tool_names.is_empty() && b.tools_enabled.unwrap_or(false) {
        tool_names = tools::default_tools();
    }
    // Images jointes : sauvegardees dans le workspace (vision/edition).
    let mut imgs_saved: Vec<String> = Vec::new();
    if let Some(imgs) = b.images.as_ref() {
        if !imgs.is_empty() {
            let horodatage: String = now
                .chars()
                .map(|c| if c.is_alphanumeric() { c } else { '_' })
                .collect();
            for (i, img) in imgs.iter().enumerate() {
                if let Some(p) =
                    save_attached_image(&st.settings.root_dir, img, &horodatage, i).await
                {
                    imgs_saved.push(p);
                }
            }
        }
    }
    // Edition d'image : l'utilisateur joint une image et demande de la
    // modifier -> agent image (l'image est sur disque, chemin en contexte).
    let edit_intent = !imgs_saved.is_empty()
        && [
            "modifie", "modifier", "retouche", "retoucher", "transforme",
            "transformer", "change", "changer", "recadre", "recadrer",
            "ameliore", "ameliorer", "edite", "editer", "colore", "colorise",
            "enleve", "supprime", "remplace", "remplacer", "redessine",
        ]
        .iter()
        .any(|k| b.message.to_lowercase().contains(k));
    if edit_intent {
        tool_names = tools::agent_tools(&st.settings.root_dir, "image").await;
        if tool_names.is_empty() {
            tool_names = vec![
                "generate_image".to_string(),
                "edit_image".to_string(),
            ];
        }
        resolved_agent = Some("image".to_string());
    }
    if b.web_enabled.unwrap_or(false) {
        for w in ["web_search", "web_fetch"] {
            if !tool_names.iter().any(|t| t == w) {
                tool_names.push(w.to_string());
            }
        }
    }
    // System prompt combine : agent + memoirs + RAG (miroir _build_system_prompt).
    // + bascule vision si images jointes a un modele non-vision (sauf edition :
    // l'agent image travaille sur le fichier, pas sur le flux vision).
    let mut model = model;
    let mut images_payload = b.images.clone();
    if edit_intent {
        images_payload = None;
    } else if let Some(imgs) = b.images.as_ref() {
        if !imgs.is_empty() && !is_vision_model(&model) {
            model = "qwen2.5vl:7b".to_string();
        }
    }
    // Routeur adaptatif (opt-in, desactive par defaut via le reglage
    // `routeur_adapte`) : une requete courte SANS outil part sur le modele
    // rapide (Q5/Q6), sinon le modele par defaut (Q8). Aucune bascule si des
    // outils sont impliques (qualite de selection d'outil prioritaire).
    if b.model.as_deref().unwrap_or("auto") == "auto"
        && st.db.get_setting("routeur_adapte", "0") == "1"
    {
        let rapide = st
            .db
            .get_setting("model_rapide", "localai-prog-v9.2-q5km:latest");
        // Dispatch : par defaut TOUT passe sur le modele rapide (meme
        // fine-tune, Q5_K_M a ~105 t/s). dispatch_tout=0 restaure l'ancien
        // comportement (requetes courtes sans outils uniquement).
        let tout = st.db.get_setting("dispatch_tout", "1") == "1";
        if !rapide.is_empty()
            && (tout || (tool_names.is_empty() && b.message.chars().count() <= 120))
        {
            model = rapide;
        }
    }
    {
        let mut parts: Vec<String> = Vec::new();
        // Langue de reponse choisie (installation / reglages) — defaut francais.
        let langue = st.db.get_setting("language", "fr");
        let directive = match langue.as_str() {
            "en" => "IMPORTANT: Always answer in English.",
            "es" => "IMPORTANTE: Responde siempre en espanol.",
            "de" => "WICHTIG: Antworte immer auf Deutsch.",
            "it" => "IMPORTANTE: Rispondi sempre in italiano.",
            _ => "Reponds toujours en francais.",
        };
        parts.push(directive.to_string());
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
        // Regles d'auto-amelioration (coach hebdomadaire / feedback).
        if let Ok(regles) = st.db.query_all_json(
            "SELECT regle FROM ameliorations WHERE active=1 ORDER BY id DESC LIMIT 5", &[]) {
            let lines: Vec<String> = regles.iter().filter_map(|m| {
                m.get("regle")?.as_str().map(|c| format!("- {c}"))
            }).collect();
            if !lines.is_empty() {
                parts.push(format!(
                    "REGLES D'AUTO-AMELIORATION apprises de ton experience (applique-les):\n{}",
                    lines.join("\n")
                ));
            }
        }
        // Images jointes : chemins sur disque pour l'agent (edit_image...).
        if !imgs_saved.is_empty() {
            parts.push(format!(
                "IMAGES JOINTES PAR L'UTILISATEUR: {}\n\
                 Si la demande porte sur la modification d'une de ces images, \
                 utilise l'outil edit_image avec ce chemin.",
                imgs_saved.join(", ")
            ));
        }
        // Dossier de travail (sandbox) : indique aux agents a outils le chemin
        // ABSOLU autorise en ecriture. Evite les chemins relatifs ou inventes
        // (type /home/...) refuses par la sandbox -> boucle sans resultat.
        if !tool_names.is_empty() {
            let ws = tools::sandbox_root(&st.settings.root_dir);
            parts.push(format!(
                "DOSSIER DE TRAVAIL (sandbox, ecriture autorisee) : {ws}\n\
                 Utilise TOUJOURS des chemins ABSOLUS sous ce dossier pour \
                 write_file, render_scad, slice_model et toute sortie de fichier \
                 (ex: {ws}\\mon_fichier.scad ou {ws}\\mon_fichier.stl). \
                 N'utilise JAMAIS de chemin relatif, ni de chemin type /home/... \
                 ou /tmp. C'est le dossier de destination par defaut : ne le \
                 demande pas, utilise-le directement."
            ));
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
        "agent": resolved_agent.clone().unwrap_or_default(),
        "tools": tool_names.len(),
        "eta_ms": eta_ms_pour(&tool_names),
    })));

    // Garde-fou deterministe : pour une demande d'image 2D (agent "image"),
    // garantir l'appel a generate_image des le premier round de la boucle.
    let force_image = resolved_agent.as_deref() == Some("image");
    let msg_force = b.message.clone();

    if tool_names.is_empty() {
        // Chemin rapide : streaming direct (inchange).
        let db2 = st.db.clone();
        let ollama = st.ollama.clone();
        let model2 = model.clone();
        let body = serde_json::json!({
            "messages": messages_json(&history, &images_payload),
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
                Ok(full) => {
                    let ms = started.elapsed().as_millis() as i64;
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
        let images2 = images_payload.clone();
        let imgs_saved_t = imgs_saved.clone();
        tokio::spawn(async move {
            let started = std::time::Instant::now();
            let specs = tools::specs(&root, &tool_names).await.unwrap_or_default();
            let mut messages = history;
            let mut full = String::new();
            let mut tool_pos: Vec<(usize, String, usize)> = Vec::new();
            let seuil = NUM_CTX * 8 / 10;
            let mut img_called = false;
            let mut ref_path: Option<String> = None;
            let mut ref_desc: Option<String> = None;
            for round in 0..tools::MAX_TOOL_ROUNDS {
                let payload = messages_json(&messages, &images2);
                // Un echec transitoire du LLM ne doit pas perdre le travail
                // des outils : une nouvelle tentative, puis on persiste une
                // reponse explicite (jamais de message assistant fantome).
                let mut tentative = ollama
                    .chat_tools(&model2, payload.clone(), &specs, temperature, 2048)
                    .await;
                if tentative.is_err() {
                    tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                    tentative = ollama
                        .chat_tools(&model2, payload, &specs, temperature, 2048)
                        .await;
                }
                let (text, mut calls) = match tentative {
                    Ok(r) => r,
                    Err(e) => {
                        let err_txt = format!(
                            "\n\n\u{26a0} Erreur du modele pendant le travail ({}). \
                             Les outils ont deja produit leurs fichiers : les chemins \
                             ci-dessus restent valables.",
                            e
                        );
                        full.push_str(&err_txt);
                        let _ = tx.send(sse_data(json!({ "type": "token", "content": err_txt })));
                        break;
                    }
                };
                // Garde-fou image 2D (agent "image") : workflow professionnel
                // deterministe. Pour toute demande d'image (hors style pur),
                // on passe d'abord par find_reference_image (photo du sujet
                // validee par vision) ; le modele enchainera avec
                // generate_image(reference=...) -> fidelite au sujet reel.
                if force_image && round == 0 && !edit_intent {
                    let a_ref = calls.iter().any(|(n, _)| n == "find_reference_image");
                    let msg_bas = b.message.to_lowercase();
                    let stylise = ["logo", "cartoon", "dessin", "aquarelle", "pixel",
                        "anime", "manga", "abstrait", "icone", "emoji", "croquis",
                        "affiche", "bande dessinee", "illustration", "peinture"]
                        .iter()
                        .any(|w| {
                            let Some(p) = msg_bas.find(w) else { return false };
                            let avant = msg_bas[..p].chars().last()
                                .map(|c| c.is_alphanumeric()).unwrap_or(false);
                            let apres = msg_bas[p + w.len()..].chars().next()
                                .map(|c| c.is_alphanumeric()).unwrap_or(false);
                            !avant && !apres
                        });
                    let _ = tx.send(sse_data(json!({
                        "type": "meta",
                        "guard": format!("round0 force_image a_ref={} stylise={} ncalls={}", a_ref, stylise, calls.len()),
                    })));
                    if !a_ref && !stylise {
                        calls.retain(|(n, _)| n != "generate_image");
                        calls.insert(0, (
                            "find_reference_image".to_string(),
                            json!({ "object_query": msg_force.clone() }),
                        ));
                    }
                    if calls.is_empty() {
                        calls.insert(0, (
                            "generate_image".to_string(),
                            json!({ "prompt": msg_force.clone() }),
                        ));
                        img_called = true;
                    }
                }
                if !text.is_empty() {
                    full.push_str(&text);
                    let _ = tx.send(sse_data(json!({ "type": "token", "content": text })));
                }
                if round + 1 >= tools::MAX_TOOL_ROUNDS {
                    break;
                }
                if calls.is_empty() {
                    if force_image && !img_called {
                        // Dernier recours : le modele a travaille sans jamais
                        // appeler d'outil image. On force la bonne action :
                        // edition de l'image jointe, ou generation (avec la
                        // meilleure reference trouvee).
                        if edit_intent {
                            let mut fargs = json!({ "prompt": msg_force.clone() });
                            if let Some(p) = imgs_saved_t.first() {
                                fargs["image_path"] = json!(p.clone());
                            }
                            calls.push(("edit_image".to_string(), fargs));
                            img_called = true;
                        } else {
                            let mut fargs = json!({ "prompt": msg_force.clone() });
                            if let Some(rp) = &ref_path {
                                fargs["reference"] = json!(rp.clone());
                            }
                            if let Some(ds) = &ref_desc {
                                fargs["reference_desc"] = json!(ds.clone());
                            }
                            calls.push(("generate_image".to_string(), fargs));
                            img_called = true;
                        }
                    } else {
                        break;
                    }
                }
                // Message assistant avec les appels (pour le contexte du round suivant).
                messages.push(Msg { role: "assistant".into(), content: text.clone() });
                for (seq, (name, args)) in calls.iter_mut().enumerate() {
                    if name == "generate_image" || name == "edit_image" {
                        img_called = true;
                    }
                    if name == "generate_image" {
                        // Si une reference du sujet a ete validee, l'injecter
                        // systematiquement : fidelite garantie (FLUX Kontext).
                        if let Some(rp) = &ref_path {
                            let vide = args
                                .get("reference")
                                .and_then(|v| v.as_str())
                                .map(|s| s.trim().is_empty())
                                .unwrap_or(true);
                            if vide {
                                args["reference"] = json!(rp.clone());
                            }
                            if let Some(ds) = &ref_desc {
                                let vide_d = args
                                    .get("reference_desc")
                                    .and_then(|v| v.as_str())
                                    .map(|s| s.trim().is_empty())
                                    .unwrap_or(true);
                                if vide_d {
                                    args["reference_desc"] = json!(ds.clone());
                                }
                            }
                        }
                    }
                    // Evenement live : l'outil commence (le panneau du chat
                    // affiche « traitement… » puis bascule sur le resultat).
                    let _ = tx.send(sse_data(json!({
                        "type": "tool_start", "name": name,
                    })));
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
                    if name == "find_reference_image" && ref_path.is_none() {
                        if let Some(l) = result.lines().find(|l| l.contains("MEILLEURE REFERENCE:")) {
                            if let Some(r) = l.split("MEILLEURE REFERENCE:").nth(1) {
                                let p = r.trim().trim_matches('"').to_string();
                                if !p.is_empty() {
                                    ref_path = Some(p);
                                }
                            }
                        }
                        if ref_path.is_some() {
                            if let Some(l) = result.lines().find(|l| l.starts_with("Description:")) {
                                let d: String = l
                                    .trim_start_matches("Description:")
                                    .trim()
                                    .chars()
                                    .take(300)
                                    .collect();
                                if !d.is_empty() {
                                    ref_desc = Some(d);
                                }
                            }
                        }
                    }
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
            // Modele chaud pour le prochain message : SD/vision ont pu
            // l'evincer de la VRAM pendant la boucle d'outils.
            {
                let o = ollama.clone();
                let m = model2.clone();
                tokio::spawn(async move {
                    if !o.loaded_model(&m).await.unwrap_or(false) {
                        let _ = o.preload(&m).await;
                    }
                });
            }
            // Les scores faibles alimentent le coach hebdo (auto-amelioration).
            if full.len() > 60 {
                let ollama_j = ollama.clone();
                let db_j = db2.clone();
                // Juge = le meme modele que le chat (deja charge : aucun
                // chargement/eviction supplementaire sur 10 Go de VRAM).
                let modele_juge = model2.clone();
                let demande: String = b.message.chars().take(1200).collect();
                let reponse: String = full.chars().take(1500).collect();
                tokio::spawn(async move {
                    let prompt_juge = format!(
                        "Evalue la qualite de cette reponse d'assistant sur 10 \
                         (pertinence, exactitude, clarte, concision).\n\
                         DEMANDE: {}\nREPONSE: {}\n\
                         Reponds uniquement: NOTE: X/10 puis une phrase de justification.",
                        demande, reponse
                    );
                    let verdict = ollama_j
                        .chat(&modele_juge, vec![localai_core::ollama::Msg {
                            role: "user".into(),
                            content: prompt_juge,
                        }], 0.2, 90)
                        .await
                        .unwrap_or_default();
                    let mut score = 0i64;
                    for cap in ["NOTE:", "note:"] {
                        if let Some(p) = verdict.find(cap) {
                            if let Ok(n) = verdict[p + cap.len()..]
                                .chars()
                                .take(4)
                                .collect::<String>()
                                .split('/')
                                .next()
                                .unwrap_or("0")
                                .trim()
                                .parse::<i64>()
                            {
                                score = n.clamp(1, 10);
                            }
                            break;
                        }
                    }
                    if score > 0 {
                        crate::feedback::noter_echange(&db_j, cid, score, &verdict);
                    }
                });
            }
        });
    }

    let stream = tokio_stream::wrappers::UnboundedReceiverStream::new(rx);
    let raw = stream.map(|s| {
        let payload = s.trim_start_matches("data: ").trim().to_string();
        Ok::<_, Infallible>(Event::default().data(payload))
    });
    Ok(Sse::new(raw).keep_alive(KeepAlive::default()))
}
