//! Connecteurs : gestion generique des cles API des services externes
//! (Meshy pour la 3D haute qualite, Telegram, Discord). Les cles vivent en
//! base locale (app_settings), jamais dans le code. Les valeurs ne sont
//! jamais renvoyees en clair : seulement leur presence.
use std::sync::Arc;

use axum::{
    extract::State,
    routing::{get, post, put},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use localai_core::AppError;

/// Services et champs autorises (cle DB : connecteur_<service>_<champ>).
const SERVICES: &[(&str, &[&str])] = &[
    ("meshy", &["token"]),
    ("telegram", &["token"]),
    ("discord", &["token"]),
];

fn cle_db(service: &str, champ: &str) -> Option<String> {
    if SERVICES.iter().any(|(s, champs)| *s == service && champs.contains(&champ)) {
        Some(format!("connecteur_{service}_{champ}"))
    } else {
        None
    }
}

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/connecteurs", get(liste))
        .route("/api/connecteurs", put(enregistrer))
        .route("/api/connecteurs/test", post(tester))
}

/// Liste : pour chaque service/champ, present ? (valeur jamais renvoyee).
async fn liste(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let services: Vec<Value> = SERVICES
        .iter()
        .map(|(s, champs)| {
            let champs_v: Vec<Value> = champs
                .iter()
                .map(|c| {
                    let v = st.db.get_setting(&cle_db(s, c).unwrap_or_default(), "");
                    json!({ "champ": c, "present": !v.is_empty() })
                })
                .collect();
            json!({ "service": s, "champs": champs_v })
        })
        .collect();
    Ok(Json(json!({ "services": services })))
}

#[derive(Debug, Deserialize)]
struct CorpsEnregistrer {
    service: String,
    champ: String,
    valeur: String,
}

/// Enregistre/remplace (valeur vide = efface la cle).
async fn enregistrer(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<CorpsEnregistrer>,
) -> ApiResult<Json<Value>> {
    let cle = cle_db(&b.service, &b.champ).ok_or_else(|| {
        ApiError(AppError::BadRequest(format!(
            "connecteur inconnu : {}/{}",
            b.service, b.champ
        )))
    })?;
    if b.valeur.trim().is_empty() {
        st.db.set_setting(&cle, "")?;
    } else {
        st.db.set_setting(&cle, b.valeur.trim())?;
    }
    Ok(Json(json!({ "ok": true, "present": !b.valeur.trim().is_empty() })))
}

#[derive(Debug, Deserialize)]
struct CorpsTester {
    service: String,
}

/// Test reel par service (sans consommer de credits) :
/// - telegram : getMe (gratuit, verifie le token)
/// - discord  : /users/@me avec le token bot
/// - meshy    : autorisation formelle (401 vs autre) sans tache facturable
async fn tester(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<CorpsTester>,
) -> ApiResult<Json<Value>> {
    let champ = "token";
    let cle = cle_db(&b.service, champ).ok_or_else(|| {
        ApiError(AppError::BadRequest(format!("connecteur inconnu : {}", b.service)))
    })?;
    let token = st.db.get_setting(&cle, "");
    if token.is_empty() {
        return Ok(Json(json!({ "ok": false, "detail": "cle absente" })));
    }
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("client: {e}"))))?;
    let mut verdict_ok = false;
    let detail = match b.service.as_str() {
        "telegram" => {
            let r = client
                .get(format!("https://api.telegram.org/bot{token}/getMe"))
                .send()
                .await;
            match r {
                Ok(resp) if resp.status().is_success() => {
                    verdict_ok = true;
                    "telegram OK — bot valide".to_string()
                }
                Ok(_) => "telegram REFUSE — token invalide".to_string(),
                Err(e) => format!("telegram injoignable : {e}"),
            }
        }
        "discord" => {
            let r = client
                .get("https://discord.com/api/v10/users/@me")
                .header("Authorization", format!("Bot {token}"))
                .send()
                .await;
            match r {
                Ok(resp) if resp.status().is_success() => {
                    verdict_ok = true;
                    "discord OK — bot valide".to_string()
                }
                Ok(_) => "discord REFUSE — token invalide".to_string(),
                Err(e) => format!("discord injoignable : {e}"),
            }
        }
        "meshy" => {
            let r = client
                .get("https://api.meshy.ai/openapi/v2/tasks/verification-ping")
                .header("Authorization", format!("Bearer {token}"))
                .send()
                .await;
            match r {
                // 404 sur une tache inexistante = la cle PASSE l'authentification.
                Ok(resp) if resp.status().as_u16() != 401 && resp.status().as_u16() != 403 => {
                    verdict_ok = true;
                    "meshy OK — cle acceptee".to_string()
                }
                Ok(_) => "meshy REFUSE — token invalide".to_string(),
                Err(e) => format!("meshy injoignable : {e}"),
            }
        }
        autre => format!("service inconnu : {autre}"),
    };
    Ok(Json(json!({ "ok": verdict_ok, "detail": detail })))
}
