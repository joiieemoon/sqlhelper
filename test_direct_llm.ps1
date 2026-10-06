Set-Location "C:\Team G\Jainil K task SQLopt\backend"
$pyExe = "C:\Users\jainil_moontechnolab\AppData\Local\Programs\Python\Python312\python.exe"

# Quick direct Groq test with the actual llm.py call_llm function
Write-Host "=== Direct Groq call test ===" -ForegroundColor Cyan
& $pyExe -c "
import asyncio, os, sys
sys.path.insert(0, 'llm-20260922T070723Z-1-001')
os.environ['GROQ_API_KEY'] = '#REMOVED_GROQ_KEY'
from llm.llm import call_llm

async def test():
    rsp = await call_llm(
        'Return JSON: {\"test\": true}',
        'You are a helpful assistant. Always respond with valid JSON.'
    )
    print('RAW RESPONSE:')
    print(repr(rsp))
    print()
    print('LENGTH:', len(rsp))

asyncio.run(test())
" 2>&1