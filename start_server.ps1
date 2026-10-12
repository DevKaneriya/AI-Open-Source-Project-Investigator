# Start Backend Server Script
# This script ensures clean startup by removing locks and killing stale processes

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Starting AI Investigator Backend Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Kill any existing Python/Uvicorn processes on port 8000
Write-Host "[1/4] Checking for existing processes on port 8000..." -ForegroundColor Yellow
$port = 8000
$processes = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | 
             Select-Object -ExpandProperty OwningProcess -Unique

if ($processes) {
    Write-Host "      Found processes using port 8000, terminating..." -ForegroundColor Yellow
    foreach ($pid in $processes) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    Write-Host "      ✓ Processes terminated" -ForegroundColor Green
} else {
    Write-Host "      ✓ Port 8000 is free" -ForegroundColor Green
}

# Step 2: Remove Qdrant lock file if it exists
Write-Host "[2/4] Checking Qdrant lock file..." -ForegroundColor Yellow
$lockFile = "data\qdrant\.lock"
if (Test-Path $lockFile) {
    Remove-Item $lockFile -Force
    Write-Host "      ✓ Lock file removed" -ForegroundColor Green
} else {
    Write-Host "      ✓ No lock file found" -ForegroundColor Green
}

# Step 3: Verify virtual environment
Write-Host "[3/4] Verifying virtual environment..." -ForegroundColor Yellow
if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "      ✓ Virtual environment found" -ForegroundColor Green
} else {
    Write-Host "      ✗ Virtual environment not found!" -ForegroundColor Red
    Write-Host "      Please create it with: python -m venv .venv" -ForegroundColor Red
    exit 1
}

# Step 4: Start the server
Write-Host "[4/4] Starting Uvicorn server..." -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Server starting on http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "API Docs: http://127.0.0.1:8000/docs" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

# Start server (without --reload to avoid file watching issues)
& .venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
