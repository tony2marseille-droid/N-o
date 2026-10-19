pub mod config;
pub mod db;
pub mod error;
pub mod ollama;

pub use config::{APP_NAME, APP_VERSION, Settings};
pub use error::AppError;
