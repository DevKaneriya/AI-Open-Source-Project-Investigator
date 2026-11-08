<#
.SYNOPSIS
    Creates a free UptimeRobot monitor that keeps the Hugging Face Space awake.

.DESCRIPTION
    Free Spaces sleep after about 48 hours without traffic, and the next
    visitor then waits a minute or two for it to start. A request every five
    minutes keeps it running. Safe to re-run: an existing monitor for the same
    URL is left as it is.

.EXAMPLE
    $env:UPTIMEROBOT_API_KEY = "u123456-..."   # Main API key from UptimeRobot > Integrations & API
    .\deploy\setup_uptime_monitor.ps1 -Owner DevKaneriya
#>
param(
    [Parameter(Mandatory = $true)][string]$Owner,
    [string]$SpaceName = "opensource-investigator",
    [int]$IntervalSeconds = 300
)

$ErrorActionPreference = "Stop"

$apiKey = $env:UPTIMEROBOT_API_KEY
if (-not $apiKey) {
    $secure = Read-Host -Prompt "UptimeRobot Main API key" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

$appHost = ("$Owner-$SpaceName").ToLower() -replace "[_.]", "-"
$url = "https://$appHost.hf.space/"

function Invoke-UptimeRobot([string]$method, [hashtable]$fields) {
    $fields.api_key = $apiKey
    $fields.format = "json"
    $response = Invoke-RestMethod -Method Post -Uri "https://api.uptimerobot.com/v2/$method" -Body $fields
    if ($response.stat -ne "ok") {
        $detail = if ($response.error) { $response.error | ConvertTo-Json -Compress } else { "unknown error" }
        throw "UptimeRobot $method failed: $detail"
    }
    return $response
}

$existing = (Invoke-UptimeRobot "getMonitors" @{ search = $appHost }).monitors |
    Where-Object { $_.url -eq $url }
if ($existing) {
    Write-Host "A monitor for $url already exists (id $($existing.id)). Nothing to do." -ForegroundColor Green
    return
}

$created = Invoke-UptimeRobot "newMonitor" @{
    type          = 1  # HTTP(s)
    url           = $url
    friendly_name = "OpenSource Investigator (keep awake)"
    interval      = $IntervalSeconds
}
Write-Host "Created monitor $($created.monitor.id): $url is checked every $($IntervalSeconds / 60) minutes." -ForegroundColor Green
