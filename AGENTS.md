# AI Presale MVP (Ollama + PDF upload)

Goal (MVP):
- Run locally via Docker Compose on Windows.
- Upload PDF/DOCX/TXT -> store in MinIO.
- Start analysis -> background job.
- Extract text from PDF (text layer only).
- Call local Ollama.
- Return JSON: epics/tasks with PERT estimates.

Definition of Done:
1) docker compose up -d --build works.
2) GET /health returns {"status":"ok"}.
3) POST /api/v1/presales creates presale.
4) POST /api/v1/files/upload uploads a file to MinIO and stores metadata in Postgres.
5) POST /api/v1/documents/start creates document job (queued).
6) GET /api/v1/documents/{id}/status shows progress.
7) Worker processes job:
   - extracts PDF text layer (no OCR in MVP)
   - if empty text -> status=error, message="scanned pdf not supported in MVP"
   - calls Ollama and requests strict JSON matching spec/json-schema/llm_output.schema.json
   - validates schema
   - computes expected PERT and rounds to 0.5 hours
   - saves result -> status=done
8) GET /api/v1/documents/{id}/result returns stored JSON result.

Tech (locked):
- FastAPI + Uvicorn
- Postgres
- MinIO
- Ollama
- No Kafka. No extra microservices.
