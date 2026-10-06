$pp = "c:\Team G\Jainil K task SQLopt\backend"
$py = "C:\Users\jainil_moontechnolab\AppData\Local\Programs\Python\Python312\python.exe"
$env:PYTHONPATH = @(
  "c:\Team G\Jainil K task SQLopt\backend",
  "c:\Team G\Jainil K task SQLopt\backend\llm-20260922T070723Z-1-001",
  "c:\Team G\Jainil K task SQLopt\backend\db_less_optimizer-20260922T070953Z-1-001",
  "c:\Team G\Jainil K task SQLopt\backend\validator-20260922T070723Z-1-001",
  "c:\Team G\Jainil K task SQLopt\backend\utils-20260922T070723Z-1-001"
) -join ";"
Set-Location $pp
Start-Process -FilePath $py -ArgumentList "-m","uvicorn","api:app","--host","127.0.0.1","--port","8000" -WorkingDirectory $pp -WindowStyle Minimized
Start-Sleep -Seconds 12
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -Method Get -TimeoutSec 15 | ConvertTo-Json -Depth 4
