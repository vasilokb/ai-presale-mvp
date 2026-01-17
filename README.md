# AI Presale MVP

This repository contains a minimal FastAPI + Postgres + MinIO + Ollama MVP for presale document analysis.

## Quick start

```powershell
# Build and start services
Docker compose up -d --build
```

Health check:

```powershell
curl -UseBasicParsing http://localhost:8080/health
```

## Manual test (PowerShell)

```powershell
# 1) Create presale
$presale = curl -UseBasicParsing -Method Post http://localhost:8080/api/v1/presales `
  -ContentType "application/json" `
  -Body '{"name":"Test presale"}' | ConvertFrom-Json

# 2) Upload PDF
curl -UseBasicParsing -Method Post "http://localhost:8080/api/v1/files/upload?presale_id=$($presale.id)" `
  -Form @{ file = Get-Item "fixtures/sample.pdf" }

# 3) Start analysis
$doc = curl -UseBasicParsing -Method Post http://localhost:8080/api/v1/documents/start `
  -ContentType "application/json" `
  -Body "{\"presale_id\":\"$($presale.id)\",\"prompt\":\"Разбей требования на эпики и задачи и оцени PERT\",\"params\":{\"roles\":[\"Backend\",\"Frontend\",\"BA\",\"DevOps\",\"Data\"],\"round_to_hours\":0.5}}" | ConvertFrom-Json

# 4) Poll status
curl -UseBasicParsing http://localhost:8080/api/v1/documents/$($doc.document_id)/status

# 5) Get result
curl -UseBasicParsing http://localhost:8080/api/v1/documents/$($doc.document_id)/result
```

## Ollama smoke check

```powershell
# Ensure model exists
curl -UseBasicParsing http://localhost:11434/api/tags

# Start analysis and verify it reaches done
$status = curl -UseBasicParsing http://localhost:8080/api/v1/documents/$($doc.document_id)/status
```

## Tests

```powershell
pytest
```
