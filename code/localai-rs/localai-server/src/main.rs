mod error;
mod feedback;
mod pybridge;
mod routes;
mod telegram;
mod tools;
mod worker;

use std::net::SocketAddr;
use std::sync::Arc;

use axum::extract::DefaultBodyLimit;
use axum::Router;
use localai_core::{Settings, APP_NAME, APP_VERSION};
use tower_http::cors::CorsLayer;

use crate::routes::{
    benchmark, chat, comms, connecteurs, conversations, documents, extras, finetune, fournisseurs,
    health, memory, models, onboarding, outil, sd, security, settings, studio, style_collab,
    system, tts, voice,
};

#[derive(Clone)]
pub struct AppState {
    pub settings: Settings,
    pub db: localai_core::db::Db,
    pub ollama: localai_core::ollama::Ollama,
    pub jobs: crate::routes::outil::JobStore,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("localai_server=info".parse().unwrap()),
        )
        .init();

    let settings = Settings::load();
    settings.ensure_dirs();
    let db = localai_core::db::Db::open(&settings.db_path())
        .expect("ouverture SQLite");
    let ollama = localai_core::ollama::Ollama::new(settings.ollama_host.clone())
        .expect("client ollama");
    let state = Arc::new(AppState {
        settings: settings.clone(),
        db,
        ollama,
        jobs: crate::routes::outil::JobStore::new(),
    });
    // Worker Python chaud : demarre avec le serveur.
    worker::init(state.settings.root_dir.clone());
    // Nettoyage VRAM : un modele residant 100 % en RAM (charge quand la VRAM
    // etait occupee) est lent ; on le decharge pour qu'il recharge sur GPU.
    {
        let o = state.ollama.clone();
        tokio::spawn(async move {
            tokio::time::sleep(std::time::Duration::from_secs(3)).await;
            if let Ok(v) = o.ps().await {
                if let Some(models) = v.get("models").and_then(|m| m.as_array()) {
                    for m in models {
                        let vram = m.get("size_vram").and_then(|x| x.as_i64()).unwrap_or(0);
                        let nom = m.get("name").and_then(|n| n.as_str()).unwrap_or("");
                        if vram == 0 && !nom.is_empty() {
                            let _ = o.unload(nom).await;
                            tracing::info!("[ollama] modele CPU decharge: {nom}");
                        }
                    }
                }
            }
        });
    }

    let cors = CorsLayer::new()
        .allow_origin([
            "http://localhost:5173".parse().unwrap(),
            "http://127.0.0.1:5173".parse().unwrap(),
        ])
        .allow_methods(tower_http::cors::Any)
        .allow_headers(tower_http::cors::Any);

    // Auth optionnelle : si un token API est defini, les appels /api/*
    // provenant de l'exterieur (non-loopback) doivent le presenter en
    // "Authorization: Bearer <token>". Les requetes locales (loopback,
    // UI auto-hebergee) passent toujours. Route publique : /api/health.
    let token_api = state.db.get_setting("api_token", "").to_string();
    let mut app: Router = Router::new()
        .merge(health::router())
        .merge(chat::router())
        .merge(feedback::router())
        .merge(onboarding::router())
        .merge(connecteurs::router())
        .merge(outil::router())
        .merge(conversations::router())
        .merge(models::router())
        .merge(system::router())
        .merge(settings::router())
        .merge(memory::router())
        .merge(tts::router())
        .merge(documents::router())
        .merge(benchmark::router())
        .merge(voice::router())
        .merge(sd::router())
        .merge(studio::router())
        .merge(style_collab::router())
        .merge(extras::router())
        .merge(security::router())
        .merge(comms::router())
        .merge(fournisseurs::router())
        .merge(finetune::router())
        .layer(cors)
        // Images jointes au chat (base64 4K ~10 Mo) : plafond 32 Mo.
        .layer(DefaultBodyLimit::max(32 * 1024 * 1024))
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            auth_middleware,
        ))
        .with_state(state.clone());

    // Frontend statique : dist/ servi, 404 -> index.html (SPA).
    // index.html en no-store : apres chaque deploy, le navigateur doit
    // recharger le HTML (sinon l'ancienne UI reste en cache et les
    // assets haches ont change -> UI fige sur l'ancienne version).
    let dist = state.settings.root_dir.join("frontend").join("dist");
    let index = dist.join("index.html");
    if index.is_file() {
        tracing::info!("frontend: {}", dist.display());
        let svc = tower_http::services::ServeDir::new(&dist)
            .not_found_service(tower_http::services::ServeFile::new(&index));
        app = app.fallback_service(svc);
        let idx = index.clone();
        app = app
            .route("/", axum::routing::get(move || {
                let idx = idx.clone();
                async move { no_store_file(idx).await }
            }))
            .route("/index.html", axum::routing::get(move || {
                let idx2 = index.clone();
                async move { no_store_file(idx2).await }
            }));
    } else {
        tracing::warn!("pas de frontend dist/");
    }

    let addr: SocketAddr = format!("{}:{}", settings.host, settings.port)
        .parse()
        .expect("adresse");
    tracing::info!("{APP_NAME} {APP_VERSION} (rust) sur {addr}");
    telegram::maybe_start(state.clone());
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown_signal())
    .await
    .expect("serve");
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    tracing::info!("arret demande, vidange...");
}

/// Middleware d'authentification API : les requetes EXTERNES (non-loopback)
/// vers /api/* doivent presenter le token si celui-ci est defini.
/// Les requetes locales (UI auto-hebergee) et /api/health passent toujours.
async fn auth_middleware(
    axum::extract::State(state): axum::extract::State<Arc<AppState>>,
    axum::extract::ConnectInfo(addr): axum::extract::ConnectInfo<std::net::SocketAddr>,
    req: axum::http::Request<axum::body::Body>,
    next: axum::middleware::Next,
) -> axum::response::Response {
    let chemin = req.uri().path();
    let local = addr.ip().is_loopback();
    if local || chemin == "/api/health" || !chemin.starts_with("/api/") {
        return next.run(req).await;
    }
    let token = state.db.get_setting("api_token", "");
    if token.is_empty() {
        // Pas de token defini : acces LAN autorise (config actuelle),
        // mais consigne dans le journal pour sensibilite.
        return next.run(req).await;
    }
    let ok = req
        .headers()
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|v| v.trim_start_matches("Bearer ").trim() == token)
        .unwrap_or(false);
    if ok {
        return next.run(req).await;
    }
    axum::http::Response::builder()
        .status(axum::http::StatusCode::UNAUTHORIZED)
        .body(axum::body::Body::from(
            "{\"error\":\"token API requis (Authorization: Bearer)\"}",
        ))
        .unwrap()
}

/// index.html avec Cache-Control: no-store (anti UI fige apres deploy).
async fn no_store_file(
    path: std::path::PathBuf,
) -> axum::response::Response {
    use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
    use axum::response::IntoResponse;
    match tokio::fs::read(&path).await {
        Ok(bytes) => {
            let mut headers = HeaderMap::new();
            headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
            headers.insert(
                header::CONTENT_TYPE,
                HeaderValue::from_static("text/html; charset=utf-8"),
            );
            (headers, bytes).into_response()
        }
        Err(_) => (StatusCode::NOT_FOUND, "frontend absent").into_response(),
    }
}
