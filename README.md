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

URLs:

- API: http://localhost:8080
- UI: http://localhost:3000

## UI navigation (manual clicks)

1. Откройте `http://localhost:3000` → список пресейлов загрузится.
2. Нажмите `+ Создать пресейл` → введите имя → карточка появится в списке.
3. Нажмите `⋮` → `Переименовать` → обновите имя → карточка обновится.
4. Нажмите `⋮` → `Удалить` → карточка исчезнет.
5. Нажмите `⋮` → `Открыть` → откроется экран пресейла.
6. Введите текст задачи, выберите роли, загрузите файл → нажмите `Начать AI - анализ`.
7. Откроется экран результата, дождитесь статуса `done`.
8. Переключайте вкладки ролей и режимы `Сводно/Детально`.
9. Нажмите `Редактировать` → измените значения O/M/P → `Сохранить` (появится новая версия).
10. Нажмите `Экспорт в Jira` → загрузится JSON файл.
11. Нажмите `Сгенерировать альтернативу` → откроется новая карточка результата.

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

## UI verification

```powershell
# Backend should not serve UI anymore (expect 404)
curl -UseBasicParsing http://localhost:8080/ui

# Open UI in browser
Start-Process http://localhost:3000
```

## If LLM is slow: what to do

1. Check Ollama availability:

   ```powershell
   curl -UseBasicParsing http://localhost:11434/api/tags
   curl -UseBasicParsing http://localhost:8080/api/v1/llm/health
   ```

2. Expect longer runs on CPU. The worker uses a 10-minute timeout and retries; if it still fails, the UI shows the exact reason (e.g. timeout or HTTP 500).
3. Reduce input size: upload smaller PDFs or shorten the prompt to speed up local inference.

## Troubleshooting schema validation

If you see `llm_schema_validation_failed` or `llm_invalid_json`, the worker retries with a repair prompt and stores the raw model output plus the last validation error. In the UI result screen, use **Show details** to view the raw output that failed validation.

## Acceptance checklist

1. `http://localhost:3000` → список пресейлов из API.
2. Создать пресейл → появляется в списке.
3. Переименовать пресейл → имя обновлено.
4. Удалить пресейл → удалён из списка.
5. Открыть пресейл → экран детальной карточки.
6. Загрузить PDF через UI → файл в списке + MinIO.
7. Запустить анализ → переход к результату, статус `done`.
8. Экран результата: X эпиков/Y задач, переключение режимов.
9. Роли фильтруются вкладками.
10. Редактирование → сохранение → версия 2 доступна.
11. Альтернатива создаёт новый документ.
12. Экспорт JSON скачивает файл.

## Tests

```powershell
pytest
```
