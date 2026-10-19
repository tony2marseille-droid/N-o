//! Daemon de surveillance actif : relance 8320/11434 si down, alertes disque/GPU.
//! Remplace run_server_watch.ps1 + thermal_watch + disk_retention (v1).
use std::time::Duration;

const TICK: Duration = Duration::from_secs(60);
const SRV: &str = "http://127.0.0.1:8320/api/health";
const OLLAMA: &str = "http://127.0.0.1:11434/api/version";
const DISK_MIN_GB: f64 = 30.0;
const GPU_MAX_C: f64 = 85.0;

fn log(msg: &str) {
    let line = format!("{} watch: {}\n", chrono::Local::now().format("%Y-%m-%d %H:%M:%S"), msg);
    print!("{line}");
    let path = std::env::var("LOCALAI_ROOT").map(|r| format!("{r}/logs/watch_rs.log"))
        .unwrap_or_else(|_| r"E:\ia\logs\watch_rs.log".into());
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = f.write_all(line.as_bytes());
    }
}

async fn http_ok(url: &str) -> bool {
    match tokio::time::timeout(Duration::from_secs(10), reqwest::get(url)).await {
        Ok(Ok(r)) => r.status().is_success(),
        _ => false,
    }
}

fn run_ps1(path: &str) {
    log(&format!("relance via {path}"));
    let _ = std::process::Command::new("powershell")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
}

fn start_ollama_detached() {
    log("relance ollama serve (detache)");
    let _ = std::process::Command::new(r"C:\Users\Tony\AppData\Local\Programs\Ollama\ollama.exe")
        .arg("serve")
        .env("OLLAMA_MODELS", r"E:\LocalAI-Modeles")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
}

async fn disk_gb() -> Option<f64> {
    let out = tokio::process::Command::new("powershell")
        .args(["-NoProfile", "-Command", "[math]::Round((Get-PSDrive E).Free/1GB,1)"])
        .output()
        .await
        .ok()?;
    String::from_utf8_lossy(&out.stdout).trim().parse().ok()
}

async fn disk_health() -> Option<String> {
    // SMART via Get-PhysicalDisk : renvoie le 1er disque non sain.
    let out = tokio::process::Command::new("powershell")
        .args(["-NoProfile", "-Command",
               "Get-PhysicalDisk | Where-Object { ($_.HealthStatus -ne 'OK') -and ($_.HealthStatus -ne 'Healthy') } | ForEach-Object { $_.FriendlyName + ':' + $_.HealthStatus }"])
        .output()
        .await
        .ok()?;
    let t = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if t.is_empty() { None } else { Some(t) }
}

async fn gpu_temp() -> Option<f64> {
    let out = tokio::process::Command::new("nvidia-smi")
        .args(["--query-gpu=temperature.gpu", "--format=csv,noheader"])
        .output()
        .await
        .ok()?;
    String::from_utf8_lossy(&out.stdout).trim().parse().ok()
}

/// Chantier 2 : alerte si >= 5 refus d'outils (tool_traces status='denied') en 10 min.
async fn check_tool_denials(root: &str) {
    use localai_core::db::Db;

    let db_path = std::path::Path::new(root).join("data").join("localai.db");
    let db = match Db::open(&db_path) {
        Ok(d) => d,
        Err(e) => {
            log(&format!("watch: db KO {e}"));
            return;
        }
    };
    let now = localai_core::db::now_iso();
    // fenetre glissante ISO : now - 600 s (lexicographique == chronologique).
    let sooner = chrono::Local::now().checked_add_signed(chrono::Duration::seconds(-600))
        .map(|t| t.format("%Y-%m-%dT%H:%M:%S").to_string()).unwrap_or_default();
    let rows = db.query_all_json(
        "SELECT tool_name, reason FROM tool_traces WHERE status='denied' AND created_at >= ?1",
        &[&sooner.as_str()],
    ).unwrap_or_default();
    let n = rows.len();
    if n >= 5 {
        let details: Vec<String> = rows.iter().take(5).map(|r| {
            format!("{}: {}",
                r.get("tool_name").and_then(|v| v.as_str()).unwrap_or("?"),
                r.get("reason").and_then(|v| v.as_str()).unwrap_or(""))
        }).collect();
        log(&format!("ALERTE : {n} refus d'outils en 10 min ({})", details.join(" | ")));
        // Telegram : si bot configure, envoi direct.
        let token = db.get_setting("telegram_token", "");
        if !token.is_empty() {
            let chat_id = db.get_setting("telegram_alert_chat_id", "");
            if !chat_id.is_empty() {
                let url = format!("https://api.telegram.org/bot{token}/sendMessage");
                let text: String =
                    format!("LocalAI : {n} refus d'outils en 10 min\n{}",
                            details.join("\n"))
                        .chars()
                        .take(500)
                        .collect();
                let body = serde_json::json!({ "chat_id": chat_id, "text": text });
                let _ = reqwest::Client::new().post(&url).json(&body).send().await;
            }
        }
    }
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt().init();
    let root = std::env::var("LOCALAI_ROOT").unwrap_or_else(|_| r"E:\ia".into());
    log(&format!("demarrage (root={root})"));
    let mut alerted_disk = false;
    let mut alerted_gpu = false;
    loop {
        if !http_ok(SRV).await {
            log("ALERTE :8320 down -> relance");
            run_ps1(&format!("{root}/run_server_boot.ps1"));
        }
        if !http_ok(OLLAMA).await {
            log("ALERTE ollama down -> relance");
            start_ollama_detached();
        }
        if let Some(free) = disk_gb().await {
            if free < DISK_MIN_GB && !alerted_disk {
                log(&format!("ALERTE disque E: {free} Go libres (< {DISK_MIN_GB})"));
                alerted_disk = true;
            } else if free >= DISK_MIN_GB {
                alerted_disk = false;
            }
        }
        if let Some(bad) = disk_health().await {
            log(&format!("ALERTE SMART disque : {bad}"));
        }
        if let Some(t) = gpu_temp().await {
            if t > GPU_MAX_C && !alerted_gpu {
                log(&format!("ALERTE GPU {t}C (> {GPU_MAX_C})"));
                alerted_gpu = true;
            } else if t <= GPU_MAX_C {
                alerted_gpu = false;
            }
        }
        check_tool_denials(&root).await;
        tokio::time::sleep(TICK).await;
    }
}
