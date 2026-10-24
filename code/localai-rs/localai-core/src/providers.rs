//! Fournisseurs cloud (Claude, OpenAI, Gemini, Mistral, Groq, OpenRouter).
//! Format OpenAI-compatible pour tous, plus le format Anthropic natif.
//! Les cles vivent en base (app_settings), jamais dans le code.

use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Format {
    Openai,
    Anthropic,
}

pub struct Fournisseur {
    pub id: &'static str,
    pub nom: &'static str,
    pub base_url: &'static str,
    pub format: Format,
    pub modele_defaut: &'static str,
}

pub const FOURNISSEURS: &[Fournisseur] = &[
    Fournisseur {
        id: "anthropic",
        nom: "Claude (Anthropic)",
        base_url: "https://api.anthropic.com/v1",
        format: Format::Anthropic,
        modele_defaut: "claude-sonnet-4-5",
    },
    Fournisseur {
        id: "openai",
        nom: "OpenAI",
        base_url: "https://api.openai.com/v1",
        format: Format::Openai,
        modele_defaut: "gpt-5.1",
    },
    Fournisseur {
        id: "gemini",
        nom: "Google Gemini",
        base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
        format: Format::Openai,
        modele_defaut: "gemini-3-flash",
    },
    Fournisseur {
        id: "mistral",
        nom: "Mistral",
        base_url: "https://api.mistral.ai/v1",
        format: Format::Openai,
        modele_defaut: "mistral-large-latest",
    },
    Fournisseur {
        id: "groq",
        nom: "Groq",
        base_url: "https://api.groq.com/openai/v1",
        format: Format::Openai,
        modele_defaut: "llama-3.3-70b-versatile",
    },
    Fournisseur {
        id: "openrouter",
        nom: "OpenRouter",
        base_url: "https://openrouter.ai/api/v1",
        format: Format::Openai,
        modele_defaut: "anthropic/claude-sonnet-4.5",
    },
];

pub fn trouver(id: &str) -> Option<&'static Fournisseur> {
    FOURNISSEURS.iter().find(|f| f.id == id)
}

fn convert_messages(messages: &[crate::ollama::Msg]) -> Value {
    let arr: Vec<Value> = messages
        .iter()
        .map(|m| json!({ "role": m.role, "content": m.content }))
        .collect();
    json!(arr)
}

/// Chat via un fournisseur OpenAI-compatible.
async fn chat_openai(
    client: &reqwest::Client,
    base: &str,
    cle: &str,
    modele: &str,
    messages: &[crate::ollama::Msg],
    temperature: f32,
    max_tokens: u32,
) -> Result<(String, i64, i64), String> {
    let corps = json!({
        "model": modele,
        "messages": convert_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    });
    let rep = client
        .post(format!("{base}/chat/completions"))
        .bearer_auth(cle)
        .json(&corps)
        .send()
        .await
        .map_err(|e| format!("fournisseur: {e}"))?;
    let statut = rep.status();
    let texte = rep.text().await.map_err(|e| format!("lecture: {e}"))?;
    if !statut.is_success() {
        let court: String = texte.chars().take(200).collect();
        return Err(format!("HTTP {statut}: {court}"));
    }
    let v: Value = serde_json::from_str(&texte).map_err(|e| format!("json: {e}"))?;
    let contenu = v
        .get("choices")
        .and_then(|c| c.get(0))
        .and_then(|c| c.get("message"))
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| "reponse sans contenu".to_string())?;
    // Usage (tokens entree/sortie) present chez tous les OpenAI-compatibles.
    let (tin, tout) = v
        .get("usage")
        .map(|u| {
            (
                u.get("prompt_tokens").and_then(|x| x.as_i64()).unwrap_or(0),
                u.get("completion_tokens").and_then(|x| x.as_i64()).unwrap_or(0),
            )
        })
        .unwrap_or((0, 0));
    Ok((contenu, tin, tout))
}

