//! Worker Python resident : un seul process chaud pour les outils du pont.
//!
//! Un seul process Python chaud (backend/worker.py) execute les operations
//! frequentes du pont : outils, specs, agents, routage. Supprime le cout de
//! spawn (1-4 s) du pont classique. Le worker est relance automatiquement
//! s'il tombe ; en cas d'erreur, les appelants retombent sur pybridge.
use std::collections::HashMap;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, OnceLock};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;
use tokio::sync::{oneshot, Mutex};

type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<serde_json::Value, String>>>>>;

pub struct Worker {
    root: std::path::PathBuf,
    stdin: Mutex<Option<tokio::process::ChildStdin>>,
    child: Mutex<Option<tokio::process::Child>>,
    pending: Pending,
    next_id: AtomicU64,
    alive: AtomicBool,
}

impl Worker {
    fn new(root: std::path::PathBuf) -> Arc<Self> {
        Arc::new(Self {
            root,
            stdin: Mutex::new(None),
            child: Mutex::new(None),
            pending: Arc::new(Mutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            alive: AtomicBool::new(false),
        })
    }

    async fn ensure(self: &Arc<Self>) -> Result<(), String> {
        // Vivant ? (process toujours la et stdin present)
        let vivant = {
            let mut garde = self.child.lock().await;
            match garde.as_mut() {
                Some(c) => c.try_wait().ok().flatten().is_none(),
                None => false,
            }
        };
        if vivant && self.stdin.lock().await.is_some() {
            return Ok(());
        }
        *self.child.lock().await = None;
        *self.stdin.lock().await = None;
        let py = self.root.join(".venv").join("Scripts").join("python.exe");
        if !py.is_file() {
            return Err("venv python absent (worker)".into());
        }
        let mut cmd = Command::new(&py);
        cmd.args(["-u", "backend/worker.py"])
            .current_dir(&self.root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true);
        let mut child = cmd.spawn().map_err(|e| format!("worker spawn: {e}"))?;
        let stdin = child.stdin.take().ok_or("worker stdin absent")?;
        let stdout = child.stdout.take().ok_or("worker stdout absent")?;
        *self.stdin.lock().await = Some(stdin);
        // Le Child est conserve : sinon kill_on_drop tuerait le worker.
        *self.child.lock().await = Some(child);
        self.alive.store(true, Ordering::Relaxed);
        let pending = self.pending.clone();
        tokio::spawn(async move {
            let mut lignes = BufReader::new(stdout).lines();
            while let Ok(Some(ligne)) = lignes.next_line().await {
                let Ok(v) = serde_json::from_str::<serde_json::Value>(&ligne) else {
                    continue;
                };
                let Some(id) = v.get("id").and_then(|x| x.as_u64()) else {
                    continue;
                };
                let tx = pending.lock().await.remove(&id);
                if let Some(tx) = tx {
                    let res = if v.get("ok").and_then(|x| x.as_bool()).unwrap_or(false) {
                        Ok(v.get("out").cloned().unwrap_or(serde_json::Value::Null))
                    } else {
                        Err(v
                            .get("err")
                            .and_then(|x| x.as_str())
                            .unwrap_or("erreur worker")
                            .to_string())
                    };
                    let _ = tx.send(res);
                }
            }
            // Process termine : les requetes en attente echouent tout de suite.
            let mut map = pending.lock().await;
            for (_, tx) in map.drain() {
                let _ = tx.send(Err("worker termine".into()));
            }
        });
        Ok(())
    }

    pub async fn call(
        self: &Arc<Self>,
        mut req: serde_json::Value,
        timeout_s: u64,
    ) -> Result<serde_json::Value, String> {
        self.ensure().await?;
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        if let Some(obj) = req.as_object_mut() {
            obj.insert("id".into(), serde_json::json!(id));
        }
        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id, tx);
        {
            let mut guard = self.stdin.lock().await;
            let Some(stdin) = guard.as_mut() else {
                return Err("worker stdin ferme".into());
            };
            let ligne = serde_json::to_string(&req).map_err(|e| e.to_string())?;
            if let Err(e) = stdin.write_all(format!("{ligne}\n").as_bytes()).await {
                self.alive.store(false, Ordering::Relaxed);
                *guard = None;
                return Err(format!("worker write: {e}"));
            }
            let _ = stdin.flush().await;
        }
        match tokio::time::timeout(std::time::Duration::from_secs(timeout_s), rx).await {
            Ok(Ok(Ok(v))) => Ok(v),
            Ok(Ok(Err(e))) => Err(e),
            Ok(Err(_)) => Err("worker: reponse perdue".into()),
            Err(_) => Err(format!("worker: delai depasse ({timeout_s} s)")),
        }
    }
}

static GLOBAL: OnceLock<Arc<Worker>> = OnceLock::new();

pub fn init(root: std::path::PathBuf) {
    let _ = GLOBAL.set(Worker::new(root));
}

pub fn get() -> Option<&'static Arc<Worker>> {
    GLOBAL.get()
}
