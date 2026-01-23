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
from sqlalchemy.exc import SQLAlchemyError

from app.db import Base, SessionLocal, engine, ensure_result_columns
from app.models import Document, File as FileRecord, LlmDebug, Result
from app.ollama_client import JSON_SKELETON, build_prompt, call_ollama, parse_llm_json
from app.settings import settings
from app.storage import ensure_bucket, get_s3_client

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "spec" / "json-schema" / "story_rows_output.schema.json"


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


HEADER_TITLES = {
    "Input Data",
    "Output Data",
    "Functional Requirements",
    "Non-Functional Requirements",
    "Acceptance Criteria",
    "User Scenario",
    "Data Sources",
    "Входные данные",
    "Выходные данные",
    "Функциональные требования",
    "Нефункциональные требования",
    "Критерии приёмки",
    "Пользовательский сценарий",
    "Источники данных",
}


def extract_json_object(text: str) -> dict:
    try:
        return parse_llm_json(text)
    except ValueError as exc:
        raise exc
    except json.JSONDecodeError:
        raise ValueError("llm_invalid_json")


def safe_update_document_status(document_id: str, status: str, progress: int, message: str) -> None:
    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if not document:
            return
        document.status = status
        document.progress = progress
        document.message = message
        db.add(document)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logging.error("Failed to persist status for document %s", document_id)
    finally:
        db.close()


def update_document_status(db, document: Document, status: str, progress: int, message: str) -> None:
    document.status = status
    document.progress = progress
    document.message = message
    db.add(document)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        safe_update_document_status(document.id, status, progress, message)