/// Chat via l'API Anthropic (format natif : systeme separe, max_tokens requis).
async fn chat_anthropic(
    client: &reqwest::Client,
    base: &str,
    cle: &str,
    modele: &str,
    messages: &[crate::ollama::Msg],
    temperature: f32,
    max_tokens: u32,
) -> Result<(String, i64, i64), String> {
    let mut systeme = String::new();
    let mut conversation: Vec<Value> = Vec::new();
    for m in messages {
        if m.role == "system" {
            systeme.push_str(&m.content);
            systeme.push_str("\n\n");
        } else {
            conversation.push(json!({ "role": m.role, "content": m.content }));
        }
    }
    let mut corps = json!({
        "model": modele,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": conversation,
    });
    if !systeme.trim().is_empty() {
        corps["system"] = json!(systeme.trim());
    }
    let rep = client
        .post(format!("{base}/messages"))
        .header("x-api-key", cle)
        .header("anthropic-version", "2023-06-01")
        .json(&corps)
        .send()
        .await
        .map_err(|e| format!("fournisseur: {e}"))?;
    let statut = rep.status();
    let texte = rep.text().await.map_err(|e| format!("lecture: {e}"))?;
    if !statut.is_success() {
        let court: String = texte.chars().take(200).collect();
        return Err(format!("HTTP {statut}: {court}"));
    }
    let v: Value = serde_json::from_str(&texte).map_err(|e| format!("json: {e}"))?;
    let contenu = v
        .get("content")
        .and_then(|c| c.as_array())
        .and_then(|a| {
            a.iter()
                .filter_map(|b| b.get("text").and_then(|t| t.as_str()))
                .collect::<Vec<&str>>()
                .first()
                .map(|s| s.to_string())
        })
        .ok_or_else(|| "reponse sans contenu".to_string())?;
    let (tin, tout) = v
        .get("usage")
        .map(|u| {
            (
                u.get("input_tokens").and_then(|x| x.as_i64()).unwrap_or(0),
                u.get("output_tokens").and_then(|x| x.as_i64()).unwrap_or(0),
            )
        })
        .unwrap_or((0, 0));
    Ok((contenu, tin, tout))
}

/// Chat unifie : choisit le format selon le fournisseur (sans compteurs).
pub async fn chat(
    client: &reqwest::Client,
    fournisseur: &Fournisseur,
    cle: &str,
    modele: &str,
    messages: &[crate::ollama::Msg],
    temperature: f32,
    max_tokens: u32,
) -> Result<String, String> {
    let (texte, _, _) = chat_usage(
        client, fournisseur, cle, modele, messages, temperature, max_tokens,
    )
    .await?;
    Ok(texte)
}

/// Chat unifie AVEC compteurs de tokens consommes (entree, sortie).
/// Sert au suivi de consommation par fournisseur et a l encart tokens.
pub async fn chat_usage(
    client: &reqwest::Client,
    fournisseur: &Fournisseur,
    cle: &str,
    modele: &str,
    messages: &[crate::ollama::Msg],
    temperature: f32,
    max_tokens: u32,
) -> Result<(String, i64, i64), String> {
    let modele = if modele.is_empty() || modele == "defaut" {
        fournisseur.modele_defaut
    } else {
        modele
    };
    match fournisseur.format {
        Format::Openai => {
            chat_openai(client, fournisseur.base_url, cle, modele, messages, temperature, max_tokens).await
        }
        Format::Anthropic => {
            chat_anthropic(client, fournisseur.base_url, cle, modele, messages, temperature, max_tokens).await
        }
    }
}

/// Detecte une erreur de quota/credit epuise chez un fournisseur cloud
/// (402 payment required, 429 rate/quota, insufficient_credits, billing...).
/// Utilise par la bascule automatique vers un modele LOCAL.
pub fn est_quota_epuise(err: &str) -> bool {
    let e = err.to_lowercase();
    e.contains("402")
        || e.contains("429")
        || e.contains("insufficient")
        || e.contains("quota")
        || e.contains("credit")
        || e.contains("exceeded")
        || e.contains("billing")
        || e.contains("payment")
}
