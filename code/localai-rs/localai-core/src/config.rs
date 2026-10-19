use std::path::PathBuf;

pub const APP_NAME: &str = "LocalAI";
pub const APP_VERSION: &str = "0.3.0";

fn env_str(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_path(key: &str, default: PathBuf) -> PathBuf {
    std::env::var(key).map(PathBuf::from).unwrap_or(default)
}

/// Racine du projet : LOCALAI_ROOT, sinon le dossier du binaire (Rust),
/// sinon E:\ia par defaut sur ce poste.
pub fn default_root() -> PathBuf {
    if let Ok(r) = std::env::var("LOCALAI_ROOT") {
        return PathBuf::from(r);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            // Si le binaire est dans E:\localai-rs\target\..., remonter n'aide pas.
            // On prefere E:\ia si ce dossier existe.
            let candidate = PathBuf::from(r"E:\ia");
            if candidate.is_dir() {
                return candidate;
            }
            return parent.to_path_buf();
        }
    }
    PathBuf::from(r"E:\ia")
}

#[derive(Debug, Clone)]
pub struct Settings {
    pub host: String,
    pub port: u16,
    pub ollama_host: String,
    pub root_dir: PathBuf,
    pub data_dir: PathBuf,
    pub default_model: String,
}

impl Settings {
    pub fn load() -> Self {
        let root = default_root();
        let port: u16 = std::env::var("LOCALAI_PORT")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(8320);
        Self {
            // Note securite : 0.0.0.0 expose sur le LAN (acces distant necessaire).
            // Pas d'auth : ne jamais exposer au WAN. LOCALAI_HOST=127.0.0.1 pour restreindre.
            host: env_str("LOCALAI_HOST", "0.0.0.0"),
            port,
            ollama_host: env_str("OLLAMA_URL", "http://127.0.0.1:11434"),
            data_dir: env_path("LOCALAI_DATA", root.join("data")),
            default_model: env_str("LOCALAI_MODEL", "localai-prog-latest"),
            root_dir: root,
        }
    }

    pub fn db_path(&self) -> PathBuf {
        self.data_dir.join("localai.db")
    }

    pub fn ensure_dirs(&self) {
        for d in [&self.data_dir, &self.data_dir.join("tts")] {
            let _ = std::fs::create_dir_all(d);
        }
    }
}
