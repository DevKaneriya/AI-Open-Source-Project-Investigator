# Regression Test Suite for AI-Open-Source-Project-Investigator
# Tests all issues and validates fixes

$ErrorActionPreference = 'Continue'
$baseUrl = "http://127.0.0.1:8000"
$results = @()

function Test-Query {
    param(
        [string]$Name,
        [string]$Query,
        [string]$ExpectedType,
        [int]$MinCitations = 1
    )
    
    Write-Host "=== Test: $Name ===" -ForegroundColor Cyan
    Write-Host "Query: $Query"
    
    try {
        $encodedQuery = [System.Web.HttpUtility]::UrlEncode($Query)
        $url = "$baseUrl/repositories/1/investigate?query=$encodedQuery"
        $response = Invoke-RestMethod -Uri $url -Method GET -TimeoutSec 90
        
        $test = [PSCustomObject]@{
            Name = $Name
            Query = $Query
            Status = "PASS"
            QuestionType = $response.question_type
            Confidence = $response.investigation.confidence
            Citations = $response.investigation.citations.Count
            AnswerLength = $response.investigation.answer.Length
            InvalidCitations = ($response.investigation.citations | Where-Object { $_.start_line -eq 0 -and $_.end_line -eq 0 }).Count
            ErrorMessage = ""
        }
        
        # Validate
        if ($response.question_type -ne $ExpectedType) {
            $test.Status = "FAIL"
            $test.ErrorMessage = "Expected type '$ExpectedType', got '$($response.question_type)'"
        }
        elseif ($response.investigation.citations.Count -lt $MinCitations) {
            $test.Status = "FAIL"
            $test.ErrorMessage = "Expected at least $MinCitations citations, got $($response.investigation.citations.Count)"
        }
        elseif ($test.InvalidCitations -gt 0) {
            $test.Status = "FAIL"
            $test.ErrorMessage = "Found $($test.InvalidCitations) invalid citations with 0-0 line range"
        }
        
        $status = if ($test.Status -eq "PASS") { "[PASS]" } else { "[FAIL]" }
        $color = if ($test.Status -eq "PASS") { "Green" } else { "Red" }
        
        Write-Host "$status Status: $($test.Status)" -ForegroundColor $color
        Write-Host "  Type: $($test.QuestionType) | Confidence: $($test.Confidence)"
        Write-Host "  Citations: $($test.Citations) (Invalid: $($test.InvalidCitations))"
        Write-Host "  Answer: $($test.AnswerLength) chars"
        
        if ($test.ErrorMessage) {
            Write-Host "  Error: $($test.ErrorMessage)" -ForegroundColor Red
        }
        
        $results += $test
        
    } catch {
        Write-Host "[FAILED] FAILED: $($_.Exception.Message)" -ForegroundColor Red
        
        $results += [PSCustomObject]@{
            Name = $Name
            Query = $Query
            Status = "ERROR"
            QuestionType = ""
            Confidence = ""
            Citations = 0
            AnswerLength = 0
            InvalidCitations = 0
            ErrorMessage = $_.Exception.Message
        }
    }
    
    Write-Host ""
    Start-Sleep -Seconds 3  # Rate limit protection
}

# Load System.Web for URL encoding
Add-Type -AssemblyName System.Web

Write-Host "===========================================" -ForegroundColor Yellow
Write-Host "  Regression Test Suite" -ForegroundColor Yellow
Write-Host "===========================================" -ForegroundColor Yellow
Write-Host ""

# Issue 1: Query analyzer truncation (fixed)
Test-Query -Name "Email Task Query" `
    -Query "How are background email tasks configured and dispatched in this project" `
    -ExpectedType "cross_cutting_concern" `
    -MinCitations 1

# Issue 2: Dependency analysis (fixed - now retrieves full manifests)
Test-Query -Name "Dependency Analysis with Versions" `
    -Query "List the exact frontend and backend dependencies with their versions from package manifests" `
    -ExpectedType "dependency_analysis" `
    -MinCitations 1

# New Test: Feature flow tracing
Test-Query -Name "Cart Add Operation Flow" `
    -Query "Trace how adding an item to cart works from the frontend method through to database" `
    -ExpectedType "feature_flow" `
    -MinCitations 3

# New Test: Absence claims
Test-Query -Name "Non-existent Function Search" `
    -Query "Find the AutoRefundAllFailedPayments function and show its implementation" `
    -ExpectedType "symbol_lookup" `
    -MinCitations 0

# Issue 3: Invalid citations (fixed)
# (Already tested above - checking for InvalidCitations count)

# Regression: Previously passing tests
Test-Query -Name "CartSyncView Definition" `
    -Query "Where is CartSyncView defined" `
    -ExpectedType "symbol_lookup" `
    -MinCitations 1

Test-Query -Name "Cart Synchronization" `
    -Query "How does cart synchronization work" `
    -ExpectedType "feature_flow" `
    -MinCitations 3

Test-Query -Name "Unknown Function" `
    -Query "Find the AutoRefundAllFailedPayments function" `
    -ExpectedType "symbol_lookup" `
    -MinCitations 0

Test-Query -Name "Database Configuration" `
    -Query "Does this repository use SQLite or PostgreSQL" `
    -ExpectedType "architectural_overview" `
    -MinCitations 1

# Summary
Write-Host "===========================================" -ForegroundColor Yellow
Write-Host "  Test Summary" -ForegroundColor Yellow
Write-Host "===========================================" -ForegroundColor Yellow
Write-Host ""

$passed = ($results | Where-Object { $_.Status -eq "PASS" }).Count
$failed = ($results | Where-Object { $_.Status -in @("FAIL", "ERROR") }).Count
$total = $results.Count

Write-Host "Total: $total | Passed: $passed | Failed: $failed" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Yellow" })
Write-Host ""

# Detailed results
$results | Format-Table -Property Name, Status, QuestionType, Confidence, Citations, InvalidCitations, AnswerLength -AutoSize

# Return exit code
if ($failed -gt 0) {
    exit 1
} else {
    exit 0
}
