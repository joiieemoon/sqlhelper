# ——————————————————————————————————————————————————————————————————————————
# Restart script: install groq, load env, verify server
# ——————————————————————————————————————————————————————————————————————————

Set-Location "C:\Team G\Jainil K task SQLopt\backend"

$pyExe = "C:\Users\jainil_moontechnolab\AppData\Local\Programs\Python\Python312\python.exe"

# 1. Validate the Groq API key
Write-Host "=== Checking Groq API key ===" -ForegroundColor Cyan
& $pyExe check_groq_key.py
Write-Host ""

# 2. Set environment variable for this session
$env:GROQ_API_KEY = "gsk_8e2f9a3b1c7d4e6f5a8b9c2d1e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2"
$env:GROQ_MODEL  = "openai/gpt-oss-120b"
Write-Host "[env] GROQ_API_KEY loaded"
Write-Host ""

# 3. Kill any existing uvicorn and restart
Write-Host "=== Restarting uvicorn ===" -ForegroundColor Cyan
Get-Process -Name "uvicorn" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

Start-Process -FilePath $pyExe `
  -ArgumentList "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--reload" `
  -NoNewWindow `
  -RedirectStandardError "nul" `
  -PassThru | Out-Null

Start-Sleep -Seconds 6

# 4. Health check
Write-Host "=== Health Check ===" -ForegroundColor Cyan
curl.exe -s http://127.0.0.1:8000/health
Write-Host ""

# 5. Test agent_optimize
Write-Host ""
Write-Host "=== Test agent_optimize (JSON mode) ===" -ForegroundColor Cyan
$body = '{"query":"SELECT * FROM customers WHERE country=''USA''","agent_version":1,"db_schema":"customers(id INT, name TEXT, country TEXT)","schema_type":"MySQL"}'
$response = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/agent_optimize" `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ "X-Device-ID" = "test-device-llm-jsonmode" } `
  -Body $body

$response | ConvertTo-Json -Depth 5
