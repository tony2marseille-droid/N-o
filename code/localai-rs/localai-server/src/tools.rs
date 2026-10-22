//! Outils : specs via pont Python (cachees), execution via pont.
//! Le Rust pilote la boucle tool-calling, le Python execute (fidelite 100 %).
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

use serde_json::Value;

use crate::error::ApiResult;

static SPEC_CACHE: OnceLock<Mutex<HashMap<String, Value>>> = OnceLock::new();

fn cache() -> &'static Mutex<HashMap<String, Value>> {
    SPEC_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn cache_key(names: &[String]) -> String {
    let mut sorted = names.to_vec();
    sorted.sort();
    sorted.join(",")
}

/// Specs Ollama pour une liste d'outils (via pont, cache en memoire).
pub async fn specs(root: &std::path::Path, names: &[String]) -> ApiResult<Vec<Value>> {
    let key = cache_key(names);
    if let Some(v) = cache().lock().unwrap().get(&key) {
        if let Some(arr) = v.as_array() {
            return Ok(arr.clone());
        }
    }
    let names_py: Vec<String> = names.iter().map(|n| format!("'{}'", n.replace('\'', ""))).collect();
    let code = format!(
        "from backend.tools.executor import ollama_tool_specs; print(json.dumps(ollama_tool_specs([{}])))",
        names_py.join(","));
    let v = crate::pybridge::py_json(root, &code).await?;
    let arr = v.as_array().cloned().unwrap_or_default();
    cache().lock().unwrap().insert(key, Value::Array(arr.clone()));
    Ok(arr)
}

/// Routeur d'intention Python (fallback quand la detection Rust ne matche pas).
/// Retourne l'id d'agent ("general", "dev", ...) ou "" si indecis.
pub async fn route_agent(root: &std::path::Path, message: &str) -> String {
    route_agent_full(root, message).await.0
}

/// Idem route_agent mais retourne AUSSI la raison (intention detectee),
/// affichee en badge dans le chat ("routé : ...").
pub async fn route_agent_full(root: &std::path::Path, message: &str) -> (String, String) {
    let q = message.replace('\'', "").chars().take(500).collect::<String>();
    let code = format!(
        "from backend.core.router import route_agent; r = route_agent('{}'); print(json.dumps(list(r) if isinstance(r, (list, tuple)) else [r, '']))",
        q);
    match crate::pybridge::py_json(root, &code).await {
        Ok(v) => {
            let arr = v.as_array().cloned().unwrap_or_default();
            let agent = arr.first()
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            let raison = arr.get(1)
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            (agent, raison)
        }
        Err(_) => (String::new(), String::new()),
    }
}

/// System prompt d'un agent (bridge). Vide si inconnu.
pub async fn agent_system(root: &std::path::Path, agent_id: &str) -> String {
    let id = agent_id.replace('\'', "");
    let code = format!(
        "from backend.core.agents import get_agent; a = get_agent('{}'); print(json.dumps(a.system_prompt if a and getattr(a, 'system_prompt', None) else ''))",
        id);
    crate::pybridge::py_json(root, &code)
        .await
        .ok()
        .and_then(|v| v.as_str().map(|s| s.to_string()))
        .unwrap_or_default()
}

