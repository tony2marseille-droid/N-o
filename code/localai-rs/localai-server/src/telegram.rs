//! Poller Telegram natif : getUpdates -> CERVEAU COMPLET (/api/chat avec
//! outils) -> messages de progression + reponse finale. Une tache par
//! message (poll jamais bloque). Demarre au boot si telegram_enabled=1.
use std::sync::Arc;

use crate::AppState;

/// Etat reel du poller (lu par /api/telegram/status).
static POLLER_ACTIF: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn poller_actif() -> bool {
    POLLER_ACTIF.load(std::sync::atomic::Ordering::Relaxed)
}

/// Libelles FR des etapes longues (envoyees en progression Telegram).
fn libelle_etape(nom: &str) -> Option<&'static str> {
    let libelle = match nom {
        "find_reference_image" => "recherche d'une photo de reference…",
        "generate_image" => "generation de l'image 4K (5-8 min)…",
        "edit_image" => "edition de l'image (5-8 min)…",
        "generate_mesh_from_image" => "generation du mesh 3D (2-4 min)…",
        "repair_mesh" => "reparation du mesh 3D…",
        "validate_mesh" | "validate_design" => "controle qualite…",
        "render_scad" => "rendu OpenSCAD…",
        "slice_model" => "tranchage G-code…",
        "blender_run" => "calcul Blender…",
        "blender_render" => "rendu Blender…",
        "document_create" => "creation du document…",
        "deep_research" => "recherche approfondie (2-4 min)…",
        "telecharger_fichier" => "telechargement du fichier…",
        _ => return None,
    };
    Some(libelle)
}

pub async fn envoyer_message(client: &reqwest::Client, token: &str, cid: i64, texte: &str) {
    let _ = client
        .post(format!("https://api.telegram.org/bot{token}/sendMessage"))
        .json(&serde_json::json!({ "chat_id": cid, "text": texte.chars().take(4000).collect::<String>() }))
        .send()
        .await;
}

/// Masters TIFF/EXR : Telegram ne les affiche pas en photo -> remplace
/// par l'apercu JPEG cote a cote (genere par l'outil) quand il existe.
fn chemin_telegram(chemin: &str) -> String {
    let bas = chemin.to_lowercase();
    if bas.ends_with(".tiff") || bas.ends_with(".tif") || bas.ends_with(".exr") {
        if let Some(pos) = chemin.rfind('.') {
            let apercu = format!("{}_apercu.jpg", &chemin[..pos]);
            if std::path::Path::new(&apercu).is_file() {
                return apercu;
            }
        }
    }
    chemin.to_string()
}

/// Envoie une image locale sur Telegram : sendPhoto (<= 10 Mo) sinon
/// sendDocument (jusqu'a 50 Mo, cas des images 4K).
pub async fn envoyer_image(client: &reqwest::Client, token: &str, cid: i64, chemin: &str, legende: &str) {
    let chemin_owned: String = chemin_telegram(chemin);
    let chemin: &str = &chemin_owned;
    let meta = match tokio::fs::metadata(chemin).await {
        Ok(m) => m,
        Err(_) => return,
    };
    let taille = meta.len();
    if taille == 0 || taille > 50_000_000 {
        return;
    }
    let fichier = match tokio::fs::File::open(chemin).await {
        Ok(f) => f,
        Err(_) => return,
    };
    let nom = std::path::Path::new(chemin)
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| "image.png".into());
    let legende: String = legende.chars().take(1000).collect();
    let methode = if taille <= 10_000_000 { "sendPhoto" } else { "sendDocument" };
    let champ = if taille <= 10_000_000 { "photo" } else { "document" };
    let partie = reqwest::multipart::Part::stream(fichier)
        .file_name(nom)
        .mime_str("image/png")
        .unwrap_or_else(|_| reqwest::multipart::Part::text(""));
    let forme = reqwest::multipart::Form::new()
        .text("chat_id", cid.to_string())
        .part(champ, partie)
        .text("caption", legende);
    let _ = client
        .post(format!("https://api.telegram.org/bot{token}/{methode}"))
        .multipart(forme)
        .send()
        .await;
}

/// Extrait les chemins d'images d'un texte de resultat d'outil
/// (formats : "MEILLEURE IMAGE: <chemin>" et "Image generee: <chemin>").
fn chemins_images(texte: &str, deja: &mut Vec<String>) {
    for ligne in texte.lines() {
        for marqueur in ["MEILLEURE IMAGE: ", "Image generee: "] {
            if let Some(i) = ligne.find(marqueur) {
                let reste = ligne[i + marqueur.len()..].trim();
                let chemin: String = reste
                    .split_whitespace()
                    .next()
                    .unwrap_or("")
                    .to_string();
                let bas = chemin.to_lowercase();
                if (bas.ends_with(".png")
                    || bas.ends_with(".jpg")
                    || bas.ends_with(".jpeg")
                    || bas.ends_with(".tiff")
                    || bas.ends_with(".tif")
                    || bas.ends_with(".exr"))
                    && !deja.iter().any(|x| x.eq_ignore_ascii_case(&chemin))
                {
                    deja.push(chemin);
                }
            }
        }
    }
}

