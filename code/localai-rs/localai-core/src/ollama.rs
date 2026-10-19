//! Client Ollama minimal (chat non-stream + stream + tags + ps + generate).
use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};

/// Budget de contexte par défaut pour la RTX 3080 10 Go.
pub const DEFAULT_NUM_CTX: usize = 8192;

#[derive(Clone)]
pub struct Ollama {
    base: String,
    client: reqwest::Client,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Msg {
    pub role: String,
    pub content: String,
}

impl Ollama {
    pub fn new(base: impl Into<String>) -> AppResult<Self> {
        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(900))
            // Pas de pooling : Ollama redemarre regulierement et les sockets
            // gardees deviennent mortes (requetes pendantes jusqu'au timeout).
            // En localhost le cout d'une connexion fraiche est < 1 ms.
            .pool_max_idle_per_host(0)
            .build()?;
        Ok(Self {
            base: base.into().trim_end_matches('/').to_string(),
            client,
        })
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    pub async fn version(&self) -> AppResult<String> {
        let r = self
            .client
            .get(format!("{}/api/version", self.base))
            .send()
            .await?;
        Ok(r.text().await.unwrap_or_default())
    }

    pub async fn tags(&self) -> AppResult<serde_json::Value> {
        let r = self
            .client
            .get(format!("{}/api/tags", self.base))
            .send()
            .await?;
        r.json().await.map_err(AppError::from)
    }

    pub async fn ps(&self) -> AppResult<serde_json::Value> {
        let r = self
            .client
            .get(format!("{}/api/ps", self.base))
            .send()
            .await?;
        r.json().await.map_err(AppError::from)
    }

    pub async fn chat(
        &self,
        model: &str,
        messages: Vec<Msg>,
        temperature: f32,
        num_predict: i32,
    ) -> AppResult<String> {
        // Via le transport stream (le path non-stream d'Ollama 0.40.3 a
        // montre des blocages intermittents ; le stream est fiable).
        let body = serde_json::json!({
            "messages": messages,
            "think": false,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": DEFAULT_NUM_CTX,
            },
        });
        self.chat_stream(model, body, |_| {}).await
    }

    /// Appel non-stream AVEC outils : retourne (texte, tool_calls).
    /// Chaque tool_call = (nom, arguments_json).
    pub async fn chat_tools(
        &self,
        model: &str,
        messages: serde_json::Value,
        tools: &[serde_json::Value],
        temperature: f32,
        num_predict: i32,
    ) -> AppResult<(String, Vec<(String, serde_json::Value)>)> {
        let body = serde_json::json!({
            "model": model,
            "messages": messages,
            "stream": false,
            "think": false,
            "tools": tools,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": DEFAULT_NUM_CTX,
            },
        });
        let r = self
            .client
            .post(format!("{}/api/chat", self.base))
            .json(&body)
            .send()
            .await?;
        if !r.status().is_success() {
            return Err(AppError::Ollama(format!("chat_tools: {}", r.status())));
        }
        let v: serde_json::Value = r.json().await?;
        let text = v
            .get("message")
            .and_then(|m| m.get("content"))
            .and_then(|c| c.as_str())
            .unwrap_or("")
            .to_string();
        let mut calls = Vec::new();
        if let Some(arr) = v.get("message").and_then(|m| m.get("tool_calls")).and_then(|t| t.as_array()) {
            for tc in arr {
                let name = tc
                    .get("function")
                    .and_then(|f| f.get("name"))
                    .and_then(|n| n.as_str())
                    .unwrap_or("")
                    .to_string();
                if name.is_empty() {
                    continue;
                }
                let args = tc
                    .get("function")
                    .and_then(|f| f.get("arguments"))
                    .cloned()
                    .unwrap_or(serde_json::json!({}));
                // Arguments parfois en string JSON.
                let args = match args {
                    serde_json::Value::String(s) => {
                        serde_json::from_str(&s).unwrap_or(serde_json::json!({}))
                    }
                    other => other,
                };
                calls.push((name, args));
            }
        }
        Ok((text, calls))
    }

    /// Chat en streaming : appelle `on_token` pour chaque morceau, retourne le texte complet.
    pub async fn chat_stream<F>(&self, model: &str, body: serde_json::Value, mut on_token: F) -> AppResult<String>
    where
        F: FnMut(&str),
    {
        use futures_util::StreamExt;
        let mut req = serde_json::Map::new();
        if let Some(obj) = body.as_object() {
            for (k, v) in obj {
                req.insert(k.clone(), v.clone());
            }
        }
        req.insert("model".into(), serde_json::Value::from(model));
        req.insert("stream".into(), serde_json::Value::from(true));
        let resp = self
            .client
            .post(format!("{}/api/chat", self.base))
            .json(&serde_json::Value::Object(req))
            .send()
            .await?;
        let mut stream = resp.bytes_stream();
        let mut full = String::new();
        let mut buf: Vec<u8> = Vec::new();
        while let Some(chunk) = stream.next().await {
            let bytes = chunk?;
            buf.extend_from_slice(&bytes);
            while let Some(pos) = buf.iter().position(|&b| b == b'\n') {
                let line: Vec<u8> = buf.drain(..=pos).collect();
                let s = String::from_utf8_lossy(&line);
                let s = s.trim();
                if s.is_empty() {
                    continue;
                }
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(s) {
                    if v.get("done").and_then(|d| d.as_bool()).unwrap_or(false) {
                        continue;
                    }
                    if let Some(t) = v
                        .get("message")
                        .and_then(|m| m.get("content"))
                        .and_then(|c| c.as_str())
                    {
                        full.push_str(t);
                        on_token(t);
                    }
                }
            }
        }
        Ok(full)
    }

    /// Embeddings via /api/embed (nomic-embed-text).
    pub async fn embed(&self, model: &str, inputs: Vec<String>) -> AppResult<Vec<Vec<f32>>> {
        let body = serde_json::json!({ "model": model, "input": inputs });
        let r = self
            .client
            .post(format!("{}/api/embed", self.base))
            .json(&body)
            .send()
            .await?;
        if !r.status().is_success() {
            return Err(AppError::Ollama(format!("embed: {}", r.status())));
        }
        let v: serde_json::Value = r.json().await?;
        let arr = v
            .get("embeddings")
            .and_then(|e| e.as_array())
            .ok_or_else(|| AppError::Ollama("embed: pas de embeddings".into()))?;
        let mut out = Vec::with_capacity(arr.len());
        for emb in arr {
            let vec: Vec<f32> = emb
                .as_array()
                .map(|a| a.iter().filter_map(|x| x.as_f64().map(|f| f as f32)).collect())
                .unwrap_or_default();
            out.push(vec);
        }
        Ok(out)
    }

    pub async fn unload(&self, model: &str) -> AppResult<()> {
        let body = serde_json::json!({"model": model, "keep_alive": 0});
        self.client
            .post(format!("{}/api/generate", self.base))
            .json(&body)
            .send()
            .await?;
        Ok(())
    }
}
