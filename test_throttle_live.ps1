Set-Location "C:\Team G\Jainil K task SQLopt\backend"

$logOut = "C:\Team G\Jainil K task SQLopt\backend\throttle_test.log"
$respFile = "C:\Team G\Jainil K task SQLopt\backend\throttle_response.json"
Remove-Item $respFile -ErrorAction SilentlyContinue

# Small payload: one table, "indexes" scope only -> keeps token use low so the
# throttle behaviour is visible without burning the whole 8k TPM window at once.
$body = @{
    query          = "SELECT id, name FROM customers WHERE country = 'USA' AND status = 'active'"
    agent_version  = 1
    scope          = @("indexes")
    custom_request = ""
    schema         = @{
        customers = @{
            row_count       = 250000
            avg_row_length  = 180
            columns         = @(
                @{ name = "id";      type = "INT";          nullable = $false },
                @{ name = "name";    type = "VARCHAR(100)"; nullable = $false },
                @{ name = "country"; type = "VARCHAR(50)";  nullable = $false },
                @{ name = "status";  type = "VARCHAR(20)";  nullable = $false }
            )
            indexes = @(
                @{ name = "PRIMARY"; cols = "id"; unique = $true; cardinality = 250000; type = "BTREE" }
            )
        }
    }
    schema_type    = "MySQL"
} | ConvertTo-Json -Depth 6

$body | Set-Content -Path "C:\Team G\Jainil K task SQLopt\backend\throttle_payload.json" -Encoding UTF8

Write-Host "=== Firing /agent_optimize in background ===" -ForegroundColor Cyan
$job = Start-Job -ScriptBlock {
    param($payload, $outFile)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $r = curl.exe -s --max-time 900 `
        -X POST "http://127.0.0.1:8000/agent_optimize" `
        -H "Content-Type: application/json" `
        -H "X-Device-ID: throttle-test-01" `
        -d "@C:\Team G\Jainil K task SQLopt\backend\throttle_payload.json"
    $sw.Stop()
    $r | Set-Content -Path $outFile -Encoding UTF8
    "ELAPSED_SECONDS=$([math]::Round($sw.Elapsed.TotalSeconds,1))"
} -ArgumentList $body, $respFile

# ── Poll the server log so the throttle pacing is visible as it happens ──
Write-Host "=== Monitoring throttle pacing (polling every 15s) ===" -ForegroundColor Cyan
$seen = 0
$deadline = (Get-Date).AddMinutes(16)

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15

    if (Test-Path $logOut) {
        $lines = Get-Content $logOut -ErrorAction SilentlyContinue
        if ($lines.Count -gt $seen) {
            $lines[$seen..($lines.Count - 1)] |
                Where-Object { $_ -match 'TPM throttle|429|RAW RESPONSE|agent_optimize|LLM CALL ERROR|Summary' } |
                ForEach-Object { Write-Host "  $_" }
            $seen = $lines.Count
        }
    }

    if ($job.State -ne 'Running') { break }
}

Write-Host ""
Write-Host "=== Job result ===" -ForegroundColor Green
Receive-Job -Job $job
Remove-Job -Job $job -Force

Write-Host ""
Write-Host "=== Final response ===" -ForegroundColor Green
if (Test-Path $respFile) {
    $raw = Get-Content $respFile -Raw
    try { $raw | ConvertFrom-Json | ConvertTo-Json -Depth 6 } catch { $raw }
} else {
    Write-Host "(no response file written)" -ForegroundColor Red
}