/// Flag RAG d'un agent (docs -> true). Faux si inconnu.
pub async fn agent_rag(root: &std::path::Path, agent_id: &str) -> bool {
    let id = agent_id.replace('\'', "");
    let code = format!(
        "from backend.core.agents import get_agent; a = get_agent('{}'); print(json.dumps(bool(a and getattr(a, 'rag', False))))",
        id);
    crate::pybridge::py_json(root, &code)
        .await
        .ok()
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

/// Outils d'un agent (via pont). Retourne [] si inconnu.
pub async fn agent_tools(root: &std::path::Path, agent_id: &str) -> Vec<String> {
    let id = agent_id.replace('\'', "");
    let code = format!(
        "from backend.core.agents import get_agent; a = get_agent('{}'); print(json.dumps(a.tools if a and getattr(a, 'tools', None) else []))",
        id);
    crate::pybridge::py_json(root, &code)
        .await
        .ok()
        .and_then(|v| v.as_array().map(|a| {
            a.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()
        }))
        .unwrap_or_default()
}

/// Execute un outil via le pont (permission evaluee cote Python).
/// Retourne le texte resultat (tronque a 6000 comme le Python).
pub async fn execute(
    root: &std::path::Path,
    name: &str,
    args: &Value,
    level: &str,
) -> String {
    if let Some(refus) = precheck(root, name, args) {
        return refus;
    }
    let nm = name.replace('\'', "");
    let lv = level.replace('\'', "");
    let args_json = serde_json::to_string(args).unwrap_or_else(|_| "{}".into());
    // Arguments via fichier (evite les problemes de quoting shell).
    let tmp = std::env::temp_dir().join(format!("tool_args_{}.json", std::process::id()));
    if std::fs::write(&tmp, &args_json).is_err() {
        return format!("Erreur: impossible d'ecrire les arguments de {nm}").to_string();
    }
    let code = format!(
        "import json; from backend.tools.executor import execute_tool, PermissionManager; \
         args = json.load(open(r'{}')); \
         print(execute_tool('{}', args, PermissionManager('{}')), end='')",
        tmp.display(), nm, lv);
    // Texte brut (pas du JSON) : py_text, timeout long (Blender/SD).
    let out = crate::pybridge::py_text_timeout(
        root,
        &["-c", &code],
        std::time::Duration::from_secs(900),
    )
    .await;
    let _ = std::fs::remove_file(&tmp);
    match out {
        Ok(s) => s.chars().take(6000).collect(),
        Err(e) => format!("Erreur d'execution de {nm}: {e}"),
    }
}

/// Niveau de permission depuis les settings (defaut USER comme le Python).
pub fn tool_level(db: &localai_core::db::Db) -> String {
    db.get_setting("tool_level", "USER").to_uppercase()
}

pub const MAX_TOOL_ROUNDS: usize = 14;

pub fn default_tools() -> Vec<String> {
    ["calculator", "get_datetime", "list_dir", "read_file", "check_code", "deep_research"]
        .iter().map(|s| s.to_string()).collect()
}

// ---- Sandbox chantier 2 : double controle Rust avant le pont ----

pub const SANDBOX_PATH_TOOLS: [&str; 3] = ["read_file", "list_dir", "write_file"];

fn lower(s: &str) -> String {
    s.to_lowercase().replace(['/', '\\'], "\\")
}

fn rules(root: &std::path::Path) -> serde_json::Value {
    let p = root.join("data").join("security_rules.json");
    std::fs::read_to_string(&p)
        .ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .unwrap_or(Value::Null)
}

fn jstrs(v: &Value, key: &str, fallback: &[&str]) -> Vec<String> {
    if let Some(items) = v.get(key).and_then(|x| x.as_array()) {
        let out: Vec<String> = items
            .iter()
            .filter_map(|x| x.as_str().map(|s| s.to_lowercase()))
            .collect();
        if !out.is_empty() { return out; }
    }
    fallback.iter().map(|s| s.to_string()).collect()
}

fn sous_racine(p: &str, racine: &str) -> bool {
    let ps = lower(p);
    let rs = lower(racine);
    if !rs.is_empty() && ps.as_str() != rs.as_str() && ps.starts_with(&rs.as_str()) {
        // doit se terminer au joncteur de dossier exact
        let rs_len = rs.len();
        let suite = &ps[rs_len..];
        suite.starts_with('\\') || rs.ends_with('\\')
    } else {
        ps == rs
    }
}

/// Pre-controle avant pont. Retourne Some(refus) si l'appel doit etre rejete.
pub fn precheck(root: &std::path::Path, name: &str, args: &Value) -> Option<String> {
    let r = rules(root);
    let root_w = r
        .get("sandbox_root")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "E:\\ia\\workspace".to_string());
    let read_roots = {
        let mut v: Vec<String> = r
            .get("sandbox_read_roots")
            .and_then(|x| x.as_array())
            .map(|a| a.iter().filter_map(|s| s.as_str().map(|s| s.to_string())).collect())
            .unwrap_or_default();
        if v.is_empty() {
            v.push(root_w.clone());
            v.push("E:\\ia\\data\\documents".to_string());
        }
        v
    };
    let deny = jstrs(&r, "cmd_deny", &["del", "rmdir", "format", "reg", "schtasks", "net", "sc", "powershell", "pwsh", "cmd", "curl", "wget", "ssh", "scp", "taskkill"]);
    let allow = jstrs(&r, "cmd_allow", &["python", "node", "git", "openscad", "dir", "type", "findstr"]);
    if name == "run_command" {
        let cmd = args
            .get("command")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if cmd.is_empty() {
            return Some("refusé: commande vide".into());
        }
        for ch in ['|', '&', ';', '>', '<', '`'] {
            if cmd.contains(ch) {
                return Some(format!("refusé: caractere interdit en sandbox: {ch}"));
            }
        }
        if cmd.contains("$(") {
            return Some("refusé: substitution de commande interdite ($())".into());
        }
        let head = cmd
            .split_whitespace()
            .next()
            .unwrap_or("")
            .trim_matches('"')
            .to_lowercase();
        let base = head.rsplit(['\\', '/']).next().unwrap_or(&head).to_string();
        if deny.contains(&head) || deny.contains(&base) {
            return Some(format!("refusé: executable interdit en sandbox: {head}"));
        }
        if !allow.contains(&head) && !allow.contains(&base) {
            return Some(format!("refusé: executable hors liste blanche: {head}"));
        }
        return None;
    }
    if SANDBOX_PATH_TOOLS.contains(&name) {
        let raw = args
            .get("path")
            .and_then(|x| x.as_str())
            .unwrap_or("");
        if raw.contains("..\\") || raw.contains("../") {
            return Some("refusé: traversée de dossier interdite (..) ".into());
        }
        if raw.contains(':') && !lower(raw).starts_with(&lower(&root_w)) {
            // chemin absolu : verifier racine (ecriture=workspace, lecture=roots)
            let racines: Vec<String> = if name == "write_file" {
                vec![root_w.clone()]
            } else {
                read_roots.clone()
            };
            if !racines.iter().any(|rac| sous_racine(raw, rac)) {
                return Some(format!("refusé: chemin hors racines autorisées {racines:?}"));
            }
        }
    }
    None
}
