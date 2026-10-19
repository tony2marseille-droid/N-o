//! CLI pipeline : orchestration train/merge/convert/deploy/bench/promote.
//! Les commandes lourdes GPU deleguent au venv Python ; bench/promote sont natifs.
use clap::{Parser, Subcommand};
use localai_core::ollama::Msg;

#[derive(Parser)]
#[command(name = "localai-pipeline", version)]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Etat : serveur, ollama, modeles, disques.
    Status,
    /// Bench candidat vs incumbent (juge Ollama, batterie integree).
    Bench {
        /// Modele candidat (ex. localai-prog-v9.2).
        candidat: String,
        /// Modele en place (ex. localai-prog-v9).
        #[arg(default_value = "localai-prog-v9")]
        incumbent: String,
        /// Juge (defaut qwen3:14b).
        #[arg(long, default_value = "qwen3:14b")]
        judge: String,
    },
    /// Promotion : alias localai-prog-latest + registre.
    Promote {
        candidat: String,
        incumbent: String,
    },
}

const JUDGE_SYS: &str = "Tu es un evaluateur impartial qui compare deux reponses d'assistant IA (reponse A et reponse B) a la meme question en francais. Reponds UNIQUEMENT par A, B ou TIE selon laquelle est la plus correcte, complete, structuree, puis claire. Un seule lettre, rien d'autre.";

const BATTERY: &[(&str, &str)] = &[
    ("ops_1", "Explique comment verifier l'espace disque sous Windows en ligne de commande."),
    ("ops_2", "Un service Windows ne demarre pas. Donne 3 commandes de diagnostic."),
    ("code_1", "Ecris une fonction Python qui lit un fichier ligne par ligne."),
    ("code_2", "Ecris une requete SQL qui compte les lignes par statut."),
    ("redac_1", "En deux phrases, explique ce qu'est une junction NTFS."),
    ("maths_1", "Calcule 17*24-89. Reponds uniquement par le nombre."),
];

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt().init();
    let cli = Cli::parse();
    match cli.cmd {
        Cmd::Status => cmd_status().await,
        Cmd::Bench { candidat, incumbent, judge } => cmd_bench(&candidat, &incumbent, &judge).await,
        Cmd::Promote { candidat, incumbent } => cmd_promote(&candidat, &incumbent).await,
    }
}

async fn cmd_status() {
    let s = localai_core::Settings::load();
    println!("root: {}", s.root_dir.display());
    println!("db:   {}", s.db_path().display());
    match localai_core::ollama::Ollama::new(s.ollama_host.clone()) {
        Ok(oc) => match oc.version().await {
            Ok(v) => println!("ollama: {v}"),
            Err(e) => println!("ollama DOWN: {e}"),
        },
        Err(e) => println!("ollama client: {e}"),
    }
}

async fn cmd_bench(candidat: &str, incumbent: &str, judge: &str) {
    let s = localai_core::Settings::load();
    let oc = match localai_core::ollama::Ollama::new(s.ollama_host.clone()) {
        Ok(o) => o,
        Err(e) => {
            eprintln!("ollama: {e}");
            return;
        }
    };
    let (mut wins, mut losses, mut ties) = (0, 0, 0);
    for (id, prompt) in BATTERY {
        let a = oc
            .chat(candidat, vec![Msg { role: "user".into(), content: prompt.to_string() }], 0.7, 800)
            .await
            .unwrap_or_default();
        let b = oc
            .chat(incumbent, vec![Msg { role: "user".into(), content: prompt.to_string() }], 0.7, 800)
            .await
            .unwrap_or_default();
        let verdict = oc
            .chat(
                judge,
                vec![
                    Msg { role: "system".into(), content: JUDGE_SYS.into() },
                    Msg { role: "user".into(), content: format!("Question : {prompt}\n\nReponse A : {a}\n\nReponse B : {b}") },
                ],
                0.1,
                10,
            )
            .await
            .unwrap_or_default();
        let v = verdict.trim().to_uppercase();
        let tag = if v.starts_with('A') {
            wins += 1;
            "A(candidat)"
        } else if v.starts_with('B') {
            losses += 1;
            "B(incumbent)"
        } else {
            ties += 1;
            "TIE"
        };
        println!("{id}: {tag}");
    }
    let verdict = if wins > losses { "promote" } else if wins == losses { "tie" } else { "regress" };
    println!("candidat meilleur: {wins} | incumbent: {losses} | tie: {ties}");
    println!("VERDICT: {verdict}");
}

async fn cmd_promote(candidat: &str, incumbent: &str) {
    // Equivalent de promote.py : ollama cp + registre JSON.
    let s = localai_core::Settings::load();
    let ollama_bin = r"C:\Users\Tony\AppData\Local\Programs\Ollama\ollama.exe";
    let out = tokio::process::Command::new(ollama_bin)
        .args(["cp", candidat, "localai-prog-latest"])
        .output()
        .await;
    match out {
        Ok(o) if o.status.success() => println!("alias localai-prog-latest -> {candidat}"),
        Ok(o) => {
            eprintln!("ollama cp KO: {}", String::from_utf8_lossy(&o.stderr));
            return;
        }
        Err(e) => {
            eprintln!("ollama cp: {e}");
            return;
        }
    }
    let reg_path = s.data_dir.join("model_registry.json");
    let mut reg: serde_json::Value = std::fs::read_to_string(&reg_path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_else(|| serde_json::json!({"current": "", "history": []}));
    reg["current"] = serde_json::Value::from("localai-prog-latest");
    let entry = serde_json::json!({
        "name": candidat, "vs": incumbent, "verdict": "promote (cli rust)",
        "alias": "localai-prog-latest",
        "date": chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
    });
    if let Some(h) = reg.get_mut("history").and_then(|h| h.as_array_mut()) {
        h.push(entry);
    }
    if std::fs::write(&reg_path, serde_json::to_string_pretty(&reg).unwrap()).is_ok() {
        println!("registre maj : {}", reg_path.display());
    }
    println!("PROMOTION OK: {candidat} -> localai-prog-latest");
}
