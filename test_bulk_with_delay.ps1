# Test bulk API with rate limit handling

$baseUrl = "http://127.0.0.1:8000"
$repositoryId = 1
$delayBetweenQueries = 3  # 3 seconds between queries

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Bulk API with Rate Limit Handling" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Small set of queries to test
$queries = @(
    "Where is CartSyncView defined?",
    "What methods does CartSyncView have?"
)

Write-Host "Testing with $($queries.Count) queries" -ForegroundColor Yellow
Write-Host "Delay between queries: ${delayBetweenQueries}s`n" -ForegroundColor Yellow

$body = $queries | ConvertTo-Json

try {
    $url = "$baseUrl/repositories/$repositoryId/investigate/bulk?delay_between_queries=$delayBetweenQueries"
    
    Write-Host "Sending request to: $url" -ForegroundColor Gray
    
    $response = Invoke-RestMethod `
        -Uri $url `
        -Method POST `
        -Body $body `
        -ContentType "application/json" `
        -TimeoutSec 120
    
    Write-Host "`n[SUCCESS] Request completed!`n" -ForegroundColor Green
    
    # Display summary
    Write-Host "Summary:" -ForegroundColor Cyan
    Write-Host "  Total queries: $($response.summary.total_queries)"
    Write-Host "  Successful: $($response.summary.successful)" -ForegroundColor Green
    Write-Host "  Failed: $($response.summary.failed)" -ForegroundColor $(if ($response.summary.failed -gt 0) { "Red" } else { "Green" })
    
    if ($response.summary.skipped) {
        Write-Host "  Skipped: $($response.summary.skipped)" -ForegroundColor Yellow
    }
    
    if ($response.summary.rate_limit_hit) {
        Write-Host "  Rate Limit Hit: Yes" -ForegroundColor Red
    } else {
        Write-Host "  Rate Limit Hit: No" -ForegroundColor Green
    }
    
    Write-Host "  Total time: $($response.summary.total_execution_time_seconds)s"
    Write-Host "  Configured delay: $($response.metadata.delay_between_queries)s`n"
    
    # Display results
    Write-Host "Results:" -ForegroundColor Cyan
    foreach ($result in $response.results) {
        Write-Host "`n  [$($result.query_index)] $($result.query)" -ForegroundColor Yellow
        
        $statusColor = switch ($result.status) {
            "success" { "Green" }
            "error" { "Red" }
            "skipped" { "Yellow" }
            default { "Gray" }
        }
        
        Write-Host "    Status: $($result.status)" -ForegroundColor $statusColor
        
        if ($result.status -eq "success") {
            Write-Host "    Type: $($result.question_type)"
            Write-Host "    Confidence: $($result.investigation.confidence)"
            Write-Host "    Citations: $($result.investigation.citations.Count)"
            Write-Host "    Time: $($result.execution_time_seconds)s"
        }
        elseif ($result.status -eq "error") {
            Write-Host "    Error Type: $($result.error_type)" -ForegroundColor Red
            Write-Host "    Error: $($result.error)" -ForegroundColor Red
        }
        elseif ($result.status -eq "skipped") {
            Write-Host "    Reason: $($result.error)" -ForegroundColor Yellow
        }
    }
    
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "  Test Complete" -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan
    
} catch {
    Write-Host "`n[FAILED] Request failed!" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    
    if ($_.ErrorDetails) {
        Write-Host "Details: $($_.ErrorDetails.Message)" -ForegroundColor Red
    }
    
    exit 1
}
