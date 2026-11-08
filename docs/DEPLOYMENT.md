# Deploying the public demo

The app runs for free with no upkeep on three services:

| Part | Service | Free tier |
|---|---|---|
| Web UI and API (one Docker container) | Hugging Face Spaces | 2 vCPU, 16 GB RAM. Sleeps after 48 hours without traffic |
| Database and vectors (PostgreSQL + pgvector) | Neon | 0.5 GB. Pauses when idle and resumes automatically on the next query |
| Keep-awake ping every 5 minutes | UptimeRobot | 50 monitors |

The LLM is Groq's free API, as in local development.

## How it stays maintenance-free

- **Nothing important lives on the container's disk.** The code index and the vectors live in Neon (`VECTOR_BACKEND=pgvector`), so restarts lose nothing. Cloned checkouts are re-downloaded automatically the first time a repository is used after a restart.
- **The API restarts itself.** `deploy/start.sh` runs it in a loop, so a crash recovers within seconds.
- **Visitors can't break it.** `DEMO_MODE=true`: visitors can only ask questions about the repositories you've indexed. Adding, re-indexing and deleting repositories requires the admin password (sidebar → *Owner access*).
- **The Groq quota is protected.** Each visitor gets 8 questions per hour, and the whole demo gets 40 per hour. Change these with the `DEMO_SESSION_QUERIES` and `DEMO_QUERIES_PER_HOUR` Space variables.
- **No cold-start wait.** UptimeRobot requests the app every 5 minutes, so the Space never goes idle long enough to sleep.

## One-time setup

### 1. Create the database (Neon)
1. Sign up at <https://neon.tech> and create a project (any region; pick one close to you).
2. On the project dashboard, open **Connect**, turn **Connection pooling off**, and copy the connection string. It looks like `postgresql://user:password@ep-xxx.region.aws.neon.tech/neondb?sslmode=require`.

You don't need to create any tables. The app sets up the schema and enables pgvector when it starts.

### 2. Get a Hugging Face token
1. Sign up at <https://huggingface.co>.
2. Go to **Settings → Access Tokens → Create new token**, choose the **Write** type, and copy it.

### 3. Deploy
From the project folder in PowerShell:

```powershell
$env:HF_TOKEN = "hf_..."                       # token from step 2
.\deploy\deploy_to_huggingface.ps1 -DatabaseUrl "postgresql://...neon.tech/neondb?sslmode=require"
```

The script:
- creates the Space
- stores `DATABASE_URL`, `GROQ_API_KEY` (read from your `.env`) and a generated `ADMIN_PASSWORD` as Space secrets
- uploads the project, including uncommitted changes

It prints the public link and the admin password. **Save the password.** Pass `-AdminPassword "..."` if you'd rather choose your own.

The first build takes about 5–10 minutes. Watch it on the Space page.

### 4. Publish your showcase repositories
1. Open the public link, then in the sidebar open **Owner access** and enter the admin password.
2. On **Repositories**, add each repository you want to show and wait for it to reach **Ready**.
3. Click **Lock admin mode**. Visitors now see only the ready repositories.

### 5. Keep it awake (UptimeRobot)
1. Sign up at <https://uptimerobot.com> (free plan).
2. Go to **Integrations & API → API**, create the **Main API key**, and copy it.
3. Run:

```powershell
$env:UPTIMEROBOT_API_KEY = "u..."
.\deploy\setup_uptime_monitor.ps1 -Owner <your-hugging-face-username>
```

That's it. Put `https://<username>-opensource-investigator.hf.space` in your portfolio.

## Redeploying after changes

Run `.\deploy\deploy_to_huggingface.ps1 -SkipSecrets`. The Space rebuilds, and your indexed repositories are kept because they live in Neon.

## Running the hosted configuration locally

```powershell
docker build -t opensource-investigator .
docker run -p 7860:7860 -e DATABASE_URL="<neon url>" -e GROQ_API_KEY="<key>" -e ADMIN_PASSWORD="<pw>" opensource-investigator
```

Then open <http://localhost:7860>. Local development without Docker is unchanged: it uses Qdrant and has no demo restrictions.
