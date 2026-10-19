//! Adaptation AppError (core) -> reponse HTTP axum.
use axum::{
    http::StatusCode,
    response::{IntoResponse, Json},
};
use localai_core::AppError;
use serde_json::json;

pub struct ApiError(pub AppError);

impl From<AppError> for ApiError {
    fn from(e: AppError) -> Self {
        Self(e)
    }
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> axum::response::Response {
        let code = StatusCode::from_u16(self.0.status_code())
            .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
        (code, Json(json!({ "detail": self.0.message() }))).into_response()
    }
}

pub type ApiResult<T> = Result<T, ApiError>;
