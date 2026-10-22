"""
Run the 10 bulk regression queries against the live server and print results.

Usage:
    python backend/scripts/run_regression.py [--repo-id 1] [--delay 3.0]
"""
import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

REGRESSION_QUERIES = [
    # 1 – checkout end-to-end
    "Trace the checkout flow end-to-end from the Angular UI to the Django backend, "
    "including order creation and payment handling.",
    # 2 – cart lifecycle
    "Explain how cart items are added, updated, removed, and synchronized between "
    "the frontend and backend.",
    # 3 – dependency versions
    "Identify the exact Angular and Django versions and their key dependencies "
    "from the actual package manifests.",
    # 4 – Celery / email
    "Trace the background email and Celery task flow from task definition to "
    "invocation and worker configuration.",
    # 5 – CartSyncView
    "Locate CartSyncView and explain its complete GET and POST implementation.",
    # 6 – nonexistent symbol
    "Search for AutoRefundAllFailedPayments and determine whether its absence "
    "can be confirmed across the whole repository.",
    # 7 – database config
    "Determine the application's effective SQLite or PostgreSQL database "
    "configuration from the active settings module.",
    # 8 – Razorpay
    "Explain Razorpay payment verification and webhook behavior based on "
    "the actual executable code.",
    # 9 – definitely nonexistent
    "Search for DefinitelyNonexistentFunctionForTesting and assess whether "
    "repository-wide absence is justified.",
    # 10 – citation audit
    "Audit the answers about checkout and verify whether their citations and "
    "claim support are valid.",
]


def run(repo_id: int = 1, delay: float = 3.0, base_url: str = "http://127.0.0.1:8000") -> None:
    url = f"{base_url}/repositories/{repo_id}/investigate/bulk?delay_between_queries={delay}"
    body = json.dumps(REGRESSION_QUERIES).encode()

    print("=" * 70)
    print(f"REGRESSION RUN — {len(REGRESSION_QUERIES)} queries, delay={delay}s")
    print(f"Endpoint: {url}")
    print("=" * 70)
    t0 = time.time()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP error {e.code}: {e.read().decode()[:400]}")
        return
    except Exception as e:
        print(f"Request failed: {e}")
        return

    elapsed = time.time() - t0
    summary = data.get("summary", {})
    results = data.get("results", [])

    print(f"\nTotal wall time  : {elapsed:.1f}s")
    print(f"Server reports   : {summary.get('total_execution_time_seconds', '?')}s")
    print(f"Successful        : {summary.get('successful', '?')}")
    print(f"Failed            : {summary.get('failed', '?')}")
    print(f"Skipped           : {summary.get('skipped', '?')}")
    print(f"Rate limit hit    : {summary.get('rate_limit_hit', '?')}")
    avg = summary.get("avg_stage_times", {})
    if avg:
        print(f"Avg retrieval     : {avg.get('retrieval_seconds', '?')}s")
        print(f"Avg answer gen    : {avg.get('answer_generation_seconds', '?')}s")

    print()
    print("-" * 70)
    print("PER-QUERY RESULTS")
    print("-" * 70)

    for r in results:
        idx = r.get("query_index", "?")
        status = r.get("status", "?")
        qt = r.get("question_type", "?")
        t = r.get("execution_time_seconds", "?")
        q = r.get("query", "")[:70]

        if status == "success":
            inv = r.get("investigation", {})
            conf = inv.get("confidence", "?")
            n_cit = len(inv.get("citations", []))
            n_lim = len(inv.get("limitations", []))
            fallback = r.get("evidence_summary", {}).get("used_fallback_analysis", False)
            fb_tag = " [FALLBACK]" if fallback else ""
            print(f"[{idx}] ✅ {status} | {qt}{fb_tag} | conf={conf} | cit={n_cit} | lim={n_lim} | {t}s")
            print(f"     {q}")
            if inv.get("limitations"):
                for lim in inv["limitations"][:2]:
                    print(f"     ⚠  {lim[:100]}")
        else:
            err = r.get("error", "")[:100]
            etype = r.get("error_type", "?")
            print(f"[{idx}] ❌ {status} | {etype} | {t}s")
            print(f"     {q}")
            print(f"     Error: {err}")

    print()
    print("=" * 70)
    print("DONE")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--repo-id", type=int, default=1)
    p.add_argument("--delay", type=float, default=3.0)
    p.add_argument("--url", default="http://127.0.0.1:8000")
    args = p.parse_args()
    run(args.repo_id, args.delay, args.url)