/// Reponse via le cerveau LocalAI : POST /api/chat (SSE) consomme ligne par
/// ligne. Les etapes longues declenchent des messages de progression
/// Telegram ; les tokens s'agregent en la reponse finale.
async fn reponse_cerveau(
    port: u16,
    modele: &str,
    message: &str,
    telegram: Option<(reqwest::Client, String, i64)>,
) -> (String, Vec<(String, String)>) {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(1800))
        .connect_timeout(std::time::Duration::from_secs(10))
        .build();
    let client = match client {
        Ok(c) => c,
        Err(e) => return (format!("Erreur client : {e}"), Vec::new()),
    };
    let body = serde_json::json!({
        "message": message,
        "model": modele,
        "agent_id": "auto",
        "temperature": 0.5,
    });
    let resp = client
        .post(format!("http://127.0.0.1:{port}/api/chat"))
        .json(&body)
        .send()
        .await;
    let mut stream = match resp {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => return (format!("Erreur cerveau : HTTP {}", r.status()), Vec::new()),
        Err(e) => return (format!("Erreur cerveau : {e}"), Vec::new()),
    };
    let mut out = String::new();
    let mut buf: Vec<u8> = Vec::new();
    let mut envoyees: Vec<String> = Vec::new();
    let mut images: Vec<(String, String)> = Vec::new();
    let mut stream_octets = stream.bytes_stream();
    use futures_util::StreamExt;
    while let Some(chunk) = stream_octets.next().await {
        let octets = match chunk {
            Ok(b) => b,
            Err(e) => {
                out.push_str(&format!("\n(erreur stream: {e})"));
                break;
            }
        };
        buf.extend_from_slice(&octets);
        while let Some(pos) = buf.iter().position(|&b| b == b'\n') {
            let ligne: Vec<u8> = buf.drain(..=pos).collect();
            let ligne = String::from_utf8_lossy(&ligne).trim().to_string();
            let Some(payload) = ligne.strip_prefix("data: ") else { continue };
            let Ok(ev) = serde_json::from_str::<serde_json::Value>(payload) else { continue };
            match ev.get("type").and_then(|x| x.as_str()).unwrap_or("") {
                "token" => {
                    if let Some(c) = ev.get("content").and_then(|x| x.as_str()) {
                        out.push_str(c);
                    }
                }
                "tool_start" => {
                    if let Some((ref cl, ref tk, cid)) = telegram {
                        let nom = ev.get("name").and_then(|x| x.as_str()).unwrap_or("");
                        if !nom.is_empty() && !envoyees.iter().any(|x| x == nom) {
                            if let Some(libelle) = libelle_etape(nom) {
                                envoyees.push(nom.to_string());
                                envoyer_message(cl, tk, cid, &format!("⏳ Néo : {libelle}")).await;
                            }
                        }
                    }
                }
                "tool_exec" => {
                    if let Some(pv) = ev.get("preview").and_then(|x| x.as_str()) {
                        let mut chemins: Vec<String> = Vec::new();
                        chemins_images(pv, &mut chemins);
                        for c in chemins {
                            // Legende = la ligne d'annonce (chemin + note/fidelite).
                            let legende = pv
                                .lines()
                                .find(|l| l.contains(&c))
                                .unwrap_or("Image generee")
                                .chars()
                                .take(1000)
                                .collect::<String>();
                            images.push((c, legende));
                        }
                    }
                }
                "error" => {
                    out.push_str(&format!(
                        "\n(erreur: {})",
                        ev.get("error").and_then(|x| x.as_str()).unwrap_or("?")
                    ));
                }
                _ => {}
            }
        }
    }
    // Secours : le texte final peut aussi mentionner une image (chemin dans
    // la reponse) sans preview explicite.
    let mut chemins_bruts: Vec<String> = Vec::new();
    chemins_images(&out, &mut chemins_bruts);
    for c in chemins_bruts {
        if !images.iter().any(|(x, _)| x.eq_ignore_ascii_case(&c)) {
            images.push((c, "Image generee par Neo".to_string()));
        }
    }
    if out.trim().is_empty() {
        out = "(reponse vide)".to_string();
    }
    (out, images)
}

