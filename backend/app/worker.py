import json
import logging
import time
import traceback
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from docx import Document as DocxDocument
from jsonschema import ValidationError, validate
from pypdf import PdfReader
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import Document, File as FileRecord, Result
from app.ollama_client import JSON_SKELETON, build_prompt, call_ollama, parse_llm_json
from app.settings import settings
from app.storage import ensure_bucket, get_s3_client

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "spec" / "json-schema" / "llm_output.schema.json"


def load_schema_text() -> str:
    raw_bytes = SCHEMA_PATH.read_bytes()
    text = raw_bytes.decode("utf-8-sig")
    return text.lstrip("\ufeff")


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


def extract_json_object(text: str) -> dict:
    try:
        return parse_llm_json(text)
    except ValueError as exc:
        raise exc
    except json.JSONDecodeError:
        raise ValueError("llm_invalid_json")


def update_document_status(db, document: Document, status: str, progress: int, message: str) -> None:
    document.status = status
    document.progress = progress
    document.message = message
    db.add(document)
    db.commit()


def limit_prompt_text(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


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
            try:
                schema_text = load_schema_text()
            except (OSError, UnicodeError):
                update_document_status(db, document, "error", 100, "schema_load_failed")
                return
            combined_text = "\n\n".join(extracted_sections)
            combined_text = limit_prompt_text(combined_text, max_chars=12000)
            prompt = build_prompt(f"{document.prompt}\n\n{combined_text}", schema_text)
            try:
                schema = load_schema()
            except json.JSONDecodeError:
                update_document_status(db, document, "error", 100, "schema_load_failed")
                return

            llm_json = None
            raw_output = None
            last_error = None
            validation_error = None
            for attempt in range(3):
                try:
                    raw_output = call_ollama(prompt)
                    llm_json = extract_json_object(raw_output)
                except ValueError as exc:
                    last_error = str(exc)
                except json.JSONDecodeError:
                    last_error = "llm_invalid_json"
                except Exception as exc:
                    message = str(exc)
                    if "llm_http_error:" in message:
                        update_document_status(db, document, "error", 100, message)
                    else:
                        update_document_status(db, document, "error", 100, "unexpected_error")
                    return

                if llm_json is not None:
                    try:
                        validate(instance=llm_json, schema=schema)
                        break
                    except ValidationError:
                        last_error = "llm_schema_validation_failed"
                        validation_error = "llm_schema_validation_failed"

                repair_prompt = (
                    "Return ONLY corrected JSON that matches the schema EXACTLY. No other text.\n"
                    f"Schema:\n{schema_text}\n"
                    "You MUST strictly follow this structure exactly as shown:\n"
                    f"{JSON_SKELETON}\n"
                    f"Invalid output:\n{raw_output}\n"
                )
                prompt = repair_prompt

            if llm_json is None:
                update_document_status(db, document, "error", 100, last_error or "llm_invalid_json")
                result = Result(
                    id=str(uuid4()),
                    document_id=document.id,
                    version=1,
                    llm_model=settings.ollama_model,
                    result_json={"error": last_error or "llm_invalid_json"},
                    raw_llm_output=raw_output,
                    validation_error=validation_error or last_error,
                )
                db.add(result)
                db.commit()
                return

            try:
                validate(instance=llm_json, schema=schema)
            except ValidationError:
                update_document_status(db, document, "error", 100, "llm_schema_validation_failed")
                result = Result(
                    id=str(uuid4()),
                    document_id=document.id,
                    version=1,
                    llm_model=settings.ollama_model,
                    result_json={"error": "llm_schema_validation_failed"},
                    raw_llm_output=raw_output,
                    validation_error="llm_schema_validation_failed",
                )
                db.add(result)
                db.commit()
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
                raw_llm_output=raw_output,
                validation_error=validation_error,
            )
            db.add(result)
            db.commit()

            update_document_status(db, document, "done", 100, "ok")
        except Exception:
            logging.error("Worker error for document %s", document_id)
            logging.error(traceback.format_exc())
            update_document_status(db, document, "error", 100, "unexpected_error")
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
