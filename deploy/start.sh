#!/usr/bin/env bash
# Container entry point: migrate the database, keep the API running in the
# background, then serve the Streamlit UI on the public port.
set -euo pipefail

cd "$(dirname "$0")/.."

for name in DATABASE_URL GROQ_API_KEY; do
    if [ -z "${!name:-}" ]; then
        echo "ERROR: the $name secret is not set. Add it in the Space settings." >&2
        exit 1
    fi
done

echo "Applying database migrations..."
python -m alembic upgrade head

# Restart the API if it ever exits, so the app recovers without intervention.
(
    while true; do
        python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1 || true
        echo "API exited; restarting in 3 seconds..." >&2
        sleep 3
    done
) &

echo "Waiting for the API to become healthy..."
python - <<'PY'
import time
import urllib.request

deadline = time.time() + 300
while time.time() < deadline:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
            if response.status == 200:
                print("API is healthy.")
                break
    except Exception:
        pass
    time.sleep(2)
else:
    raise SystemExit("API did not become healthy within 5 minutes.")
PY

exec python -m streamlit run frontend/streamlit_app.py \
    --server.port "${PORT:-7860}" \
    --server.address 0.0.0.0
