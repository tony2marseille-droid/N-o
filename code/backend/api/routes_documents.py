import uuid
from pathlib import Path
from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.documents import ExtractionError, chunk_text, extract_text
from backend.core.vector_store import Embedder, delete_document, list_documents, search, store_document
from backend.inference import get_active_backend
from backend.inference.ollama import OllamaError
from pydantic import BaseModel

router = APIRouter(prefix="/api/documents", tags=["documents"])

UPLOAD_DIR = settings.data_dir / "documents"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_SIZE = 30 * 1024 * 1024


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


@router.get("")
async def get_documents() -> dict:
    return {"documents": list_documents()}


@router.post("/upload")
async def upload_document(file: UploadFile) -> dict:
    raw = await file.read()
    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 30 Mo)")
    filename = file.filename or "document.txt"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".pdf", ".docx", ".xlsx", ".xlsm", ".txt", ".md", ".csv", ".json", ".py", ".log"):
        raise HTTPException(status_code=415, detail=f"Format non supporte: {suffix}")

    temp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    temp_path.write_bytes(raw)
    try:
        text = extract_text(temp_path)
    except ExtractionError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    chunks = chunk_text(text)
    if not chunks:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Aucun texte extractible")

    backend = get_active_backend()
    embedder = Embedder(backend)
    vectors: list[list[float]] = []
    try:
        batch_size = 16
        for i in range(0, len(chunks), batch_size):
            vectors.extend(await embedder.embed_batch(chunks[i : i + batch_size]))
    except OllamaError as exc:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail=f"Embedding impossible: {exc}") from exc

    doc_id = store_document(filename, len(raw), chunks, vectors)
    final_path = UPLOAD_DIR / f"doc_{doc_id}{suffix}"
    temp_path.rename(final_path)
    return {"id": doc_id, "filename": filename, "chunks": len(chunks)}


@router.delete("/{doc_id}")
async def remove_document(doc_id: int) -> dict:
    delete_document(doc_id)
    for old in UPLOAD_DIR.glob(f"doc_{doc_id}.*"):
        old.unlink(missing_ok=True)
    return {"deleted": doc_id}


@router.post("/reindex")
async def reindex_documents() -> dict:
    """Re-encode tous les documents avec le modele d'embed courant du palier."""
    from backend.core.database import get_db
    from backend.core.documents import extract_text
    from backend.core.rag import embed_model

    backend = get_active_backend()
    embedder = Embedder(backend)
    db = get_db()
    docs = db.query_all("SELECT id, filename FROM documents ORDER BY id")
    traites, echecs = 0, 0
    for doc in docs:
        source = next(UPLOAD_DIR.glob(f"doc_{doc['id']}.*"), None)
        if not source:
            echecs += 1
            continue
        try:
            texte = extract_text(source)
            chunks = chunk_text(texte)
            if not chunks:
                echecs += 1
                continue
            vecteurs: list[list[float]] = []
            for i in range(0, len(chunks), 16):
                vecteurs.extend(await embedder.embed_batch(chunks[i : i + 16]))
            db.execute("DELETE FROM document_chunks WHERE document_id=?", (doc["id"],))
            from datetime import datetime

            from backend.core.vector_store import _pack

            for pos, (content, vec) in enumerate(zip(chunks, vecteurs)):
                db.execute(
                    "INSERT INTO document_chunks (document_id, position, content, embedding)"
                    " VALUES (?,?,?,?)",
                    (doc["id"], pos, content, _pack(vec)),
                )
            db.execute("UPDATE documents SET chunk_count=? WHERE id=?", (len(chunks), doc["id"]))
            traites += 1
        except Exception:
            echecs += 1
    return {
        "reindexes": traites,
        "echecs": echecs,
        "modele_embed": embed_model(),
    }


@router.post("/search")
async def search_documents(req: SearchRequest) -> dict:
    backend = get_active_backend()
    embedder = Embedder(backend)
    try:
        vec = (await embedder.embed_batch([req.query]))[0]
    except OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"results": search(vec, top_k=max(1, min(req.top_k, 20)))}


class FicheOut(BaseModel):
    id: int
    titre: str
    categorie: str
    resume: str
    contenu_md: str
    image_path: str
    source: str
    created_at: str
    updated_at: str


@router.get("/fiches")
async def list_fiches() -> dict:
    db = get_db()
    rows = db.query_all("SELECT * FROM fiches ORDER BY updated_at DESC")
    return {"fiches": rows}


@router.get("/fiches/{fiche_id}")
async def get_fiche(fiche_id: int) -> dict:
    db = get_db()
    row = db.query_one("SELECT * FROM fiches WHERE id=?", (fiche_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Fiche non trouvee")
    return dict(row)


@router.delete("/fiches/{fiche_id}")
async def delete_fiche(fiche_id: int) -> dict:
    db = get_db()
    db.execute("DELETE FROM fiches WHERE id=?", (fiche_id,))
    return {"deleted": fiche_id}
