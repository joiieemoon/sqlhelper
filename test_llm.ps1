Set-Location "C:\Team G\Jainil K task SQLopt\backend"

# Clean JSON payload
$body = '{"query":"SELECT * FROM customers WHERE country=''USA''","agent_version":1,"db_schema":"customers(id INT, name TEXT, country TEXT)","schema_type":"MySQL"}'

# Use Invoke-RestMethod (native PowerShell, no @ issues)
$response = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/agent_optimize" `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ "X-Device-ID" = "test-device-llm-verify" } `
  -Body $body

$response | ConvertTo-Json -Depth 5
