//! Correctif vision : sanitisation base64, injection des images dans le payload Ollama,
//! forçage du modèle vision, limite de corps HTTP.
use axum::extract::DefaultBodyLimit;
use serde_json::{json, Value};

pub const VISION_MODEL: &str = "qwen2.5vl:7b";
pub const DEFAULT_PROMPT: &str = "Décris cette image en détail.";
pub const VISION_SYSTEM: &str =
    "Des images sont jointes au dernier message de l'utilisateur. Tu les vois : analyse-les réellement \
     (objets, texte, couleurs, contexte) et réponds en français à la question posée.";

/// Axum limite le JSON à 2 Mo par défaut : une photo en base64 dépasse => rejet silencieux (413).
/// À ajouter au routeur : `.layer(vision::body_limit())`
pub fn body_limit() -> DefaultBodyLimit {
    DefaultBodyLimit::max(64 * 1024 * 1024)
}

/// Ollama attend du base64 BRUT : retire "data:image/...;base64," et les espaces/retours ligne.
pub fn clean_b64(s: &str) -> Option<String> {
    let raw = s.split_once("base64,").map(|(_, b)| b).unwrap_or(s);
    let raw: String = raw.chars().filter(|c| !c.is_whitespace()).collect();
    (raw.len() >= 16).then_some(raw)
}

/// Injecte les images dans le dernier message `user` (champ `images`), garde le texte de l'utilisateur,
/// retire les images des anciens messages, ajoute le prompt système vision. Renvoie le nombre d'images valides.
pub fn attach_images(messages: &mut Vec<Value>, images: &[String]) -> usize {
    let imgs: Vec<String> = images.iter().filter_map(|s| clean_b64(s)).collect();
    if imgs.is_empty() {
        return 0;
    }
    for m in messages.iter_mut() {
        if let Some(o) = m.as_object_mut() {
            o.remove("images");
        }
    }
    let n = imgs.len();
    match messages.iter_mut().rev().find(|m| m["role"] == "user") {
        Some(m) => {
            if m["content"].as_str().map_or(true, |t| t.trim().is_empty()) {
                m["content"] = json!(DEFAULT_PROMPT);
            }
            m["images"] = json!(imgs);
        }
        None => messages.push(json!({"role":"user","content":DEFAULT_PROMPT,"images":imgs})),
    }
    messages.insert(0, json!({"role":"system","content":VISION_SYSTEM}));
    n
}
