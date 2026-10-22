# Create a new database migration
# Usage: .\backend\scripts\create_migration.ps1 "Add new column to table"

param(
    [Parameter(Mandatory=$true)]
    [string]$Message
)

$ErrorActionPreference = "Stop"

Write-Host "Creating new migration: $Message" -ForegroundColor Cyan

# Change to project root
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $projectRoot

# Generate migration
& .venv\Scripts\alembic.exe revision --autogenerate -m $Message

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nMigration created successfully!" -ForegroundColor Green
    Write-Host "`nNext steps:" -ForegroundColor Yellow
    Write-Host "1. Review the generated migration file in backend\alembic\versions\"
    Write-Host "2. Edit if needed (Alembic doesn't catch everything)"
    Write-Host "3. Apply with: alembic upgrade head"
    Write-Host "4. Verify with: python backend\scripts\verify_schema.py"
} else {
    Write-Host "`nFailed to create migration!" -ForegroundColor Red
    exit 1
}
