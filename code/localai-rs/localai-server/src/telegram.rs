//! Poller Telegram natif : getUpdates -> reponse Ollama -> sendMessage.
//! Demarre au boot si telegram_enabled=1 (remplace le poller Python).
use std::sync::Arc;

use crate::AppState;

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
        // Chantier 2 (sandbox) : run_command/write_file restent desactives sur
        // Telegram tant que telegram_dangerous_tools != 1. Le poller actuel
        // n'execute JAMAIS d'outils (reponse Ollama directe, sans tool-calling) ;
        // ce flag sert de garde-fou documente si la boucle d'outils y est
        // ajoutee un jour. Defaut = 0 (settings.rs demande la liste ALLOWED).
        let dangerous = st.db.get_setting("telegram_dangerous_tools", "0") == "1";
        if !dangerous {
            tracing::info!("[telegram] outils dangereux desactives (defaut sandbox)");
        }
        let model = st.db.get_setting("telegram_model", "");
        let model = if model.is_empty() { st.settings.default_model.clone() } else { model };
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
                // Session -> conversation.
                let now = localai_core::db::now_iso();
                let conv: i64 = match st.db.query_one_json(
                    "SELECT conversation_id AS id FROM telegram_sessions WHERE chat_id=?", &[&cid]) {
                    Ok(Some(r)) => r.get("id").and_then(|v| v.as_i64()).unwrap_or(0),
                    _ => 0,
                };
                let conv = if conv > 0 { conv } else {
                    match st.db.execute(
                        "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?,?,?,?)",
                        &[&t.chars().take(40).collect::<String>().as_str(), &model.as_str(), &now.as_str(), &now.as_str()]) {
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
                let answer = st.ollama.chat(&model,
                    vec![localai_core::ollama::Msg { role: "user".into(), content: t }],
                    0.7, 800).await.unwrap_or_else(|e| format!("Erreur : {e}"));
                let now2 = localai_core::db::now_iso();
                let _ = st.db.execute(
                    "INSERT INTO messages (conversation_id, role, content, model, created_at) VALUES (?,?,?,?,?)",
                    &[&conv, &"assistant", &answer.as_str(), &model.as_str(), &now2.as_str()]);
                let send_url = format!("https://api.telegram.org/bot{token}/sendMessage");
                let _ = client.post(&send_url)
                    .json(&serde_json::json!({ "chat_id": cid, "text": answer.chars().take(4000).collect::<String>() }))
                    .send().await;
            }
        }
    });
}
