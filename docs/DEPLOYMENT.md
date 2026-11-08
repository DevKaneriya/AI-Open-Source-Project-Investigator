# Deploying the public demo

The app runs for free, with no credit card, on two services:

| Part | Service | Free tier |
|---|---|---|
| Web UI and API | Streamlit Community Cloud | Deploys from a public GitHub repo. Sleeps after 12 hours without visitors |
| Database and vectors (PostgreSQL + pgvector) | Neon | 0.5 GB. Pauses when idle and resumes automatically on the next query |

The LLM is Groq's free API, as in local development.

> Hugging Face Spaces was the original host, but Docker Spaces now require a PRO subscription. The Docker setup (`Dockerfile`, `deploy/*.ps1`) still works if you subscribe; see [Hugging Face (paid)](#alternative-hugging-face-spaces-paid) below.

## How it works

- **One app runs both parts.** Community Cloud runs only a Streamlit script, so the root [`streamlit_app.py`](../streamlit_app.py) runs the database migrations, starts the FastAPI backend in the background and then shows the normal UI. If the API crashes, the next page load starts it again.
- **Nothing important lives on the app's disk.** The code index and the vectors live in Neon (`VECTOR_BACKEND=pgvector`), so restarts lose nothing. Cloned checkouts are downloaded again the first time a repository is used after a restart.
- **Visitors can't break it.** Demo mode is on: visitors can only ask questions about the repositories you've indexed. Adding, re-indexing and deleting repositories requires the admin password (sidebar → *Owner access*).
- **The Groq quota is protected.** Each visitor gets 8 questions per hour, and the whole demo gets 40 per hour. Change these with the `DEMO_SESSION_QUERIES` and `DEMO_QUERIES_PER_HOUR` secrets.
- **Sleeping.** After 12 hours without visitors the app sleeps. The next visitor sees a "wake up" button, and the app is back within a couple of minutes.

## One-time setup

### 1. Create the database (Neon)
1. Sign up at <https://neon.tech> and create a project (any region; pick one close to you).
2. On the project dashboard, open **Connect**, turn **Connection pooling off**, and copy the connection string. It looks like `postgresql://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`.

You don't need to create any tables. The app sets up the schema and enables pgvector when it starts.

### 2. Push the code to GitHub
Community Cloud deploys from GitHub, so commit and push your latest changes. The repository must be public, unless you use your one free private app.

### 3. Create the app (Streamlit Community Cloud)
1. Sign in at <https://share.streamlit.io> with your GitHub account.
2. Click **Create app → Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** your repository
   - **Branch:** `main`
   - **Main file path:** `streamlit_app.py` (the root file, not `frontend/streamlit_app.py`)
   - **App URL:** pick a name, for example `opensource-investigator`
4. Open **Advanced settings**:
   - **Python version:** `3.12`. The CPU-only torch build in `requirements.txt` is pinned for 3.12.
   - **Secrets:** paste this, with your own values:

     ```toml
     DATABASE_URL = "postgresql://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require"
     GROQ_API_KEY = "gsk_..."
     ADMIN_PASSWORD = "choose-a-long-password"
     ```
5. Click **Deploy**.

The first build takes about 5–10 minutes. The first page load after that also downloads the embedding model and starts the API, which takes another minute or two. Later loads are fast.

Secrets are stored only in the Streamlit dashboard. Never commit them; `.streamlit/secrets.toml` is in `.gitignore`.

### 4. Publish your showcase repositories
1. Open the app, then in the sidebar open **Owner access** and enter the admin password.
2. On **Repositories**, add each repository you want to show and wait for it to reach **Ready**.
3. Click **Lock admin mode**. Visitors now see only the ready repositories.

Put the app URL (`https://<your-name>.streamlit.app`) in your portfolio.

## Redeploying after changes

Push to `main`. Community Cloud picks up the change and restarts the app. Your indexed repositories are kept because they live in Neon.

To change a secret, open the app's **Settings → Secrets**, edit it and save. The app restarts with the new value.

## Troubleshooting

- **"Missing secrets"**: add the named secret under **Settings → Secrets**.
- **"Database migration failed"**: check that `DATABASE_URL` is the Neon string with connection pooling off.
- **The app restarts or shows "over its resource limits"**: the free tier has limited memory. Index smaller repositories, or index one at a time.
- Full logs: in the app, open **Manage app** (bottom right).

## Alternative: Hugging Face Spaces (paid)

With a Hugging Face PRO subscription, you can deploy the Docker image instead:

```powershell
$env:HF_TOKEN = "hf_..."   # a Write token from Settings → Access Tokens
.\deploy\deploy_to_huggingface.ps1 -DatabaseUrl "postgresql://...neon.tech/neondb?sslmode=require"
```

The script creates the Space, stores `DATABASE_URL`, `GROQ_API_KEY` (from your `.env`) and a generated `ADMIN_PASSWORD` as Space secrets, and prints the link and password. Redeploy with `-SkipSecrets`. `deploy/setup_uptime_monitor.ps1` adds an UptimeRobot ping that keeps the Space awake.

## Running the hosted configuration locally

With Docker:

```powershell
docker build -t opensource-investigator .
docker run -p 7860:7860 -e DATABASE_URL="<neon url>" -e GROQ_API_KEY="<key>" -e ADMIN_PASSWORD="<pw>" opensource-investigator
```

Then open <http://localhost:7860>. Local development without Docker is unchanged: it uses Qdrant and has no demo restrictions.
