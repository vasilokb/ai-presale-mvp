from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse

from app.db import Base, engine, get_db
from app.models import Document, File as FileRecord, Presale, Result
from app.storage import ensure_bucket, get_s3_client
from app.settings import settings

app = FastAPI(title="AI Presale MVP")


def error_response(status_code: int, error: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error})


class PresaleCreate(BaseModel):
    name: str = Field(..., min_length=1)


class PresaleResponse(BaseModel):
    id: str
    name: str
    created_at: datetime


class DocumentStartRequest(BaseModel):
    presale_id: str
    prompt: str
    params: dict


class DocumentStartResponse(BaseModel):
    document_id: str
    status: str


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/v1/presales", response_model=PresaleResponse)
def create_presale(payload: PresaleCreate, db: Annotated[Session, Depends(get_db)]):
    presale = Presale(id=str(uuid4()), name=payload.name)
    db.add(presale)
    db.commit()
    db.refresh(presale)
    return PresaleResponse(id=presale.id, name=presale.name, created_at=presale.created_at)


@app.post("/api/v1/files/upload")
async def upload_file(
    presale_id: Annotated[str, Query(...)],
    file: Annotated[UploadFile, File(...)],
    db: Annotated[Session, Depends(get_db)],
):
    presale = db.get(Presale, presale_id)
    if not presale:
        return error_response(400, "presale_not_found")

    filename = file.filename or ""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    allowed_ext = {"pdf", "docx", "txt"}
    allowed_types = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    }
    if ext not in allowed_ext and (file.content_type or "") not in allowed_types:
        return error_response(400, "unsupported_file_type")

    content = await file.read()
    file_id = str(uuid4())
    storage_key = f"uploads/{presale_id}/{file_id}_{filename}"
    client = get_s3_client()
    ensure_bucket(client, settings.minio_bucket)
    client.put_object(Bucket=settings.minio_bucket, Key=storage_key, Body=content)

    record = FileRecord(
        id=file_id,
        presale_id=presale_id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        storage_key=storage_key,
    )
    db.add(record)
    db.commit()

    return {
        "file_id": record.id,
        "presale_id": record.presale_id,
        "filename": record.filename,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "storage_key": record.storage_key,
    }


@app.post("/api/v1/documents/start", response_model=DocumentStartResponse)
def start_document(payload: DocumentStartRequest, db: Annotated[Session, Depends(get_db)]):
    presale = db.get(Presale, payload.presale_id)
    if not presale:
        return error_response(400, "presale_not_found")

    file_count = db.scalar(
        select(func.count()).select_from(FileRecord).where(FileRecord.presale_id == payload.presale_id)
    )
    if not file_count:
        return error_response(400, "no_files_uploaded")

    document_id = str(uuid4())
    document = Document(
        id=document_id,
        presale_id=payload.presale_id,
        prompt=payload.prompt,
        params_json=payload.params,
        status="queued",
        progress=0,
        message="",
    )
    db.add(document)
    db.commit()
    return DocumentStartResponse(document_id=document_id, status="queued")


@app.get("/api/v1/documents/{document_id}/status")
def get_status(document_id: str, db: Annotated[Session, Depends(get_db)]):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    return {
        "document_id": document.id,
        "status": document.status,
        "progress": document.progress,
        "message": document.message,
    }


@app.get("/api/v1/documents/{document_id}/result")
def get_result(document_id: str, db: Annotated[Session, Depends(get_db)]):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    if document.status != "done":
        return error_response(409, "result_not_ready")
    result = db.scalar(select(Result).where(Result.document_id == document_id))
    if not result:
        return error_response(404, "document_not_found")
    return {
        "document_id": document.id,
        "version": result.version,
        "llm_model": result.llm_model,
        **result.result_json,
    }