pub fn maybe_start(st: Arc<AppState>) {
    tokio::spawn(async move {
        // Attend que tout soit pret.
        tokio::time::sleep(std::time::Duration::from_secs(15)).await;
        let enabled = st.db.get_setting("telegram_enabled", "0") == "1";
        let token = st.db.get_setting("telegram_token", "");
        if !enabled || token.is_empty() {
            tracing::info!("[telegram] poller inactif (non configure)");
            return;
        }
        tracing::info!("[telegram] poller demarre");
        POLLER_ACTIF.store(true, std::sync::atomic::Ordering::Relaxed);
        let modele = st.db.get_setting("telegram_model", "");
        let modele = if modele.is_empty() { "auto".to_string() } else { modele };
        let port = st.settings.port;
        let client = reqwest::Client::new();
        let mut offset: i64 = 0;
        loop {
            let url = format!(
                "https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout=50&allowed_updates=%5B%22message%22%5D");
            let resp = match tokio::time::timeout(
                std::time::Duration::from_secs(70), client.get(&url).send()).await
            {
                Ok(Ok(r)) => r,
                _ => {
                    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                    continue;
                }
            };
            let v: serde_json::Value = match resp.json().await {
                Ok(v) => v,
                Err(_) => continue,
            };
            let arr = v.get("result").and_then(|r| r.as_array()).cloned().unwrap_or_default();
            for upd in arr {
                let uid = upd.get("update_id").and_then(|u| u.as_i64()).unwrap_or(0);
                if uid >= offset {
                    offset = uid + 1;
                }
                let chat_id = upd.get("message").and_then(|m| m.get("chat")).and_then(|c| c.get("id")).and_then(|i| i.as_i64());
                let text = upd.get("message").and_then(|m| m.get("text")).and_then(|t| t.as_str()).unwrap_or("").to_string();
                let (Some(cid), t) = (chat_id, text.trim().to_string()) else { continue };
                if t.is_empty() {
                    continue;
                }
                // Session -> conversation (persistee).
                let now = localai_core::db::now_iso();
                let conv: i64 = match st.db.query_one_json(
                    "SELECT conversation_id AS id FROM telegram_sessions WHERE chat_id=?", &[&cid]) {
                    Ok(Some(r)) => r.get("id").and_then(|v| v.as_i64()).unwrap_or(0),
                    _ => 0,
                };
                let conv = if conv > 0 { conv } else {
                    match st.db.execute(
                        "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?,?,?,?)",
                        &[&t.chars().take(40).collect::<String>().as_str(), &modele.as_str(), &now.as_str(), &now.as_str()]) {
                        Ok(id) => {
                            let _ = st.db.execute(
                                "INSERT INTO telegram_sessions (chat_id, conversation_id, created_at) VALUES (?,?,?)",
                                &[&cid, &id, &now.as_str()]);
                            id
                        }
                        Err(_) => continue,
                    }
                };
                let _ = st.db.execute(
                    "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                    &[&conv, &"user", &t.as_str(), &now.as_str()]);
                // Tache NON bloquante : le poll continue pendant le travail.
                let st2 = st.clone();
                let client2 = client.clone();
                let token2 = token.clone();
                let modele2 = modele.clone();
                let t2 = t;
                tokio::spawn(async move {
                    // Indicateur "Néo est en train d'ecrire..." pendant le travail.
                    let fini = Arc::new(std::sync::atomic::AtomicBool::new(false));
                    let fini2 = fini.clone();
                    let client_typing = client2.clone();
                    let token_typing = token2.clone();
                    let cid_typing = cid;
                    tokio::spawn(async move {
                        while !fini2.load(std::sync::atomic::Ordering::Relaxed) {
                            let _ = client_typing
                                .post(format!("https://api.telegram.org/bot{token_typing}/sendChatAction"))
                                .json(&serde_json::json!({ "chat_id": cid_typing, "action": "typing" }))
                                .send().await;
                            tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                        }
                    });
                    let (answer, images) = reponse_cerveau(
                        port, &modele2, &t2,
                        Some((client2.clone(), token2.clone(), cid)),
                    ).await;
                    fini.store(true, std::sync::atomic::Ordering::Relaxed);
                    let now2 = localai_core::db::now_iso();
                    let _ = st2.db.execute(
                        "INSERT INTO messages (conversation_id, role, content, model, created_at) VALUES (?,?,?,?,?)",
                        &[&conv, &"assistant", &answer.as_str(), &modele2.as_str(), &now2.as_str()]);
                    // Les images seules (le texte vide n'apporte rien) :
                    let texte_seul = answer.trim() != "(reponse vide)" && !answer.trim().is_empty();
                    if texte_seul {
                        envoyer_message(&client2, &token2, cid, &answer).await;
                    }
                    for (chemin, legende) in &images {
                        envoyer_image(&client2, &token2, cid, chemin, legende).await;
                    }
                    if !texte_seul && images.is_empty() {
                        envoyer_message(&client2, &token2, cid, &answer).await;
                    }
                });
            }
        }
    });
}
