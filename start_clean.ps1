Set-Location "C:\Team G\Jainil K task SQLopt\backend"
$pyExe = "C:\Users\jainil_moontechnolab\AppData\Local\Programs\Python\Python312\python.exe"

# Set env vars
$env:GROQ_API_KEY = "#REMOVED_GROQ_KEY"
$env:GROQ_MODEL  = "openai/gpt-oss-120b"

Write-Host "=== Starting uvicorn ===" -ForegroundColor Cyan

$proc = Start-Process -FilePath $pyExe `
  -ArgumentList "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--reload" `
  -NoNewWindow `
  -PassThru
Write-Host "PID: $($proc.Id)"

Start-Sleep -Seconds 6

Write-Host ""
Write-Host "=== Health Check ===" -ForegroundColor Cyan
curl.exe -s http://127.0.0.1:8000/health
Write-Host ""

Write-Host ""
Write-Host "=== agent_optimize Test ===" -ForegroundColor Cyan
$body = '{"query":"SELECT * FROM customers WHERE country=''USA''","agent_version":1,"db_schema":"customers(id INT, name TEXT, country TEXT)","schema_type":"MySQL"}'
$response = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/agent_optimize" `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ "X-Device-ID" = "test-device-final" } `
  -Body $body

$response | ConvertTo-Json -Depth 5