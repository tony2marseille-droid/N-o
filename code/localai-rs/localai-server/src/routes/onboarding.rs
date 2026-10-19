//! Assistant de premiere installation : langue choisie, materiel detecte et
//! plan de modeles adapte a la configuration (T1 petit, T3 grand).
use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use serde_json::{json, Value};

use crate::error::ApiResult;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new().route("/api/onboarding", get(etat))
}

async fn etat(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let code = r#"
from backend.core.hardware import (build_profile, resolve_plan,
    recommended_pulls, modeles_optionnels)
import httpx, json
try:
    tags = [m.get('name', '') for m in
            httpx.get('http://127.0.0.1:11434/api/tags', timeout=6).json().get('models', [])]
except Exception:
    tags = []
prof = build_profile(tags)
tier = prof.get('tier', 'T1')
print(json.dumps({
    'tier': tier,
    'gpus': prof.get('gpus', []),
    'ram_gb': prof.get('ram_gb', 0),
    'disk': prof.get('disk', {}),
    'cpu_only': prof.get('cpu_only', False),
    'recommended': recommended_pulls(tier),
    'optional': modeles_optionnels(tier),
    'plan': resolve_plan(tier, tags),
    'installed': tags,
}, default=str))
"#;
    let hw = crate::pybridge::py_json(&st.settings.root_dir, code).await?;
    let langue = st.db.get_setting("language", "fr");
    let onboarded = st.db.get_setting("onboarded", "0");
    Ok(Json(json!({
        "language": langue,
        "onboarded": onboarded == "1",
        "hardware": hw,
    })))
}
