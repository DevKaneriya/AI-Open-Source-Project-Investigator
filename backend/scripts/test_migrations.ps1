# Test migration workflow with a fresh database
# WARNING: This creates and drops a test database
# Usage: .\backend\scripts\test_migrations.ps1

param(
    [string]$TestDbName = "ai_opensource_investigator_test",
    [string]$PostgresUser = "postgres"
)

$ErrorActionPreference = "Stop"

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "MIGRATION TESTING - Fresh Database" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "Test Database: $TestDbName" -ForegroundColor Yellow
Write-Host ""

# Change to project root
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projectRoot

# Check if database exists
Write-Host "Checking for existing test database..." -ForegroundColor Yellow
$dbExists = & psql -U $PostgresUser -lqt | Select-String -Pattern "\s$TestDbName\s"

if ($dbExists) {
    Write-Host "Dropping existing test database..." -ForegroundColor Yellow
    & dropdb -U $PostgresUser $TestDbName
}

# Create fresh database
Write-Host "Creating fresh test database..." -ForegroundColor Yellow
& createdb -U $PostgresUser $TestDbName

if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to create test database!" -ForegroundColor Red
    exit 1
}

# Get password from .env
$envFile = Get-Content .env
$dbUrl = $envFile | Where-Object { $_ -match "^DATABASE_URL=" }
if ($dbUrl -match "postgres:([^@]+)@") {
    $password = $matches[1]
} else {
    Write-Host "Could not extract password from .env file" -ForegroundColor Red
    exit 1
}

# Set test database URL
$testDbUrl = "postgresql+psycopg://${PostgresUser}:${password}@localhost:5432/${TestDbName}"
$env:DATABASE_URL = $testDbUrl

Write-Host "`nTest DATABASE_URL: $testDbUrl" -ForegroundColor Cyan

# Apply migrations
Write-Host "`n------------------------------------------------------------------"
Write-Host "Applying all migrations to fresh database..." -ForegroundColor Yellow
& .venv\Scripts\alembic.exe upgrade head

if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to apply migrations!" -ForegroundColor Red
    Write-Host "Cleaning up test database..." -ForegroundColor Yellow
    & dropdb -U $PostgresUser $TestDbName
    exit 1
}

# Verify schema
Write-Host "`n------------------------------------------------------------------"
Write-Host "Verifying schema..." -ForegroundColor Yellow
python backend\scripts\verify_schema.py

$verifyResult = $LASTEXITCODE

# Check current revision
Write-Host "`n------------------------------------------------------------------"
Write-Host "Current migration revision:" -ForegroundColor Yellow
& .venv\Scripts\alembic.exe current

# Cleanup
Write-Host "`n------------------------------------------------------------------"
Write-Host "Cleaning up test database..." -ForegroundColor Yellow
& dropdb -U $PostgresUser $TestDbName

# Results
Write-Host "`n==================================================================" -ForegroundColor Cyan
if ($verifyResult -eq 0) {
    Write-Host "✅ MIGRATION TEST PASSED" -ForegroundColor Green
    Write-Host "All migrations applied successfully to fresh database" -ForegroundColor Green
    Write-Host "Schema matches SQLAlchemy models" -ForegroundColor Green
} else {
    Write-Host "❌ MIGRATION TEST FAILED" -ForegroundColor Red
    Write-Host "Schema verification found issues" -ForegroundColor Red
    exit 1
}
Write-Host "==================================================================" -ForegroundColor Cyan
