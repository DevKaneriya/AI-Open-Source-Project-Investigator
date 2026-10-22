"""
Quick 3-query bulk check to verify the answer completeness fixes.
Sends 3 targeted queries that previously showed truncation issues.

Usage:
    python backend/scripts/quick_bulk_check.py
"""
import sys, json, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

QUERIES = [
    # Previously truncated at 500 chars — POST body was invisible
    "Locate CartSyncView and explain its complete GET and POST implementation.",
    # Previously showed 'POST method body is truncated' limitation
    "Trace the checkout flow in CheckoutView including order creation.",
    # Dependency manifests — verifies package.json content
    "What are the exact Angular and Django versions from the package manifests?",
]

URL = "http://127.0.0.1:8000/repositories/1/investigate/bulk?delay_between_queries=3.0"
body = json.dumps(QUERIES).encode()

print("=" * 65)
print("QUICK BULK CHECK — 3 queries")
print("=" * 65)
print()

req = urllib.request.Request(
    URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:300]}")
    sys.exit(1)

results = data.get("results", [])
summary = data.get("summary", {})

print(f"Total time : {summary.get('total_execution_time_seconds', '?')}s")
print(f"Successful : {summary.get('successful', '?')}/3")
print()

for r in results:
    idx  = r.get("query_index")
    q    = r.get("query", "")[:65]
    st   = r.get("status")

    if st != "success":
        print(f"[{idx}] ❌ {st}: {r.get('error','')[:80]}")
        continue

    inv  = r.get("investigation", {})
    ans  = inv.get("answer", "")
    conf = inv.get("confidence")
    cits = len(inv.get("citations", []))
    lims = inv.get("limitations", [])

    # Flag if spurious truncation limitation injected
    spurious = any(
        "incomplete" in l.lower() and "length" in l.lower()
        for l in lims
    )

    print(f"[{idx}] ✅ conf={conf} | cit={cits} | ans={len(ans)}chars | "
          f"{'⚠ SPURIOUS TRUNC LIM' if spurious else 'no false trunc'}")
    print(f"     {q}")

    # Show first 300 chars of answer to verify completeness
    print(f"     Answer excerpt: {ans[:300].replace(chr(10), ' ')}")

    if lims:
        for lim in lims[:2]:
            print(f"     Limitation: {lim[:90]}")
    print()

print("=" * 65)
print("What to look for:")
print("  - conf=partial or high (not insufficient)")
print("  - cit >= 1")
print("  - answer excerpt mentions actual code (not 'not in evidence')")
print("  - no 'SPURIOUS TRUNC LIM' flag")
