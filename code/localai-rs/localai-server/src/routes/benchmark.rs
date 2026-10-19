use std::sync::Arc;

use axum::{extract::State, routing::{get, post}, Json, Router};

use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::ApiResult;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/agents", get(agents))
        .route("/api/benchmark", post(benchmark))
}

async fn agents(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = crate::pybridge::py_json(&st.settings.root_dir,
        "from backend.core.agents import list_agents; print(json.dumps({'agents': list_agents()}, default=str))").await?;
    Ok(Json(v))
}

const TESTS: &[(&str, &str, &str)] = &[
    ("maths_1", "Calcule 17*24-89. Reponds uniquement par le nombre, sans explication.", "319"),
    ("maths_2", "Combien font 13*13 ? Reponds uniquement par le nombre.", "169"),
    ("maths_3", "Quel est le plus grand nombre premier inferieur a 20 ? Reponds par le chiffre.", "19"),
    ("maths_4", "Calcule 5+3*4. Reponds uniquement par le nombre.", "17"),
    ("maths_5", "Quel est le reste de la division entiere de 100 par 7 ? Reponds par le chiffre.", "2"),
    ("code_1", "Ecris une fonction Python fizzbuzz(n). Ton code doit contenir le mot fizzbuzz.", "fizzbuzz"),
    ("code_2", "Ecris une fonction Python qui inverse une chaine. Ton code doit contenir le mot def.", "def"),
    ("code_3", "Ecris une fonction Python qui retourne la somme d'une liste. Ton code doit contenir le mot return.", "return"),
    ("code_4", "Ecris une fonction Python qui verifie si un nombre est pair avec %. Ton code doit contenir le mot %.", "%"),
    ("code_5", "Ecris une fonction Python qui calcule le factoriel. Ton code doit contenir le mot def.", "def"),
];

#[derive(Debug, Deserialize, Default)]
pub struct BenchBody {
    pub model: Option<String>,
    pub tags: Option<Vec<String>>,
}

async fn benchmark(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<BenchBody>,
) -> ApiResult<Json<Value>> {
    // Un ou plusieurs modeles (tags Python, ou model unique).
    let mut models: Vec<String> = b.tags.unwrap_or_default();
    if let Some(m) = b.model {
        if !m.trim().is_empty() {
            models.insert(0, m);
        }
    }
    if models.is_empty() {
        models.push("qwen3:0.6b".to_string());
    }
    let mut by_model = Vec::new();
    for model in &models {
        let mut results = Vec::new();
        let mut passed = 0;
        for (id, prompt, expected) in TESTS {
            let body = serde_json::json!({
                "messages": [{"role": "user", "content": prompt}],
                "think": false,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 200,
                    "num_ctx": localai_core::ollama::DEFAULT_NUM_CTX,
                },
            });
            let answer = st
                .ollama
                .chat_stream(model, body, |_| {})
                .await
                .unwrap_or_default();
            let ok = answer.trim().contains(expected);
            if ok {
                passed += 1;
            }
            results.push(json!({ "id": id, "ok": ok, "expected": expected,
                                 "got": answer.trim().chars().take(120).collect::<String>() }));
        }
        by_model.push(json!({ "model": model, "passed": passed, "total": TESTS.len(), "results": results }));
    }
    // Compat : si un seul modele, forme plate comme avant.
    if by_model.len() == 1 {
        return Ok(Json(by_model.into_iter().next().unwrap()));
    }
    Ok(Json(json!({ "models": by_model })))
}
