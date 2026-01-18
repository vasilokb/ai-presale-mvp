from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.db import Base, engine, get_db
from app.models import Document, File as FileRecord, Presale, Result
from app.storage import ensure_bucket, get_s3_client
from app.settings import settings

app = FastAPI(title="AI Presale MVP")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)



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


class PresaleUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1)


class DocumentAlternativeRequest(BaseModel):
    prompt: str
    params: dict


class ResultVersionRequest(BaseModel):
    result_json: dict


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


@app.get("/api/v1/presales")
def list_presales(db: Annotated[Session, Depends(get_db)]):
    presales = db.scalars(select(Presale).order_by(desc(Presale.created_at))).all()
    return [
        {"id": presale.id, "name": presale.name, "created_at": presale.created_at}
        for presale in presales
    ]


@app.get("/api/v1/presales/{presale_id}")
def get_presale(presale_id: str, db: Annotated[Session, Depends(get_db)]):
    presale = db.get(Presale, presale_id)
    if not presale:
        return error_response(404, "presale_not_found")
    return {"id": presale.id, "name": presale.name, "created_at": presale.created_at}


@app.patch("/api/v1/presales/{presale_id}")
def update_presale(
    presale_id: str, payload: PresaleUpdateRequest, db: Annotated[Session, Depends(get_db)]
):
    presale = db.get(Presale, presale_id)
    if not presale:
        return error_response(404, "presale_not_found")
    presale.name = payload.name
    db.add(presale)
    db.commit()
    return {"id": presale.id, "name": presale.name, "created_at": presale.created_at}


@app.delete("/api/v1/presales/{presale_id}")
def delete_presale(presale_id: str, db: Annotated[Session, Depends(get_db)]):
    presale = db.get(Presale, presale_id)
    if not presale:
        return error_response(404, "presale_not_found")
    db.delete(presale)
    db.commit()
    return {"status": "deleted"}


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


@app.get("/api/v1/presales/{presale_id}/files")
def list_presale_files(presale_id: str, db: Annotated[Session, Depends(get_db)]):
    presale = db.get(Presale, presale_id)
    if not presale:
        return error_response(404, "presale_not_found")
    files = db.scalars(select(FileRecord).where(FileRecord.presale_id == presale_id)).all()
    return [
        {
            "file_id": record.id,
            "filename": record.filename,
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "storage_key": record.storage_key,
            "created_at": record.created_at,
        }
        for record in files
    ]


@app.delete("/api/v1/files/{file_id}")
def delete_file(file_id: str, db: Annotated[Session, Depends(get_db)]):
    record = db.get(FileRecord, file_id)
    if not record:
        return error_response(404, "file_not_found")
    client = get_s3_client()
    ensure_bucket(client, settings.minio_bucket)
    client.delete_object(Bucket=settings.minio_bucket, Key=record.storage_key)
    db.delete(record)
    db.commit()
    return {"status": "deleted"}


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


@app.get("/api/v1/documents/{document_id}")
def get_document(document_id: str, db: Annotated[Session, Depends(get_db)]):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    return {
        "document_id": document.id,
        "presale_id": document.presale_id,
        "prompt": document.prompt,
        "params": document.params_json,
        "status": document.status,
    }


@app.get("/api/v1/documents/{document_id}/result")
def get_result(
    document_id: str,
    db: Annotated[Session, Depends(get_db)],
    version: int | None = Query(None, ge=1),
):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    if document.status != "done":
        return error_response(409, "result_not_ready")
    query = select(Result).where(Result.document_id == document_id)
    if version is not None:
        query = query.where(Result.version == version)
    else:
        query = query.order_by(desc(Result.version))
    result = db.scalar(query)
    if not result:
        return error_response(404, "document_not_found")
    return {
        "document_id": document.id,
        "version": result.version,
        "llm_model": result.llm_model,
        **result.result_json,
    }


@app.get("/api/v1/documents/{document_id}/versions")
def list_document_versions(document_id: str, db: Annotated[Session, Depends(get_db)]):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    versions = db.scalars(
        select(Result.version).where(Result.document_id == document_id).order_by(desc(Result.version))
    ).all()
    return {"document_id": document_id, "versions": versions}


@app.get("/api/v1/documents/{document_id}/export/json")
def export_document_json(
    document_id: str,
    db: Annotated[Session, Depends(get_db)],
    version: int | None = Query(None, ge=1),
):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    if document.status != "done":
        return error_response(409, "result_not_ready")
    query = select(Result).where(Result.document_id == document_id)
    if version is not None:
        query = query.where(Result.version == version)
    else:
        query = query.order_by(desc(Result.version))
    result = db.scalar(query)
    if not result:
        return error_response(404, "document_not_found")
    filename = f"presale_{document_id}_v{result.version}.json"
    return Response(
        content=JSONResponse(
            content={
                "document_id": document.id,
                "version": result.version,
                "llm_model": result.llm_model,
                **result.result_json,
            }
        ).body,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/v1/documents")
def list_documents(db: Annotated[Session, Depends(get_db)]):
    documents = db.scalars(select(Document).order_by(desc(Document.created_at))).all()
    return [
        {
            "document_id": document.id,
            "presale_id": document.presale_id,
            "status": document.status,
            "progress": document.progress,
            "message": document.message,
            "created_at": document.created_at,
        }
        for document in documents
    ]


@app.get("/api/v1/presales/{presale_id}/documents")
def list_presale_documents(presale_id: str, db: Annotated[Session, Depends(get_db)]):
    presale = db.get(Presale, presale_id)
    if not presale:
        return error_response(404, "presale_not_found")
    documents = db.scalars(
        select(Document).where(Document.presale_id == presale_id).order_by(desc(Document.created_at))
    ).all()
    return [
        {
            "document_id": document.id,
            "status": document.status,
            "progress": document.progress,
            "message": document.message,
            "created_at": document.created_at,
        }
        for document in documents
    ]


@app.post("/api/v1/presales/{presale_id}/documents/alternative")
def create_alternative_document(
    presale_id: str, payload: DocumentAlternativeRequest, db: Annotated[Session, Depends(get_db)]
):
    presale = db.get(Presale, presale_id)
    if not presale:
        return error_response(404, "presale_not_found")
    file_count = db.scalar(
        select(func.count()).select_from(FileRecord).where(FileRecord.presale_id == presale_id)
    )
    if not file_count:
        return error_response(400, "no_files_uploaded")
    document_id = str(uuid4())
    document = Document(
        id=document_id,
        presale_id=presale_id,
        prompt=payload.prompt,
        params_json=payload.params,
        status="queued",
        progress=0,
        message="",
    )
    db.add(document)
    db.commit()
    return {"document_id": document_id, "status": "queued"}


@app.post("/api/v1/documents/{document_id}/result/version")
def create_result_version(
    document_id: str, payload: ResultVersionRequest, db: Annotated[Session, Depends(get_db)]
):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    latest = db.scalar(
        select(Result).where(Result.document_id == document_id).order_by(desc(Result.version))
    )
    if not latest:
        return error_response(404, "document_not_found")
    next_version = latest.version + 1
    result = Result(
        id=str(uuid4()),
        document_id=document_id,
        version=next_version,
        llm_model=latest.llm_model,
        result_json=payload.result_json,
    )
    db.add(result)
    db.commit()
    return {"document_id": document_id, "version": next_version}
