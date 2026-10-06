Set-Location "C:\Team G\Jainil K task SQLopt\backend"

$pyExe = "C:\Users\jainil_moontechnolab\AppData\Local\Programs\Python\Python312\python.exe"

Write-Host "=== Parsing Debug ===" -ForegroundColor Cyan
& $pyExe debug_parse.py

Write-Host ""
Write-Host "=== Press any key to exit ===" -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")