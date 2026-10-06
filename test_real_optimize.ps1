Set-Location "C:\Team G\Jainil K task SQLopt\backend"

Write-Host "=== agent_optimize: Real optimization test ===" -ForegroundColor Cyan
Write-Host ""

# Test 1: Query with schema and scope — should find real issues
Write-Host "--- Test 1: Full optimization (schema + scopes) ---" -ForegroundColor Yellow
$body1 = @{
    query              = "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.status = 'pending' AND o.amount > 1000 ORDER BY o.created_at DESC LIMIT 100"
    agent_version      = 1
    db_schema          = @{
        orders   = @{
            row_count       = 5000000
            avg_row_length = 250
            columns        = @(
                @{ name = "id";         type = "INT";          nullable = $false },
                @{ name = "customer_id"; type = "INT";          nullable = $false },
                @{ name = "status";     type = "VARCHAR(20)";  nullable = $false },
                @{ name = "amount";     type = "DECIMAL(10,2)"; nullable = $false },
                @{ name = "created_at"; type = "DATETIME";     nullable = $false }
            )
            indexes = @(
                @{ name = "PRIMARY";   cols = "id";         unique = $true ; cardinality = 5000000; type = "BTREE" },
                @{ name = "idx_status"; cols = "status";    unique = $false; cardinality = 10;       type = "BTREE" }
            )
        }
        customers = @{
            row_count       = 500000
            avg_row_length = 300
            columns        = @(
                @{ name = "id";   type = "INT";          nullable = $false },
                @{ name = "name"; type = "VARCHAR(100)"; nullable = $false }
            )
            indexes = @(
                @{ name = "PRIMARY"; cols = "id"; unique = $true; cardinality = 500000; type = "BTREE" }
            )
        }
    }
    schema_type        = "MySQL"
    scope              = @("joins", "indexes", "SQL Optimization")
    custom_request     = ""
} | ConvertTo-Json -Depth 5

$response1 = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/agent_optimize" `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ "X-Device-ID" = "test-device-optimized" } `
  -Body $body1

Write-Host "Response:" -ForegroundColor Green
$response1 | ConvertTo-Json -Depth 5
Write-Host ""

# Test 2: Simple query with partial schema
Write-Host "--- Test 2: Simple query with indexes scope ---" -ForegroundColor Yellow
$body2 = @{
    query          = "SELECT * FROM customers WHERE country = 'USA'"
    agent_version  = 1
    db_schema      = @{
        customers = @{
            row_count       = 100000
            avg_row_length = 200
            columns        = @(
                @{ name = "id";      type = "INT";          nullable = $false },
                @{ name = "name";    type = "VARCHAR(100)"; nullable = $false },
                @{ name = "country"; type = "VARCHAR(50)";  nullable = $false }
            )
            indexes = @(
                @{ name = "PRIMARY";     cols = "id";       unique = $true;  cardinality = 100000; type = "BTREE" },
                @{ name = "idx_country"; cols = "country";  unique = $false; cardinality = 50;      type = "BTREE" }
            )
        }
    }
    schema_type    = "MySQL"
    scope          = @("indexes")
    custom_request = ""
} | ConvertTo-Json -Depth 5

$response2 = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/agent_optimize" `
  -Method Post `
  -ContentType "application/json" `
  -Headers @{ "X-Device-ID" = "test-device-simple" } `
  -Body $body2

Write-Host "Response:" -ForegroundColor Green
$response2 | ConvertTo-Json -Depth 5