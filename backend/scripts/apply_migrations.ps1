# Apply pending database migrations
# Usage: .\backend\scripts\apply_migrations.ps1

$ErrorActionPreference = "Stop"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "DATABASE MIGRATION APPLICATION" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# Change to project root
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projectRoot

Write-Host "`nChecking current migration status..." -ForegroundColor Yellow
& .venv\Scripts\alembic.exe current

Write-Host "`n------------------------------------------------------------------"
Write-Host "Applying migrations..." -ForegroundColor Yellow
& .venv\Scripts\alembic.exe upgrade head

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n------------------------------------------------------------------"
    Write-Host "Migrations applied successfully!" -ForegroundColor Green
    
    Write-Host "`nCurrent migration:" -ForegroundColor Yellow
    & .venv\Scripts\alembic.exe current
    
    Write-Host "`n------------------------------------------------------------------"
    Write-Host "Verifying schema..." -ForegroundColor Yellow
    python backend\scripts\verify_schema.py
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n==================================================================" -ForegroundColor Green
        Write-Host "✅ MIGRATIONS COMPLETE - Database is up to date" -ForegroundColor Green
        Write-Host "==================================================================" -ForegroundColor Green
    } else {
        Write-Host "`n⚠️  Schema verification found issues" -ForegroundColor Yellow
    }
} else {
    Write-Host "`nFailed to apply migrations!" -ForegroundColor Red
    exit 1
}
