#[derive(Debug)]
pub enum AppError {
    Db(String),
    Ollama(String),
    BadRequest(String),
    NotFound(String),
    Internal(String),
}

impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Db(e) => write!(f, "db: {e}"),
            Self::Ollama(e) => write!(f, "ollama: {e}"),
            Self::BadRequest(e) => write!(f, "{e}"),
            Self::NotFound(e) => write!(f, "{e}"),
            Self::Internal(e) => write!(f, "{e}"),
        }
    }
}

impl AppError {
    /// Code HTTP associe (le serveur convertit en reponse).
    pub fn status_code(&self) -> u16 {
        match self {
            Self::BadRequest(_) => 400,
            Self::NotFound(_) => 404,
            _ => 500,
        }
    }

    pub fn message(&self) -> String {
        match self {
            Self::BadRequest(e) | Self::NotFound(e) => e.clone(),
            _ => self.to_string(),
        }
    }
}

impl From<rusqlite::Error> for AppError {
    fn from(e: rusqlite::Error) -> Self {
        Self::Db(e.to_string())
    }
}

impl From<reqwest::Error> for AppError {
    fn from(e: reqwest::Error) -> Self {
        Self::Ollama(e.to_string())
    }
}

pub type AppResult<T> = Result<T, AppError>;
