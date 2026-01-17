# API (MVP)
Base URL: /api/v1

## Create presale
POST /presales
Request:
{ "name": "string" }
Response:
{ "id": "uuid", "name": "string", "created_at": "iso" }

## Upload file (simple MVP)
POST /files/upload?presale_id=<uuid>
Multipart form:
- file: PDF/DOCX/TXT
Response:
{
  "file_id": "uuid",
  "presale_id": "uuid",
  "filename": "string",
  "content_type": "string",
  "size_bytes": 123,
  "storage_key": "string"
}

## Start analysis
POST /documents/start
Request:
{
  "presale_id": "uuid",
  "prompt": "string",
  "params": {
    "roles": ["Backend","Frontend","BA"],
    "round_to_hours": 0.5
  }
}
Response:
{ "document_id": "uuid", "status": "queued" }

## Poll status
GET /documents/{document_id}/status
Response:
{
  "document_id": "uuid",
  "status": "queued|running|done|error",
  "progress": 0-100,
  "message": "string"
}

## Get result
GET /documents/{document_id}/result
Response:
{
  "document_id": "uuid",
  "version": 1,
  "llm_model": "string",
  "epics": [
    {
      "title": "string",
      "tasks": [
        {
          "title": "string",
          "role": "Backend|Frontend|BA|DevOps|Data",
          "pert_hours": {
            "optimistic": 1,
            "most_likely": 2,
            "pessimistic": 4,
            "expected": 2.5
          }
        }
      ]
    }
  ],
  "totals": { "expected_hours": 123.0 }
}
