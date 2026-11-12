"""
Keep the Streamlit Community Cloud app awake.

Community Cloud puts an app to sleep after a period without *visitors*.
A plain HTTP ping (UptimeRobot, curl) does not open an app session, so it
does not count as a visit. This script opens the app in a real headless
browser, which does count, and if the app is already asleep it clicks the
"wake up" button and waits until the app has loaded again.

Run by .github/workflows/keep-awake.yml on a schedule. Locally:

    pip install playwright && playwright install chromium
    python deploy/keep_awake.py https://<your-app>.streamlit.app
"""

import os
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

WAKE_BUTTON_TEXT = "get this app back up"
LOAD_TIMEOUT_MS = 240_000  # a cold start (model download + API start) can take a few minutes


def app_loaded(page) -> bool:
    """True once the Streamlit app itself has rendered (it runs inside an iframe on Community Cloud)."""
    for frame in page.frames:
        try:
            if frame.locator('[data-testid="stApp"]').count() > 0:
                return True
        except Exception:
            continue
    return False


def main() -> int:
    url = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("STREAMLIT_APP_URL", "")).strip()
    if not url:
        print("Set the app URL: pass it as an argument or set STREAMLIT_APP_URL.")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        print(f"Opening {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=120_000)

        wake = page.get_by_role("button", name=WAKE_BUTTON_TEXT, exact=False)
        try:
            wake.wait_for(timeout=15_000)
            print("App was asleep; clicking the wake-up button.")
            wake.click()
        except PlaywrightTimeout:
            print("App is awake (no wake-up button).")

        deadline = time.time() + LOAD_TIMEOUT_MS / 1000
        while time.time() < deadline:
            if app_loaded(page):
                print("App loaded.")
                # Stay briefly so the visit registers as an app session.
                page.wait_for_timeout(20_000)
                browser.close()
                return 0
            page.wait_for_timeout(5_000)

        page.screenshot(path="keep-awake-failure.png", full_page=True)
        browser.close()
        print("The app did not finish loading in time (screenshot saved).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
