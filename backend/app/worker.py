import json
import logging
import time
import traceback
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from docx import Document as DocxDocument
import httpx
from jsonschema import ValidationError, validate
from pypdf import PdfReader
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import Document, File as FileRecord, Result
from app.ollama_client import build_prompt, call_ollama, parse_llm_json
from app.settings import settings
from app.storage import ensure_bucket, get_s3_client

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "spec" / "json-schema" / "llm_output.schema.json"


def load_schema_text() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def load_schema() -> dict:
    return json.loads(load_schema_text())


def round_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(value / step) * step


def extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    chunks = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        chunks.append(page_text)
    return "\n".join(chunks)


def extract_docx_text(content: bytes) -> str:
    document = DocxDocument(BytesIO(content))
    return "\n".join([para.text for para in document.paragraphs])


def extract_txt_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return content.decode("cp1251")
        except UnicodeDecodeError as exc:
            raise ValueError("txt_decode_failed") from exc


def call_llm_with_retry(prompt: str) -> dict:
    raw = call_ollama(prompt)
    try:
        return parse_llm_json(raw)
    except json.JSONDecodeError:
        strict_prompt = (
            f"{prompt}\n"
            "Your previous response was invalid JSON. "
            "Return ONLY valid JSON that matches the schema. No prose."
        )
        raw_retry = call_ollama(strict_prompt)
        return parse_llm_json(raw_retry)


def update_document_status(db, document: Document, status: str, progress: int, message: str) -> None:
    document.status = status
    document.progress = progress
    document.message = message
    db.add(document)
    db.commit()


def process_document(document_id: str) -> None:
    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if not document:
            return
        try:
            files = db.scalars(select(FileRecord).where(FileRecord.presale_id == document.presale_id)).all()
            client = get_s3_client()
            ensure_bucket(client, settings.minio_bucket)

            extracted_sections = []
            for record in files:
                response = client.get_object(Bucket=settings.minio_bucket, Key=record.storage_key)
                content = response["Body"].read()
                filename = record.filename
                ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

                try:
                    if ext == "pdf":
                        text = extract_pdf_text(content)
                        if not text.strip():
                            update_document_status(
                                db, document, "error", 100, "scanned pdf not supported in MVP"
                            )
                            return
                    elif ext == "docx":
                        text = extract_docx_text(content)
                    elif ext == "txt":
                        text = extract_txt_text(content)
                    else:
                        text = ""
                except ValueError as exc:
                    update_document_status(db, document, "error", 100, str(exc))
                    return

                extracted_sections.append(f"----- FILE: {filename} -----\n{text}")

            update_document_status(db, document, "running", 30, "calling_llm")
            schema_text = load_schema_text()
            combined_text = "\n\n".join(extracted_sections)
            prompt = build_prompt(f"{document.prompt}\n\n{combined_text}", schema_text)
            try:
                llm_json = call_llm_with_retry(prompt)
            except json.JSONDecodeError:
                update_document_status(db, document, "error", 100, "llm_invalid_json")
                return
            except httpx.HTTPError:
                update_document_status(db, document, "error", 100, "llm_http_error")
                return

            schema = load_schema()
            try:
                validate(instance=llm_json, schema=schema)
            except ValidationError:
                update_document_status(db, document, "error", 100, "llm_schema_validation_failed")
                return

            round_to_hours = document.params_json.get("round_to_hours", 0.5)
            total_expected = 0.0
            for epic in llm_json.get("epics", []):
                for task in epic.get("tasks", []):
                    pert = task.get("pert_hours", {})
                    optimistic = float(pert.get("optimistic", 0))
                    most_likely = float(pert.get("most_likely", 0))
                    pessimistic = float(pert.get("pessimistic", 0))
                    expected = (optimistic + 4 * most_likely + pessimistic) / 6
                    expected = round_to_step(expected, round_to_hours)
                    pert["expected"] = round(expected, 2)
                    total_expected += pert["expected"]

            llm_json["totals"] = {"expected_hours": round(total_expected, 2)}

            update_document_status(db, document, "running", 90, "saving_result")
            result = Result(
                id=str(uuid4()),
                document_id=document.id,
                version=1,
                llm_model=settings.ollama_model,
                result_json=llm_json,
            )
            db.add(result)
            db.commit()

            update_document_status(db, document, "done", 100, "ok")
        except Exception:
            logging.error("Worker error for document %s", document_id)
            logging.error(traceback.format_exc())
            update_document_status(db, document, "error", 100, "worker_error")
    finally:
        db.close()


def pick_next_document_id(db) -> str | None:
    query = (
        select(Document)
        .where(Document.status == "queued")
        .order_by(Document.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    document = db.scalar(query)
    if not document:
        return None
    document.status = "running"
    document.progress = 10
    document.message = "extracting_text"
    db.add(document)
    db.commit()
    return document.id


def main() -> None:
    Base.metadata.create_all(bind=engine)
    schema_exists = SCHEMA_PATH.exists()
    if not schema_exists:
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

    print("worker: running")
    while True:
        db = SessionLocal()
        try:
            document_id = pick_next_document_id(db)
        finally:
            db.close()

        if not document_id:
            time.sleep(3)
            continue

        process_document(document_id)
        time.sleep(2)


if __name__ == "__main__":
    main()
