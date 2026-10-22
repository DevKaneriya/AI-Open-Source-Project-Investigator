# Troubleshooting Guide

## Common Issues

### 1. Qdrant Lock Error

**Error:**
```
RuntimeError: Storage folder ...\data\qdrant is already accessed by another instance of Qdrant client.
```

**Solution:**
```powershell
.\stop_server.ps1
.\start_server.ps1
```

**Manual fix:**
```powershell
# Kill processes on port 8000
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force }

# Remove lock file
Remove-Item "data\qdrant\.lock" -Force
```

---

### 2. Port 8000 Already in Use

**Error:**
```
ERROR: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)
```

**Solution:**
```powershell
.\stop_server.ps1
```

---

### 3. Module Not Found

**Error:**
```
ModuleNotFoundError: No module named 'X'
```

**Solution:**
```powershell
uv pip install -r requirements.txt
```

---

### 4. GROQ_API_KEY Not Configured

**Error:**
```
RuntimeError: GROQ_API_KEY is not configured.
```

**Solution:**
```powershell
Copy-Item .env.example .env
# Edit .env and add:  GROQ_API_KEY=your_key_here
```

---

### 5. DATABASE_URL Not Configured

**Error:**
```
RuntimeError: DATABASE_URL is not configured.
```

**Solution:**
Add to `.env`:
```
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/ai_opensource_investigator
```

Then apply migrations:
```powershell
.venv\Scripts\alembic.exe upgrade head
```

---

### 6. No Investigation Results (Empty Evidence)

**Symptom:** Investigation returns `confidence: insufficient` with "No evidence was retrieved."

**Cause:** Repository is not indexed or the index is stale.

**Solution:**
```powershell
# Re-index (code symbols + chunks + vectors)
python backend\scripts\reindex.py --repo-id 1

# Or check what's indexed
python check_data.py
```

---

### 7. Rate Limit Hit

**Error in bulk results:**
```json
"error_type": "rate_limit",
"error": "Rate limit exceeded. Try again in Xm..."
```

**Cause:** Groq free tier allows 200k tokens/day (~25–100 investigations depending on complexity).

**Solutions:**
- Wait for the daily quota to reset (resets at midnight UTC)
- Increase `delay_between_queries` in bulk requests: `?delay_between_queries=5.0`
- Reduce bulk batch size to 3–5 queries

---

### 8. Alembic Migration Issues

**Check migration status:**
```powershell
.venv\Scripts\alembic.exe current
.venv\Scripts\alembic.exe history
```

**Apply pending migrations:**
```powershell
.venv\Scripts\alembic.exe upgrade head
```

**Verify schema matches models:**
```powershell
python backend\scripts\verify_schema.py
```

---

### 9. Query Analysis Using Fallback

**Symptom:** Bulk results show `[FALLBACK]` on the question type.

**Cause:** The LLM returned truncated or malformed JSON during query analysis. The system fell back to deterministic keyword extraction and continued the investigation.

**Effect:** Retrieval quality may be slightly lower — the fallback uses simpler keyword matching rather than LLM-generated search strategies. The investigation still completes and returns evidence-grounded results.

**Fix:** Usually transient. If persistent, check `GROQ_API_KEY` is valid and the model is available.

---

## Quick Diagnostics

```powershell
# Check what's indexed
python check_data.py

# Check call graph relationships
python check_relationships.py

# Verify DB schema matches models
python backend\scripts\verify_schema.py

# Check if server is responding
Invoke-RestMethod -Uri "http://127.0.0.1:8000/docs" -Method GET

# View processes on port 8000
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue

# Check for Qdrant lock file
Test-Path "data\qdrant\.lock"
```

---

## Prevention

1. Always use `.\stop_server.ps1` to stop the server — don't just close the terminal
2. Use `.\start_server.ps1` to start — it handles port cleanup and lock removal
3. Never run two server instances at once (Qdrant uses exclusive file locking)
4. After changing investigator service code, re-index with `python backend\scripts\reindex.py --repo-id 1`
