import re
import json

def parse_llm_json_debug(response) -> dict:
    print(f"  [parse] Input type: {type(response).__name__}")
    print(f"  [parse] Input repr: {repr(response)}")
    
    if isinstance(response, dict):
        print(f"  [parse] Already a dict, returning")
        return response
    if not response:
        print(f"  [parse] Empty response")
        return {}
    if isinstance(response, str):
        try:
            print(f"  [parse] Attempting json.loads directly...")
            result = json.loads(response)
            print(f"  [parse] SUCCESS: {result}")
            return result
        except Exception as e:
            print(f"  [parse] Direct json.loads failed: {e}")
            try:
                cleaned = re.sub(r"```json|```", "", response).strip()
                print(f"  [parse] After fence strip: {repr(cleaned)}")
                cleaned = re.sub(r'//[^\n]*', '', cleaned)
                cleaned = re.sub(r',\s*}', '}', cleaned)
                cleaned = re.sub(r',\s*]', ']', cleaned)
                cleaned = re.sub(r"\s+", " ", cleaned).strip()
                if cleaned.startswith("{{"):
                    cleaned = cleaned[1:-1]
                print(f"  [parse] After cleanup: {repr(cleaned)}")
                result = json.loads(cleaned)
                print(f"  [parse] SUCCESS after cleanup: {result}")
                return result
            except Exception as e2:
                print(f"  [parse] Cleanup also failed: {e2}")
                return {}
    return {}

# Test with actual LLM responses
print("=== Test 1: Simple JSON ===")
r1 = '{"hello":"world"}'
parse_llm_json_debug(r1)

print("\n=== Test 2: SQL response ===")
r2 = '{"issues":["SELECT * may retrieve unnecessary columns","No index on the country column could cause a full table scan","String comparison may be case‑sensitive depending on collation"],"summary":"The query uses SELECT * and lacks an index on country, which can lead to inefficient data retrieval and full scans; adding a covering index and selecting only needed columns would improve performance."}'
parse_llm_json_debug(r2)

print("\n=== Test 3: What api.py actually gets ===")
# The safe_json_call print shows the raw response, let's see what parse_llm_json gets
test_resp = '{"issues":["SELECT * may retrieve unnecessary columns"],"summary":"test"}'
print(f"  json.loads(test_resp) = {json.loads(test_resp)}")