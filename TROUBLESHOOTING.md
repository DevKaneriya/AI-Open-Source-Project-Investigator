# Troubleshooting Guide

## Common Issues

### 1. Qdrant Lock Error

**Error:**
```
RuntimeError: Storage folder C:\...\data\qdrant is already accessed by another instance of Qdrant client.
```

**Solution:**
```powershell
# Run the stop script
.\stop_server.ps1

# Then start the server again
.\start_server.ps1
```

**Manual Fix:**
1. Kill processes on port 8000:
   ```powershell
   Get-NetTCPConnection -LocalPort 8000 | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
   ```

2. Remove lock file:
   ```powershell
   Remove-Item "data\qdrant\.lock" -Force
   ```

### 2. Port 8000 Already in Use

**Error:**
```
ERROR: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)
```

**Solution:**
```powershell
# Run the stop script
.\stop_server.ps1

# Or manually kill the process
$port = 8000
Get-NetTCPConnection -LocalPort $port | Select-Object -ExpandProperty OwningProcess -Unique | Stop-Process -Force
```

### 3. Module Not Found Errors

**Error:**
```
ModuleNotFoundError: No module named 'X'
```

**Solution:**
```powershell
# Activate virtual environment
.venv\Scripts\activate

# Reinstall dependencies
pip install -r requirements.txt
```

### 4. GROQ_API_KEY Not Set

**Error:**
```
Configuration error: GROQ_API_KEY environment variable is not set
```

**Solution:**
1. Copy `.env.example` to `.env`:
   ```powershell
   Copy-Item .env.example .env
   ```

2. Edit `.env` and add your GROQ API key:
   ```
   GROQ_API_KEY=your_key_here
   ```

### 5. Database Not Found

**Error:**
```
Repository not found
```

**Solution:**
```python
# Check database
python check_data.py

# If empty, you need to index a repository first
# Use the API to add and index a repository
```

### 6. Vector Index Missing

**Error:**
```
Collection 'repository_code' not found
```

**Solution:**
The vector index is created automatically when you run:
```
POST /repositories/{id}/vector-index
```

### 7. Server Won't Stop

**Solution:**
```powershell
# Nuclear option - kill all Python processes
Stop-Process -Name python -Force
```

⚠️ **Warning:** This will kill ALL Python processes, including other projects!

## Quick Commands

```powershell
# Check what's running on port 8000
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue

# Check for Python processes
Get-Process python*

# Check if lock file exists
Test-Path "data\qdrant\.lock"

# View database stats
python check_data.py

# Test Qdrant connection
python check_redis.py
```

## Prevention

To avoid issues:
1. Always use `.\stop_server.ps1` to stop the server (don't just Ctrl+C)
2. Use `.\start_server.ps1` to start the server (handles cleanup automatically)
3. Don't run multiple instances of the backend simultaneously
4. Keep your virtual environment activated when working

## Still Having Issues?

Check the logs in the terminal where Uvicorn is running for detailed error messages.