def limit_prompt_text(text: str, max_chars: int = 12000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def normalize_role(role: str) -> str:
    mapping = {
        "BA": "SA/BA",
        "SA": "SA/BA",
        "SA/BA": "SA/BA",
        "Data": "Data-engineer",
        "Data-engineer": "Data-engineer",
        "Data Engineer": "Data-engineer",
    }
    return mapping.get(role, role)


def apply_role_normalization(llm_json: dict) -> None:
    for row in llm_json.get("rows", []):
        role = row.get("role")
        if isinstance(role, str):
            row["role"] = normalize_role(role)


def is_story_title_low_quality(title: str) -> bool:
    normalized = title.strip()
    if not normalized:
        return True
    if normalized.endswith(":"):
        return True
    if normalized in HEADER_TITLES:
        return True
    if len(normalized.split()) < 4:
        return True
    return False


def has_low_quality_titles(llm_json: dict) -> bool:
    for row in llm_json.get("rows", []):
        title = row.get("story_title", "")
        if isinstance(title, str) and is_story_title_low_quality(title):
            return True
    return False


def has_required_row_counts(llm_json: dict) -> bool:
    types = [row.get("story_type") for row in llm_json.get("rows", [])]
    return types.count("functional") >= 3 and types.count("non_functional") >= 2


def has_required_row_fields(llm_json: dict) -> bool:
    for row in llm_json.get("rows", []):
        if len(row.get("acceptance", [])) < 2:
            return False
        if len(row.get("see", [])) < 1:
            return False
        if len(row.get("do", [])) < 1:
            return False
        if len(row.get("get", [])) < 1:
            return False
    return True


def has_uniform_pert_rows(llm_json: dict) -> bool:
    perts = {
        (row.get("pert_hours", {}).get("optimistic"),
         row.get("pert_hours", {}).get("most_likely"),
         row.get("pert_hours", {}).get("pessimistic"))
        for row in llm_json.get("rows", [])
    }
    return len(perts) == 1


def rows_text(llm_json: dict) -> str:
    return " ".join(
        " ".join(row.get(field, "") if isinstance(row.get(field), str) else " ".join(row.get(field, [])))
        for row in llm_json.get("rows", [])
        for field in ("epic", "story_title", "see", "do", "get", "acceptance")
    ).lower()


def has_role_coverage(llm_json: dict) -> bool:
    text = rows_text(llm_json)
    roles = {row.get("role") for row in llm_json.get("rows", [])}
    if any(keyword in text for keyword in ("api", "provider", "tariff", "file", "upload", "storage")):
        if "Data-engineer" not in roles:
            return False
    if any(keyword in text for keyword in ("deploy", "monitor", "logging", "observability")):
        if "DevOps" not in roles:
            return False
    return True


def log_llm_output(document_id: str, attempt: int, raw_output: str | None) -> None:
    if raw_output is None:
        logging.info("LLM output missing for document %s attempt %s", document_id, attempt)
        return
    snippet = raw_output[:2000]
    logging.info(
        "LLM output for document %s attempt %s: length=%s snippet=%s",
        document_id,
        attempt,
        len(raw_output),
        snippet,
    )


def save_llm_debug(
    db,
    document_id: str,
    attempt: int,
    prompt: str,
    raw_output: str | None,
    error_code: str | None,
    error_detail: str | None,
) -> None:
    entry = LlmDebug(
        id=str(uuid4()),
        document_id=document_id,
        attempt=attempt,
        prompt=prompt,
        raw_output=raw_output,
        error_code=error_code,
        error_detail=error_detail,
    )
    db.add(entry)
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logging.error("Failed to persist LLM debug info for document %s attempt %s", document_id, attempt)


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
                attempt_number = attempt + 1
                try:
                    raw_output = call_ollama(prompt)
                    log_llm_output(document.id, attempt_number, raw_output)
                    llm_json = extract_json_object(raw_output)
                    save_llm_debug(
                        db,
                        document.id,
                        attempt_number,
                        prompt,
                        raw_output,
                        None,
                        None,
                    )
                except ValueError as exc:
                    last_error = str(exc)
                    save_llm_debug(
                        db,
                        document.id,
                        attempt_number,
                        prompt,
                        raw_output,
                        last_error,
                        str(exc),
                    )
                except json.JSONDecodeError:
                    last_error = "llm_invalid_json"
                    save_llm_debug(
                        db,
                        document.id,
                        attempt_number,
                        prompt,
                        raw_output,
                        last_error,
                        "json_decode_error",
                    )
                except Exception as exc:
                    message = str(exc)
                    save_llm_debug(
                        db,
                        document.id,
                        attempt_number,
                        prompt,
                        raw_output,
                        "llm_http_error" if "llm_http_error:" in message else "unexpected_error",
                        message,
                    )
                    if "llm_http_error:" in message:
                        update_document_status(db, document, "error", 100, message)
                    else:
                        update_document_status(db, document, "error", 100, "unexpected_error")
                    return

                if llm_json is not None:
                    apply_role_normalization(llm_json)
                    llm_json["llm_model"] = settings.ollama_model
                    llm_json["document_id"] = document.id
                    if llm_json.get("document_id") == "string":
                        last_error = "llm_quality_gate_failed"
                        save_llm_debug(
                            db,
                            document.id,
                            attempt_number,
                            prompt,
                            raw_output,
                            last_error,
                            "document_id_placeholder",
                        )
                    elif has_low_quality_titles(llm_json):
                        last_error = "llm_quality_gate_failed"
                        save_llm_debug(
                            db,
                            document.id,
                            attempt_number,
                            prompt,
                            raw_output,
                            last_error,
                            "low_quality_titles",
                        )
                    elif not has_required_row_counts(llm_json):
                        last_error = "llm_quality_gate_failed"
                        save_llm_debug(
                            db,
                            document.id,
                            attempt_number,
                            prompt,
                            raw_output,
                            last_error,
                            "row_type_counts",
                        )
                    elif not has_required_row_fields(llm_json):
                        last_error = "llm_quality_gate_failed"
                        save_llm_debug(
                            db,
                            document.id,
                            attempt_number,
                            prompt,
                            raw_output,
                            last_error,
                            "missing_row_fields",
                        )
                    elif has_uniform_pert_rows(llm_json):
                        last_error = "llm_quality_gate_failed"
                        save_llm_debug(
                            db,
                            document.id,
                            attempt_number,
                            prompt,
                            raw_output,
                            last_error,
                            "uniform_pert_values",
                        )
                    elif not has_role_coverage(llm_json):
                        last_error = "llm_quality_gate_failed"
                        save_llm_debug(
                            db,
                            document.id,
                            attempt_number,
                            prompt,
                            raw_output,
                            last_error,
                            "missing_role_coverage",
                        )
                    else:
                        try:
                            validate(instance=llm_json, schema=schema)
                            break
                        except ValidationError:
                            last_error = "llm_schema_validation_failed"
                            validation_error = "llm_schema_validation_failed"
                            save_llm_debug(
                                db,
                                document.id,
                                attempt_number,
                                prompt,
                                raw_output,
                                last_error,
                                "schema_validation_failed",
                            )

                repair_prompt = (
                    "Return ONLY corrected JSON that matches the schema EXACTLY. No other text.\n"
                    "Replace section headers with concrete, actionable user stories.\n"
                    "Each row must include see/do/get and acceptance criteria, and be derived from the document.\n"
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
                    llm_prompt=prompt,
                )
                db.add(result)
                try:
                    db.commit()
                except SQLAlchemyError:
                    db.rollback()
                    safe_update_document_status(document.id, "error", 100, "db_error")
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
                    llm_prompt=prompt,
                )
                db.add(result)
                try:
                    db.commit()
                except SQLAlchemyError:
                    db.rollback()
                    safe_update_document_status(document.id, "error", 100, "db_error")
                return

            round_to_hours = document.params_json.get("round_to_hours", 0.5)
            total_expected = 0.0
            for row in llm_json.get("rows", []):
                pert = row.get("pert_hours", {})
                optimistic = float(pert.get("optimistic", 0))
                most_likely = float(pert.get("most_likely", 0))
                pessimistic = float(pert.get("pessimistic", 0))
                expected = (optimistic + 4 * most_likely + pessimistic) / 6
                expected = round_to_step(expected, round_to_hours)
                pert["expected"] = round(expected, 2)
                total_expected += pert["expected"]

            total_expected = round_to_step(total_expected, round_to_hours)
            llm_json["totals"] = {"expected_hours": round(total_expected, 2)}
            llm_json["llm_model"] = settings.ollama_model
            llm_json["document_id"] = document.id

            update_document_status(db, document, "running", 90, "saving_result")
            result = Result(
                id=str(uuid4()),
                document_id=document.id,
                version=1,
                llm_model=settings.ollama_model,
                result_json=llm_json,
                raw_llm_output=raw_output,
                validation_error=validation_error,
                llm_prompt=prompt,
            )
            db.add(result)
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                safe_update_document_status(document.id, "error", 100, "db_error")
                return

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
    ensure_result_columns()
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
