Set-Location "C:\Team G\Jainil K task SQLopt\backend"
$pyExe = "C:\Users\jainil_moontechnolab\AppData\Local\Programs\Python\Python312\python.exe"

# ── 1. Stop anything already listening / running python ─────────────────
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Write-Host "[1/3] stopped old python processes" -ForegroundColor Cyan

# ── 2. Start uvicorn, logging to a file we can inspect ──────────────────
$logOut = "C:\Team G\Jainil K task SQLopt\backend\throttle_test.log"
$logErr = "C:\Team G\Jainil K task SQLopt\backend\throttle_test_err.log"
Remove-Item $logOut, $logErr -ErrorAction SilentlyContinue

# Drop any stale key inherited from an earlier shell/terminal session so the
# server is guaranteed to load GROQ_API_KEY from backend/.env.
Remove-Item Env:GROQ_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:GROQ_MODEL   -ErrorAction SilentlyContinue

$env:PYTHONPATH = @(
    "C:\Team G\Jainil K task SQLopt\backend",
    "C:\Team G\Jainil K task SQLopt\backend\llm-20260922T070723Z-1-001",
    "C:\Team G\Jainil K task SQLopt\backend\db_less_optimizer-20260922T070953Z-1-001",
    "C:\Team G\Jainil K task SQLopt\backend\validator-20260922T070723Z-1-001",
    "C:\Team G\Jainil K task SQLopt\backend\utils-20260922T070723Z-1-001"
) -join ";"

Start-Process -FilePath $pyExe `
  -ArgumentList "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000" `
  -WorkingDirectory "C:\Team G\Jainil K task SQLopt\backend" `
  -RedirectStandardOutput $logOut `
  -RedirectStandardError  $logErr `
  -WindowStyle Hidden

Start-Sleep -Seconds 8
Write-Host "[2/3] uvicorn started" -ForegroundColor Cyan

# ── 3. Confirm it is up and that .env overrides were picked up ──────────
$h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get
Write-Host "[3/3] health: $($h | ConvertTo-Json -Compress)" -ForegroundColor Green
