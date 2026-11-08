<#
.SYNOPSIS
    Deploys OpenSource Investigator to a free Hugging Face Docker Space.

.DESCRIPTION
    Creates the Space if needed, stores the secrets in the Space settings
    (never in the code), and uploads the current project files, including
    uncommitted changes, as a single snapshot commit. Re-run it to redeploy.

.EXAMPLE
    $env:HF_TOKEN = "hf_..."   # a Hugging Face token with "write" access
    .\deploy\deploy_to_huggingface.ps1 -DatabaseUrl "postgresql://...neon.tech/neondb?sslmode=require"
#>
param(
    [string]$SpaceName = "opensource-investigator",
    [string]$DatabaseUrl,
    [string]$GroqApiKey,
    [string]$AdminPassword,
    [switch]$SkipSecrets
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$utf8 = New-Object System.Text.UTF8Encoding $false

function Read-Secret([string]$prompt) {
    $secure = Read-Host -Prompt $prompt -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-DotEnvValue([string]$key) {
    $envFile = Join-Path $root ".env"
    if (-not (Test-Path $envFile)) { return $null }
    foreach ($line in Get-Content $envFile) {
        if ($line -match "^\s*$key\s*=\s*(.+?)\s*$") { return $Matches[1].Trim('"', "'") }
    }
    return $null
}

function Invoke-HfApi([string]$method, [string]$path, $body) {
    $params = @{
        Method      = $method
        Uri         = "https://huggingface.co/api/$path"
        Headers     = @{ Authorization = "Bearer $token" }
        ContentType = "application/json"
    }
    if ($null -ne $body) { $params.Body = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Compress)) }
    return Invoke-RestMethod @params
}

# ── Authenticate ─────────────────────────────────────────────────────────────
$token = $env:HF_TOKEN
if (-not $token) { $token = Read-Secret "Hugging Face token (write access)" }
$owner = (Invoke-HfApi GET "whoami-v2" $null).name
$spaceId = "$owner/$SpaceName"
$appHost = ("$owner-$SpaceName").ToLower() -replace "[_.]", "-"
Write-Host "Deploying to Space $spaceId" -ForegroundColor Cyan

# ── Create the Space (no-op if it exists) ────────────────────────────────────
try {
    Invoke-HfApi POST "repos/create" @{ type = "space"; name = $SpaceName; sdk = "docker"; private = $false } | Out-Null
    Write-Host "  Created Space." -ForegroundColor Green
} catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 409) {
        Write-Host "  Space already exists." -ForegroundColor Green
    } else { throw }
}

# ── Secrets ──────────────────────────────────────────────────────────────────
if (-not $SkipSecrets) {
    if (-not $DatabaseUrl) { $DatabaseUrl = Read-Secret "Neon DATABASE_URL" }
    if ($DatabaseUrl -match "localhost|127\.0\.0\.1") {
        throw "DATABASE_URL points at this machine. Use the hosted Neon connection string."
    }
    if (-not $GroqApiKey) { $GroqApiKey = Get-DotEnvValue "GROQ_API_KEY" }
    if (-not $GroqApiKey) { $GroqApiKey = Read-Secret "GROQ_API_KEY" }
    $generatedPassword = $false
    if (-not $AdminPassword) {
        $AdminPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
        $generatedPassword = $true
    }

    $secrets = @{ DATABASE_URL = $DatabaseUrl; GROQ_API_KEY = $GroqApiKey; ADMIN_PASSWORD = $AdminPassword }
    foreach ($key in $secrets.Keys) {
        Invoke-HfApi POST "spaces/$spaceId/secrets" @{ key = $key; value = $secrets[$key] } | Out-Null
        Write-Host "  Stored secret $key" -ForegroundColor Green
    }
}

# ── Stage a clean snapshot of the project ───────────────────────────────────
$stage = Join-Path ([IO.Path]::GetTempPath()) ("hf-space-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    Push-Location $root
    $files = git ls-files --cached --others --exclude-standard
    Pop-Location
    foreach ($file in $files) {
        $source = Join-Path $root $file
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }  # deleted but not committed
        if ($file -like "frontend/.investigation_history*") { continue }
        $target = Join-Path $stage $file
        New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $target
    }

    # The Space reads its settings from YAML front matter in README.md.
    $emoji = [char]::ConvertFromUtf32(0x1F50D)
    $frontMatter = @"
---
title: OpenSource Investigator
emoji: $emoji
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Ask about any GitHub codebase and get cited answers
---

"@
    $readme = [IO.File]::ReadAllText((Join-Path $root "README.md"), $utf8)
    [IO.File]::WriteAllText((Join-Path $stage "README.md"), $frontMatter + $readme, $utf8)

    # ── Push ────────────────────────────────────────────────────────────────
    Push-Location $stage
    git init --quiet --initial-branch=main
    git config core.autocrlf false
    git config core.safecrlf false
    git add --all
    git -c user.name="$owner" -c user.email="$owner@users.noreply.huggingface.co" commit --quiet -m "Deploy $(Get-Date -Format s)"
    $basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${owner}:$token"))
    git -c credential.helper= -c "http.extraHeader=Authorization: Basic $basic" push --force --quiet "https://huggingface.co/spaces/$spaceId" main
    if ($LASTEXITCODE -ne 0) { throw "git push to the Space failed." }
    Pop-Location
} finally {
    if ((Get-Location).Path -eq $stage) { Pop-Location }
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Deployed. The first build takes about 5-10 minutes." -ForegroundColor Green
Write-Host "  Build logs:  https://huggingface.co/spaces/$spaceId"
Write-Host "  Public link: https://$appHost.hf.space"
if ($generatedPassword) {
    Write-Host ""
    Write-Host "Admin password (save it; it unlocks 'Owner access' in the sidebar):" -ForegroundColor Yellow
    Write-Host "  $AdminPassword"
}
