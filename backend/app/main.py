from datetime import datetime
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.db import Base, engine, ensure_result_columns, get_db
from app.models import Document, File as FileRecord, LlmDebug, Presale, Result, StoryRow
from app.storage import ensure_bucket, get_s3_client
from app.ollama_client import call_ollama, parse_llm_json
from app.ollama_client import check_ollama_health
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


def round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(value / step) * step


def build_story_rows_from_result(result_json: dict, document_id: str, version: int) -> list[StoryRow]:
    rows = []
    for row in result_json.get("rows", []):
        pert = row.get("pert_hours", {})
        rows.append(
            StoryRow(
                id=row.get("row_id") or str(uuid4()),
                document_id=document_id,
                version=version,
                epic=row.get("epic", ""),
                title=row.get("story_title", ""),
                type=row.get("story_type", "functional"),
                role=row.get("role", ""),
                see=row.get("see", []),
                do=row.get("do", []),
                get=row.get("get", []),
                acceptance=row.get("acceptance", []),
                optimistic=float(pert.get("optimistic", 0)),
                most_likely=float(pert.get("most_likely", 0)),
                pessimistic=float(pert.get("pessimistic", 0)),
                expected=float(pert.get("expected", 0)),
            )
        )
    return rows


def ensure_story_rows(db: Session, document_id: str, version: int, result_json: dict) -> list[StoryRow]:
    rows = db.scalars(
        select(StoryRow).where(StoryRow.document_id == document_id, StoryRow.version == version)
    ).all()
    if rows:
        return rows
    rows = build_story_rows_from_result(result_json, document_id, version)
    db.add_all(rows)
    db.commit()
    return rows


def build_result_json_from_rows(rows: list[StoryRow], llm_model: str, document_id: str, version: int) -> dict:
    total_expected = sum(row.expected for row in rows)
    total_expected = round_to_step(total_expected, 0.5)
    return {
        "document_id": document_id,
        "version": version,
        "llm_model": llm_model,
        "rows": [
            {
                "row_id": row.id,
                "epic": row.epic,
                "story_title": row.title,
                "story_type": row.type,
                "role": row.role,
                "see": row.see,
                "do": row.do,
                "get": row.get,
                "acceptance": row.acceptance,
                "pert_hours": {
                    "optimistic": row.optimistic,
                    "most_likely": row.most_likely,
                    "pessimistic": row.pessimistic,
                    "expected": row.expected,
                },
            }
            for row in rows
        ],
        "totals": {"expected_hours": round(total_expected, 2)},
    }


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


class PertHours(BaseModel):
    optimistic: float
    most_likely: float
    pessimistic: float
    expected: float


class StoryRowPayload(BaseModel):
    id: str | None = None
    epic: str
    title: str
    type: Literal["functional", "non_functional"]
    role: Literal["SA/BA", "Backend", "Frontend", "Data-engineer", "DevOps"]
    see: list[str]
    do: list[str]
    get: list[str]
    acceptance: list[str]
    pert_hours: PertHours


class StoryRowsPatch(BaseModel):
    rows: list[StoryRowPayload]


class ReestimateRequest(BaseModel):
    row_ids: list[str]


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_result_columns()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/llm/health")
def llm_health():
    return check_ollama_health()


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
        "raw_llm_output": result.raw_llm_output,
        "validation_error": result.validation_error,
        **result.result_json,
    }


@app.get("/api/v1/documents/{document_id}/result-view")
def get_result_view(
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
    story_rows = ensure_story_rows(db, document_id, result.version, result.result_json)
    rows = [
        {
            "id": row.id,
            "epic": row.epic,
            "title": row.title,
            "type": row.type,
            "role": row.role,
            "see": row.see,
            "do": row.do,
            "get": row.get,
            "acceptance": row.acceptance,
            "pert_hours": {
                "optimistic": row.optimistic,
                "most_likely": row.most_likely,
                "pessimistic": row.pessimistic,
                "expected": row.expected,
            },
        }
        for row in story_rows
    ]
    total_expected = sum(float(row["pert_hours"]["expected"]) for row in rows)
    total_expected = round_to_step(total_expected, 0.5)
    return {
        "document_id": document.id,
        "version": result.version,
        "llm_model": result.llm_model,
        "rows": rows,
        "totals": {"expected_hours": round(total_expected, 2)},
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
                "raw_llm_output": result.raw_llm_output,
                "validation_error": result.validation_error,
                **result.result_json,
            }
        ).body,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/v1/documents/{document_id}/debug/llm")
