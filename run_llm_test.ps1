Set-Location "C:\Team G\Jainil K task SQLopt\backend"

$pyExe = "C:\Users\jainil_moontechnolab\AppData\Local\Programs\Python\Python312\python.exe"

Write-Host "=== Direct Groq LLM Test ===" -ForegroundColor Cyan
& $pyExe test_direct_llm.py

Write-Host ""
Write-Host "=== Press any key to exit ===" -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")