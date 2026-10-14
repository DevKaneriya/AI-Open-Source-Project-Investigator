# Test script for bulk investigation endpoint

$baseUrl = "http://127.0.0.1:8000"
$repositoryId = 1

# Test queries
$queries = @(
    "Where is CartSyncView defined?",
    "How does cart synchronization work?",
    "What are the main dependencies?"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Bulk Investigation Test" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Repository ID: $repositoryId" -ForegroundColor Yellow
Write-Host "Queries:" -ForegroundColor Yellow
for ($i = 0; $i -lt $queries.Length; $i++) {
    Write-Host "  $($i + 1). $($queries[$i])" -ForegroundColor Gray
}
Write-Host ""

# Build request body - just the array of queries
$body = $queries | ConvertTo-Json

Write-Host "Sending bulk request..." -ForegroundColor Green

try {
    $response = Invoke-RestMethod `
        -Uri "$baseUrl/repositories/$repositoryId/investigate/bulk" `
        -Method POST `
        -Body $body `
        -ContentType "application/json" `
        -TimeoutSec 180
    
    Write-Host "[SUCCESS] Request completed!`n" -ForegroundColor Green
    
    # Display summary
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Summary" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "Total queries: $($response.summary.total_queries)"
    Write-Host "Successful: $($response.summary.successful)" -ForegroundColor Green
    Write-Host "Failed: $($response.summary.failed)"
    Write-Host "Total time: $($response.summary.total_execution_time_seconds)s`n"
    
    # Display each result
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Results" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    
    foreach ($result in $response.results) {
        Write-Host "`n[$($result.query_index)] $($result.query)" -ForegroundColor Yellow
        Write-Host "  Status: $($result.status)"
        
        if ($result.status -eq "success") {
            Write-Host "  Question Type: $($result.question_type)"
            Write-Host "  Confidence: $($result.investigation.confidence)"
            Write-Host "  Citations: $($result.investigation.citations.Count)"
            Write-Host "  Answer Length: $($result.investigation.answer.Length) chars"
            Write-Host "  Execution Time: $($result.execution_time_seconds)s"
            
            # Show first 150 chars of answer
            $answerPreview = $result.investigation.answer.Substring(0, [Math]::Min(150, $result.investigation.answer.Length))
            Write-Host "  Answer: $answerPreview..." -ForegroundColor Gray
        }
        else {
            Write-Host "  Error: $($result.error)" -ForegroundColor Red
        }
    }
    
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "  Test Complete" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    
} catch {
    Write-Host "[FAILED] Request failed!" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