def get_llm_debug(document_id: str, db: Annotated[Session, Depends(get_db)]):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    entries = db.scalars(
        select(LlmDebug)
        .where(LlmDebug.document_id == document_id)
        .order_by(desc(LlmDebug.created_at))
        .limit(5)
    ).all()
    return {
        "document_id": document_id,
        "entries": [
            {
                "attempt": entry.attempt,
                "prompt": entry.prompt,
                "raw_output": entry.raw_output,
                "error_code": entry.error_code,
                "error_detail": entry.error_detail,
                "created_at": entry.created_at,
            }
            for entry in entries
        ],
    }


@app.patch("/api/v1/documents/{document_id}/rows")
def update_story_rows(
    document_id: str, payload: StoryRowsPatch, db: Annotated[Session, Depends(get_db)]
):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    latest_result = db.scalar(
        select(Result).where(Result.document_id == document_id).order_by(desc(Result.version))
    )
    if not latest_result:
        return error_response(404, "document_not_found")
    version = latest_result.version
    db.query(StoryRow).filter(StoryRow.document_id == document_id, StoryRow.version == version).delete()
    rows = []
    for row in payload.rows:
        row_id = row.id or str(uuid4())
        rows.append(
            StoryRow(
                id=row_id,
                document_id=document_id,
                version=version,
                epic=row.epic,
                title=row.title,
                type=row.type,
                role=row.role,
                see=row.see,
                do=row.do,
                get=row.get,
                acceptance=row.acceptance,
                optimistic=row.pert_hours.optimistic,
                most_likely=row.pert_hours.most_likely,
                pessimistic=row.pert_hours.pessimistic,
                expected=row.pert_hours.expected,
            )
        )
    db.add_all(rows)
    db.commit()
    return {"status": "ok"}


@app.post("/api/v1/documents/{document_id}/reestimate")
def reestimate_rows(
    document_id: str, payload: ReestimateRequest, db: Annotated[Session, Depends(get_db)]
):
    document = db.get(Document, document_id)
    if not document:
        return error_response(404, "document_not_found")
    latest_result = db.scalar(
        select(Result).where(Result.document_id == document_id).order_by(desc(Result.version))
    )
    if not latest_result:
        return error_response(404, "document_not_found")
    version = latest_result.version
    story_rows = ensure_story_rows(db, document_id, version, latest_result.result_json)
    selected = [row for row in story_rows if row.id in payload.row_ids]
    if not selected:
        return error_response(400, "no_rows_selected")

    prompt_rows = [
        {
            "id": row.id,
            "epic": row.epic,
            "title": row.title,
            "role": row.role,
            "type": row.type,
            "see": row.see,
            "do": row.do,
            "get": row.get,
            "acceptance": row.acceptance,
            "pert_hours": {
                "optimistic": row.optimistic,
                "most_likely": row.most_likely,
                "pessimistic": row.pessimistic,
                "expected": row.expected,
            },
        }
        for row in selected
    ]
    prompt = (
        "Re-estimate ONLY the pert_hours for the selected rows. "
        "Return ONLY a JSON array of objects with fields: id, pert_hours "
        "(optimistic, most_likely, pessimistic, expected). No extra text.\n"
        f"Rows:\n{prompt_rows}"
    )
    try:
        raw = call_ollama(prompt)
        updates = parse_llm_json(raw)
    except Exception:
        return error_response(500, "llm_error")
    if not isinstance(updates, list):
        return error_response(400, "llm_invalid_json")

    update_map = {item.get("id"): item.get("pert_hours", {}) for item in updates if isinstance(item, dict)}

    new_version = version + 1
    new_rows = []
    for row in story_rows:
        pert = update_map.get(row.id)
        optimistic = row.optimistic
        most_likely = row.most_likely
        pessimistic = row.pessimistic
        if pert:
            optimistic = float(pert.get("optimistic", optimistic))
            most_likely = float(pert.get("most_likely", most_likely))
            pessimistic = float(pert.get("pessimistic", pessimistic))
        expected = (optimistic + 4 * most_likely + pessimistic) / 6
        expected = round_to_step(expected, 0.5)
        new_rows.append(
            StoryRow(
                id=str(uuid4()),
                document_id=document_id,
                version=new_version,
                epic=row.epic,
                title=row.title,
                type=row.type,
                role=row.role,
                see=row.see,
                do=row.do,
                get=row.get,
                acceptance=row.acceptance,
                optimistic=optimistic,
                most_likely=most_likely,
                pessimistic=pessimistic,
                expected=expected,
            )
        )
    db.add_all(new_rows)
    result_json = build_result_json_from_rows(new_rows, latest_result.llm_model, document_id, new_version)
    result = Result(
        id=str(uuid4()),
        document_id=document_id,
        version=new_version,
        llm_model=latest_result.llm_model,
        result_json=result_json,
        raw_llm_output=None,
        validation_error=None,
        llm_prompt=prompt,
    )
    db.add(result)
    db.commit()
    return {"document_id": document_id, "version": new_version}


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
