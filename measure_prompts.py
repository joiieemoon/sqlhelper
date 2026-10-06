"""Measure the token size of each pipeline stage's system prompt."""
import re
import os

LLM_DIR = os.path.join(os.path.dirname(__file__), "llm-20260922T070723Z-1-001", "llm")
SCOPE_FILE = os.path.join(LLM_DIR, "prompt_by_scope.py")


def extract_system_prompt(path):
    """Pull the first triple-quoted block assigned to system_prompt."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'system_prompt\s*=\s*"""(.*?)"""', src, re.DOTALL)
    return m.group(1) if m else ""


def estimate_tokens(text):
    """Rough estimate: 1 token ~= 4 chars for English text."""
    return len(text) // 4


print("=" * 70)
print("SYSTEM PROMPT TOKEN ESTIMATES (chars/4 heuristic)")
print("=" * 70)

total = 0
for name in ("issue_finder.py", "planner.py", "rewriter.py"):
    path = os.path.join(LLM_DIR, name)
    sp = extract_system_prompt(path)
    toks = estimate_tokens(sp)
    total += toks
    print(f"{name:20s}  chars={len(sp):>7,}  ~tokens={toks:>6,}")

print("-" * 70)
print(f"{'TOTAL (3 stages)':20s}  {'':>13}  ~tokens={total:>6,}")
print()
print(f"Groq free-tier TPM limit for openai/gpt-oss-120b: 8,000")
print(f"Total pipeline demand:  ~{total:,} tokens  ->  {total / 8000:.1f}x over the limit")
print()
print("=" * 70)
print("DB-LESS OPTIMIZER PROMPTS")
print("=" * 70)
dl_dir = os.path.join(os.path.dirname(__file__), "db_less_optimizer-20260922T070953Z-1-001", "db_less_optimizer")
for name in ("planner.py", "rewriter.py"):
    p = os.path.join(dl_dir, name)
    if os.path.exists(p):
        sp = extract_system_prompt(p)
        print(f"{name:20s}  chars={len(sp):>7,}  ~tokens={estimate_tokens(sp):>6,}")