# Stop Backend Server Script
# Kills all processes using port 8000 and removes Qdrant lock

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Stopping Backend Server" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Kill processes on port 8000
Write-Host "[1/2] Stopping processes on port 8000..." -ForegroundColor Yellow
$port = 8000
$processes = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | 
             Select-Object -ExpandProperty OwningProcess -Unique

if ($processes) {
    foreach ($pid in $processes) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        Write-Host "      Stopped process: $pid" -ForegroundColor Yellow
    }
    Start-Sleep -Seconds 2
    Write-Host "      ✓ All processes stopped" -ForegroundColor Green
} else {
    Write-Host "      ✓ No processes found on port 8000" -ForegroundColor Green
}

# Step 2: Remove Qdrant lock file
Write-Host "[2/2] Removing Qdrant lock file..." -ForegroundColor Yellow
$lockFile = "data\qdrant\.lock"
if (Test-Path $lockFile) {
    Remove-Item $lockFile -Force
    Write-Host "      ✓ Lock file removed" -ForegroundColor Green
} else {
    Write-Host "      ✓ No lock file found" -ForegroundColor Green
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Server stopped successfully" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
