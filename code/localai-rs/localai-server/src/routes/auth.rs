//! Acces Web UI : pseudo + mot de passe configures a l'installation
//! (onboarding) ou dans les reglages. Le login delivre le token API que
//! le navigateur presente ensuite en "Authorization: Bearer" (middleware
//! main.rs). SSH : voir la note en fin de module.

use std::sync::Arc;

use axum::{extract::State, routing::{get, post}, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::error::{ApiError, ApiResult};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/auth/status", get(status))
        .route("/api/auth/login", post(login))
        .route("/api/auth/setup", post(setup))
}

/// Hache un mot de passe : sha256(salt + mot de passe), hex.
fn hacher(salt: &str, pass: &str) -> String {
    let mut h = Sha256::new();
    h.update(salt.as_bytes());
    h.update(pass.as_bytes());
    format!("{:x}", h.finalize())
}

/// Sel aleatoire (nanos + pid) : suffisant pour un reseau local.
fn sel_aleatoire() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id();
    let mut h = Sha256::new();
    h.update(nanos.to_le_bytes());
    h.update(pid.to_le_bytes());
    format!("{:x}", h.finalize())[..32].to_string()
}

/// Verifie un mot de passe contre le format stocke "sha256$salt$hash".
fn pass_ok(stocke: &str, saisi: &str) -> bool {
    let mut parties = stocke.split('$');
    let algo = parties.next().unwrap_or("");
    let salt = parties.next().unwrap_or("");
    let hash = parties.next().unwrap_or("");
    algo == "sha256" && !salt.is_empty() && hacher(salt, saisi) == hash
}

/// GET /api/auth/status : etat de l'acces (route publique, avant login).
async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let token = st.db.get_setting("api_token", "");
    let user = st.db.get_setting("webui_user", "");
    Ok(Json(json!({
        "active": !token.is_empty(),
        "user": user,
    })))
}

#[derive(Debug, Deserialize)]
pub struct CorpsLogin {
    pub user: String,
    pub pass: String,
}

/// POST /api/auth/login : verifie pseudo + mot de passe, delivre le token
/// que le navigateur presentera en Bearer (route publique).
async fn login(State(st): State<Arc<crate::AppState>>, Json(b): Json<CorpsLogin>)
    -> ApiResult<Json<Value>>
{
    let user = st.db.get_setting("webui_user", "");
    let stocke = st.db.get_setting("webui_pass", "");
    let token = st.db.get_setting("api_token", "");
    let pseudo = b.user.trim();
    if user.is_empty() || stocke.is_empty() || token.is_empty() {
        return Ok(Json(json!({ "ok": false, "error": "acces non configure" })));
    }
    if pseudo != user || !pass_ok(&stocke, &b.pass) {
        return Ok(Json(json!({ "ok": false, "error": "pseudo ou mot de passe incorrect" })));
    }
    Ok(Json(json!({ "ok": true, "token": token })))
}

#[derive(Debug, Deserialize)]
pub struct CorpsSetup {
    pub user: String,
    pub pass: String,
    pub old_pass: Option<String>,
}

/// POST /api/auth/setup : definit (ou remplace) le pseudo + mot de passe et
/// genere le token d'acces. Remplacement protege par l'ancien mot de passe.
async fn setup(State(st): State<Arc<crate::AppState>>, Json(b): Json<CorpsSetup>)
    -> ApiResult<Json<Value>>
{
    let pseudo = b.user.trim();
    if pseudo.len() < 2 {
        return Err(ApiError(localai_core::AppError::BadRequest(
            "pseudo trop court (2 caracteres minimum)".into(),
        )));
    }
    if b.pass.len() < 4 {
        return Err(ApiError(localai_core::AppError::BadRequest(
            "mot de passe trop court (4 caracteres minimum)".into(),
        )));
    }
    let stocke = st.db.get_setting("webui_pass", "");
    if !stocke.is_empty() {
        // Deja configure : exiger l'ancien mot de passe pour changer.
        let ancien = b.old_pass.clone().unwrap_or_default();
        if !pass_ok(&stocke, &ancien) {
            return Ok(Json(json!({
                "ok": false,
                "error": "ancien mot de passe requis pour modifier l'acces",
            })));
        }
    }
    let salt = sel_aleatoire();
    let hash = format!("sha256${salt}${}", hacher(&salt, &b.pass));
    st.db.set_setting("webui_user", pseudo)?;
    st.db.set_setting("webui_pass", &hash)?;
    // Token d'acces (Bearer) regenere.
    let token = sel_aleatoire() + &sel_aleatoire();
    st.db.set_setting("api_token", &token)?;
    Ok(Json(json!({ "ok": true, "token": token })))
}

// NOTE SSH (consigne installation) : l'acces SSH se gere au niveau de
// l'OS (serveur OpenSSH Windows + compte utilisateur), pas dans le
// serveur web. L'onboarding affiche la procedure et le port ; la
// creation effective reste une operation systeme volontaire.
