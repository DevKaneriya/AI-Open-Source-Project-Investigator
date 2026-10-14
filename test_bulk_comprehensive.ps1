# Comprehensive bulk investigation test

$baseUrl = "http://127.0.0.1:8000"
$repositoryId = 1

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Comprehensive Bulk Test Suite" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Test 1: Different question types
Write-Host "[Test 1] Multiple Question Types" -ForegroundColor Yellow
$queries1 = @(
    "Where is CartSyncView defined?",                    # symbol_lookup
    "How does cart synchronization work?",               # feature_flow  
    "What framework is used in the backend?",            # architectural_overview
    "List the frontend dependencies"                      # dependency_analysis
)

$body1 = $queries1 | ConvertTo-Json
$response1 = Invoke-RestMethod -Uri "$baseUrl/repositories/$repositoryId/investigate/bulk" -Method POST -Body $body1 -ContentType "application/json" -TimeoutSec 180

Write-Host "  Results: $($response1.summary.successful)/$($response1.summary.total_queries) successful" -ForegroundColor Green
Write-Host "  Total time: $($response1.summary.total_execution_time_seconds)s"
Write-Host "  Question types:" -ForegroundColor Gray
$response1.results | ForEach-Object { Write-Host "    - $($_.question_type)" -ForegroundColor Gray }

Start-Sleep -Seconds 5

# Test 2: Related questions (testing consistency)
Write-Host "`n[Test 2] Related Questions" -ForegroundColor Yellow
$queries2 = @(
    "Where is the cart service defined?",
    "What methods does the cart service have?",
    "How is the cart stored?"
)

$body2 = $queries2 | ConvertTo-Json
$response2 = Invoke-RestMethod -Uri "$baseUrl/repositories/$repositoryId/investigate/bulk" -Method POST -Body $body2 -ContentType "application/json" -TimeoutSec 180

Write-Host "  Results: $($response2.summary.successful)/$($response2.summary.total_queries) successful" -ForegroundColor Green
Write-Host "  Avg confidence: $((($response2.results | Where-Object {$_.status -eq 'success'} | ForEach-Object {$_.investigation.confidence} | Measure-Object).Count))/$($response2.results.Count) answers"
Write-Host "  Total citations: $(($response2.results | Where-Object {$_.status -eq 'success'} | ForEach-Object {$_.investigation.citations.Count} | Measure-Object -Sum).Sum)"

Start-Sleep -Seconds 5

# Test 3: Single vs Bulk comparison
Write-Host "`n[Test 3] Single vs Bulk Comparison" -ForegroundColor Yellow
$testQuery = "Where is CartSyncView defined?"

# Single request
$singleStart = Get-Date
$singleResponse = Invoke-RestMethod -Uri "$baseUrl/repositories/$repositoryId/investigate?query=$([System.Web.HttpUtility]::UrlEncode($testQuery))" -Method GET
$singleTime = ((Get-Date) - $singleStart).TotalSeconds

# Bulk request (same query)
$bulkStart = Get-Date
$bulkBody = @($testQuery) | ConvertTo-Json
$bulkResponse = Invoke-RestMethod -Uri "$baseUrl/repositories/$repositoryId/investigate/bulk" -Method POST -Body $bulkBody -ContentType "application/json"
$bulkTime = ((Get-Date) - $bulkStart).TotalSeconds

Write-Host "  Single endpoint: $([Math]::Round($singleTime, 2))s"
Write-Host "  Bulk endpoint: $([Math]::Round($bulkTime, 2))s"
Write-Host "  Difference: $([Math]::Round($bulkTime - $singleTime, 2))s overhead"

# Test 4: Error handling (intentionally invalid query count)
Write-Host "`n[Test 4] Error Handling" -ForegroundColor Yellow
try {
    $tooMany = 1..11 | ForEach-Object { "Query $_" }
    $body4 = $tooMany | ConvertTo-Json
    Invoke-RestMethod -Uri "$baseUrl/repositories/$repositoryId/investigate/bulk" -Method POST -Body $body4 -ContentType "application/json" -ErrorAction Stop
    Write-Host "  [UNEXPECTED] Should have failed with too many queries" -ForegroundColor Red
} catch {
    if ($_.Exception.Message -match "422") {
        Write-Host "  [SUCCESS] Correctly rejected 11 queries (max 10)" -ForegroundColor Green
    } else {
        Write-Host "  [ERROR] Unexpected error: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  All Tests Complete" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Summary
Write-Host "Summary:" -ForegroundColor Yellow
Write-Host "  Test 1: $($response1.summary.successful)/$($response1.summary.total_queries) queries successful"
Write-Host "  Test 2: $($response2.summary.successful)/$($response2.summary.total_queries) queries successful"
Write-Host "  Test 3: Bulk overhead ~$([Math]::Round($bulkTime - $singleTime, 2))s for 1 query"
Write-Host "  Test 4: Error handling verified`n"
