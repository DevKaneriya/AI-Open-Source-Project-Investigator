# AI Open-Source Project Investigator

An agentic code-analysis system that understands repository architecture, traces feature flows, and answers technical questions with evidence grounded directly in source code.

---

## What It Does

Point the system at any public GitHub repository. After indexing, ask natural-language questions and get answers backed by cited source snippets, not guesses.

**Example questions:**
- *"How does the checkout flow work from the Angular UI to the Django backend?"*
- *"Locate CartSyncView and explain its GET and POST implementation."*
- *"What are the exact Angular and Django versions?"*
- *"How is Razorpay payment verification implemented?"*

**Highlights:**
- **Web UI (Streamlit):** add a repository and it's cloned and indexed in one step, then ask questions and inspect every cited snippet.
- **Agentic workflow (LangGraph):** retrieves evidence, and runs a targeted follow-up round if something is missing, then verifies every citation against the real code and repairs bad ones.
- **Clear failures:** any error in the UI opens a pop-up explaining what failed, what to try, and (for the owner) the full technical details, with an error ID that matches the server logs.
- **Free public demo mode:** visitors can only ask questions, with rate limits; repository management is password-protected. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web UI | Streamlit |
| API | FastAPI (Python 3.12) |
| Agent workflow | LangGraph |
| Database | PostgreSQL + SQLAlchemy 2.x |
| Migrations | Alembic |
| Vector store | Qdrant (local file mode) locally, or pgvector in PostgreSQL when hosted |
| Embeddings | `BAAI/bge-small-en-v1.5` (sentence-transformers) |
| LLM | Groq API (configurable model) |
| Parsing | Python AST + Tree-sitter (JavaScript/TypeScript) |
| Hosting | Streamlit Community Cloud + Neon (free) |

---

## Project Structure

```
AI-OpenSource-Project-Investigator/
├── backend/
│   ├── alembic/                    # Database migrations
│   │   ├── env.py
│   │   └── versions/
│   ├── app/
│   │   ├── main.py                 # FastAPI app + all endpoints
│   │   ├── core/
│   │   │   ├── config.py           # Pydantic settings (reads .env)
│   │   │   └── database.py         # SQLAlchemy engine + session
│   │   ├── models/                 # SQLAlchemy ORM models + response schemas
│   │   │   ├── repository.py
│   │   │   ├── repository_file.py
│   │   │   ├── code_symbol.py
│   │   │   ├── code_chunk.py
│   │   │   ├── code_call.py
│   │   │   ├── code_import.py
│   │   │   ├── query_analysis.py         # Question types + LLM output schema
│   │   │   └── investigation_answer.py   # Investigation response schema
│   │   └── services/
│   │       ├── investigation_graph.py             # LangGraph workflow (orchestrator)
│   │       ├── repository_investigation_service.py
│   │       ├── query_analyzer_service.py          # LLM classification + fallback
│   │       ├── retrieval_service.py               # Runs all searches for a round
│   │       ├── evidence_collector_service.py      # Merge, dedupe, rank evidence
│   │       ├── evidence_evaluation_service.py     # Is the evidence sufficient?
│   │       ├── evidence_signals.py
│   │       ├── answer_generation_service.py       # Evidence-grounded answers
│   │       ├── citation_validation_service.py     # Checks citations against code
│   │       ├── llm_service.py                     # Groq client
│   │       ├── architectural_file_service.py      # Finds manifests, entry points, config
│   │       ├── code_search_service.py             # Symbol search
│   │       ├── repository_search_service.py       # Full-text search
│   │       ├── import_search_service.py           # Import search
│   │       ├── semantic_search_service.py         # Vector search
│   │       ├── code_relationship_service.py       # Call graph queries
│   │       ├── code_retrieval_service.py          # Source extraction
│   │       ├── code_indexer.py                    # Parse + index symbols
│   │       ├── code_analyzer.py                   # Python AST
│   │       ├── tree_sitter_analyzer.py            # JavaScript/TypeScript
│   │       ├── code_chunk_service.py              # Chunking
│   │       ├── embedding_service.py               # sentence-transformers
│   │       ├── vector_service.py                  # Qdrant or pgvector, chosen by VECTOR_BACKEND
│   │       ├── pgvector_service.py                # pgvector implementation
│   │       ├── vector_indexing_service.py         # Embed + store chunks
│   │       ├── github_service.py                  # Clone repositories
│   │       ├── repository_workspace.py            # Re-downloads missing checkouts
│   │       ├── repository_scanner.py              # File discovery
│   │       ├── repository_service.py              # Repository CRUD + indexing stage
│   │       ├── language_service.py                # Extension → language
│   │       ├── file_filter_service.py             # Exclusion rules
│   │       └── search_normalizer.py               # Query normalisation
│   ├── scripts/
│   │   ├── reindex.py              # Re-index a repository
│   │   ├── run_regression.py       # Run 10 benchmark queries
│   │   ├── verify_schema.py        # Check DB matches models
│   │   ├── apply_migrations.ps1
│   │   ├── create_migration.ps1
│   │   └── test_migrations.ps1
│   └── tests/                      # pytest suite (13 files)
├── frontend/
│   ├── streamlit_app.py            # UI entry point (local development)
│   ├── api_client.py               # HTTP client for the FastAPI backend
│   ├── components/                 # Pages: investigate, repositories, guide, status, sidebar
│   ├── utils/                      # Error pop-ups, demo access, pipeline, history, navigation
│   ├── assets/styles.css
│   └── requirements.txt            # UI-only dependencies
├── .streamlit/config.toml          # UI theme and server settings
├── streamlit_app.py                # Hosted entry point (Streamlit Community Cloud)
├── deploy/                         # Docker/Hugging Face scripts + pinned versions
│   └── constraints.txt             # Exact tested package versions
├── docs/DEPLOYMENT.md              # Free hosting guide
├── Dockerfile                      # Single-container image (API + UI)
├── data/qdrant/                    # Local Qdrant storage (not committed)
├── alembic.ini
├── requirements.txt                # All dependencies (API, UI, tests)
├── start_server.ps1
├── start_server_dev.ps1
├── stop_server.ps1
└── TROUBLESHOOTING.md
```

---

## Quick Start

### 1. Prerequisites

- Python 3.12
- PostgreSQL running locally
- Groq API key (free at [console.groq.com](https://console.groq.com))

### 2. Install Dependencies

Create the virtual environment in `.venv` (the start scripts expect that folder), then install everything at the tested versions:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -c deploy/constraints.txt
```

`requirements.txt` covers the API, the web UI and the tests.

### 3. Configure Environment

Copy `.env.example` to `.env` and fill in values:

```bash
APP_ENV=development

DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/ai_opensource_investigator

GROQ_API_KEY=your_groq_api_key_here
QUERY_ANALYZER_MODEL=llama-3.3-70b-versatile
```

> Qdrant runs in local file mode, so no server is needed. Data is stored in `data/qdrant/`.

### 4. Set Up Database

```powershell
# Create the PostgreSQL database first, then:
.venv\Scripts\alembic.exe upgrade head
```

### 5. Start the API

```powershell
# Development (with auto-reload)
.\start_server_dev.ps1

# Production
.\start_server.ps1
```

The API runs at `http://localhost:8000`, with interactive docs at `http://localhost:8000/docs`.

### 6. Start the Web UI

In a second terminal, from the project folder:

```powershell
.venv\Scripts\Activate.ps1
streamlit run frontend/streamlit_app.py --server.port 8501
```

Open `http://localhost:8501`. On first visit the **Getting started** page shows a live setup checklist. Add a repository on the **Repositories** page and it's cloned and indexed in one step. Once it shows **Ready**, ask questions on **Investigate** (press **Ctrl+Enter** or click **Investigate** to submit).

If something fails, a pop-up explains what went wrong and what to try. Locally you always see the full technical details.

---

## Deployment

The public demo runs for free, with no credit card, on **Streamlit Community Cloud** (UI + API in one app) and **Neon** (PostgreSQL + pgvector). Pushing to `main` redeploys automatically.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the step-by-step guide, including secrets, demo mode and a paid Hugging Face alternative.

---

## Indexing a Repository

The web UI runs all of these steps for you. To drive the API directly, run these four steps in order. Each step depends on the previous one, and each records its stage on the repository (`cloned` → `parsed` → `chunked` → `indexed`). `GET /repositories` returns that `stage` along with `file_count`, `chunk_count` and `vector_count`.

```bash
# 1. Clone the repository
POST /repositories
Body: {"repository_url": "https://github.com/user/repo"}

# 2. Parse and index symbols
POST /repositories/{id}/index

# 3. Create semantic chunks
POST /repositories/{id}/chunks

# 4. Generate embeddings and store vectors (repeat until "done": true)
POST /repositories/{id}/vector-index?time_budget_seconds=60
```

Step 4 is resumable. Each call embeds for about `time_budget_seconds` (default 60), skips chunks that already have vectors, and returns `vectors_total`, `chunks_total`, `remaining` and `done`. Repeat the call until `done` is true. The web UI does this automatically and shows progress, so large repositories never hit request timeouts, and an interrupted run continues where it stopped.

For a medium-sized repository (~150 files):
- Steps 1–3: ~30 seconds
- Step 4: ~60 seconds

To re-index after a code change to the investigator itself:

```powershell
python backend/scripts/reindex.py --repo-id 1
# Skip the slow embedding step if only symbol data changed:
python backend/scripts/reindex.py --repo-id 1 --skip-vectors
```

---

## API Reference

### Investigation

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/repositories/{id}/investigate?query=...` | Single evidence-grounded investigation (LangGraph workflow) |
| `POST` | `/repositories/{id}/investigate/bulk` | Up to 10 queries in one request |

**Single investigation:**
```bash
GET /repositories/1/investigate?query=How does the checkout flow work?
```

**Bulk investigation** (`delay_between_queries` defaults to 2.0 seconds):
```bash
POST /repositories/1/investigate/bulk?delay_between_queries=3.0
Body: [
  "Trace the checkout flow end-to-end.",
  "Explain how cart items are synchronized.",
  "What are the Angular and Django versions?"
]
```

The bulk response includes per-query `stage_times` (retrieval, answer generation), a summary with `avg_stage_times`, and `rate_limit_hit`.

### Repositories and Indexing

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/repositories` | List repositories with stage and counts |
| `POST` | `/repositories` | Clone a repository |
| `DELETE` | `/repositories/{id}` | Delete a repository and its index, chunks and vectors |
| `POST` | `/repositories/{id}/index` | Parse and index symbols |
| `POST` | `/repositories/{id}/chunks` | Create semantic chunks |
| `POST` | `/repositories/{id}/vector-index` | Embed chunks and store vectors |
| `POST` | `/repositories/scan` | Quick architecture scan of a URL without indexing |

### Search and Debugging

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/repositories/{id}/retrieve?query=...&limit=10` | Raw ranked evidence, before answer generation |
| `GET` | `/repositories/{id}/search` | Symbol search |
| `GET` | `/repositories/{id}/search-text` | Full-text search |
| `GET` | `/repositories/{id}/semantic-search?query=...&limit=10` | Vector similarity search |
| `GET` | `/repositories/{id}/relationships/calls` | Call graph for a symbol |
| `GET` | `/repositories/{id}/source` | Source extraction by file and line range |
| `GET` | `/query-analyze` | Test query classification |
| `GET` | `/health` | Health check |

### Errors

Endpoints report expected problems (for example "Repository not found") as HTTP errors with a `detail` message. Any unexpected server error returns HTTP 500 with JSON like:

```json
{"detail": "ValueError: ...", "error_type": "ValueError", "error_id": "7c884b3a", "path": "/repositories/1/index"}
```

The same `error_id` is written to the server log with the full traceback.

---

## How an Investigation Works

Every question runs through a LangGraph state machine with bounded loops:

```
User question
    │
    ▼
analyze_query ─────── LLM classifies the question type and extracts search terms
    │                 (falls back to deterministic extraction if the LLM fails)
    ▼
plan_retrieval ◄──────────────────────────────┐
    │                                         │
    ▼                                         │
retrieve_evidence ─── symbol, full-text,      │
    │                 import, semantic and    │
    │                 call-graph searches     │
    ▼                                         │
collect_rank_evidence ─ merge, dedupe, score, │
    │                   keep the top 12       │
    ▼                                         │
evaluate_evidence ── not enough evidence? ────┘  (at most 1 follow-up round)
    │
    ▼
generate_answer ───── evidence-grounded answer with [E#] citations
    │
    ▼
validate_citations ── every citation checked against the actual code
    │
    ├── problems found → repair_answer (at most 1 attempt)
    ▼
finalize_response ─── answer, citations, confidence, limitations, diagnostics
```

### Question Types

| Type | Example | Strategy |
|---|---|---|
| `symbol_lookup` | "Where is CartSyncView defined?" | Symbol search |
| `call_site_lookup` | "Where is addToCart called?" | Symbol + relationships |
| `file_explanation` | "What does views.py do?" | Source + semantic |
| `feature_flow` | "How does checkout work?" | All sources + follow calls |
| `architectural_overview` | "Explain this project" | Architectural files first |
| `end_to_end_flow` | "Trace a request frontend to DB" | All sources + follow calls |
| `cross_cutting_concern` | "How is caching implemented?" | Config + all sources |
| `dependency_analysis` | "What Angular version is used?" | Manifests first |

### Evidence Scoring

```
Base scores:
  source_context  → 1.00   actual retrieved source
  symbol          → 0.98   exact symbol match
  text_match      → 0.82   grep match
  import          → 0.68   import statement
  relationship    → 0.66   call graph entry
  semantic        → 0.35–0.70  scaled by vector similarity

Architectural bonuses (added on top):
  package manifests   → +0.45  (dependency questions)
  README              → +0.30  (architectural questions)
  entry points        → +0.25
  routes files        → +0.20
  models/config       → +0.15
```

---

## Evidence-Grounded Answers

Answers are generated by an LLM constrained to the retrieved evidence. The system:

- Cites every claim with evidence IDs (E1, E2, …)
- Refuses to invent code, functions, or side effects not in evidence
- Distinguishes confirmed code from docstring intentions
- Verifies citations against the source and repairs mismatches
- Reports `confidence: high / partial / insufficient`
- Lists specific limitations when evidence is incomplete

### Answer Schema

```json
{
  "question": "...",
  "answer": "CartSyncView [E1] provides two endpoints...",
  "citations": [
    {
      "evidence_id": "E1",
      "file": "backend/accounts/views.py",
      "start_line": 292,
      "end_line": 331,
      "symbol": "CartSyncView",
      "snippet": "class CartSyncView(APIView):\n    ...",
      "relevance": "Defines the class being described"
    }
  ],
  "confidence": "partial",
  "limitations": [
    "POST method body is truncated after the for-loop start"
  ],
  "execution_time_seconds": 26.2
}
```

The full response also includes `metadata` with workflow diagnostics (retrieval rounds, citation repairs, stage timings), shown in the UI under **Investigation diagnostics**.

---

## Query Analysis with Fallback

The query analyzer uses a compact JSON schema to stay well within the LLM token budget. On any LLM failure, a deterministic fallback activates:

- Extracts CamelCase/snake_case identifiers → `symbol_queries`
- Strips stopwords → `text_queries`
- Classifies the type from keywords (no LLM needed)
- The investigation continues; it never aborts on a query analysis failure

When the fallback was used, the analysis `intent` starts with `[fallback]`.

---

## Supported File Types

Symbols (functions, classes, imports, calls) are extracted from:

| Extension | Language | Symbol Extraction |
|---|---|---|
| `.py` | Python | Python AST (functions, classes, decorators, calls) |
| `.js`, `.jsx`, `.mjs`, `.cjs` | JavaScript | Tree-sitter |
| `.ts`, `.tsx` | TypeScript | Tree-sitter |

These files are indexed for content only, with no symbol extraction. Full-text and semantic search still work on them:

| Extension | Type |
|---|---|
| `.java`, `.go`, `.cs`, `.cpp`, `.cc`, `.c`, `.rs`, `.php`, `.rb` | Other source code |
| `.json`, `.jsonc` | Dependency manifests (package.json, etc.) |
| `.txt`, `.env` | Plain text (requirements.txt, etc.) |
| `.toml`, `.yaml`, `.yml` | Config files |
| `.sql` | Schemas and migrations |
| `.md`, `.mdx`, `.rst` | Documentation (chunked by heading) |

**Excluded automatically:** `node_modules/`, `.venv/`, `dist/`, `build/`, lock files (`package-lock.json`, `yarn.lock`), compiled files (`.pyc`, `.min.js`), binary files, files over 1 MB.

---

## Database Migrations

This project uses Alembic for schema migrations. The hosted app applies them automatically on startup.

```powershell
# Check current migration version
.venv\Scripts\alembic.exe current

# Apply all pending migrations (fresh install or after schema change)
.venv\Scripts\alembic.exe upgrade head

# Check if DB matches models (should say "No new upgrade operations")
.venv\Scripts\alembic.exe check

# Create a new migration after changing a model
.venv\Scripts\alembic.exe revision --autogenerate -m "Add column X to table Y"

# Or use the helper script
.\backend\scripts\create_migration.ps1 "Add column X to table Y"

# Verify schema matches models
python backend\scripts\verify_schema.py
```

### Migration History

| Revision | Description |
|---|---|
| `577189d69fe0` | Initial migration with all models |
| `aeb8eee804c4` | Add `updated_at` to repositories, index on `url` |

---

## Testing

### Run All Tests

```powershell
python -m pytest backend/tests/ -q
```

354 tests (4 skipped without a pgvector database), about a minute.

### Test Coverage

| Test File | Covers |
|---|---|
| `test_investigation_graph.py`, `test_investigation_graph_bugs.py` | LangGraph workflow, loops, termination |
| `test_answer_generation.py`, `test_evidence_grounding.py` | Grounded answers, prompts, citations |
| `test_query_analyzer_service.py` | LLM responses, truncation, fallback, retry |
| `test_llm_service.py` | Groq client, model modes, error handling |
| `test_service_units.py` | Language detection, file filtering, retrieval, evidence ranking |
| `test_reliability_regressions.py`, `test_crumbs_followup_regressions.py`, `test_evaluator_false_positive_regressions.py` | Regressions found on real repositories |
| `test_repository_stage.py` | Indexing stage tracking |
| `test_frontend.py` | Streamlit UI helpers and API client |
| `test_deployment.py` | Database URL handling, pgvector store, re-cloning missing checkouts, demo-mode safeguards |

Set `PGVECTOR_TEST_URL` to a PostgreSQL database with the `vector` extension to run the skipped pgvector tests.

### Regression Benchmark

Runs the same 10 queries against an indexed repository and reports success rate, confidence, citation count, and per-stage timing:

```powershell
# API must be running
python backend/scripts/run_regression.py --repo-id 1 --delay 3.0
```

**Baseline results (Crumbs repository, 10 queries):**

| Metric | Result |
|---|---|
| Successful | 10/10 |
| Failed | 0 |
| Rate limit hit | No |
| Total time | ~207s |
| Avg retrieval | 3.8s |
| Avg answer generation | 14.1s |

---

## Configuration Reference

### `.env` Variables (local development)

```bash
# Required
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
GROQ_API_KEY=your_groq_api_key

# Optional (defaults shown)
APP_ENV=development
APP_NAME=AI Open-Source Project Investigator
QUERY_ANALYZER_MODEL=openai/gpt-oss-120b   # .env.example sets llama-3.3-70b-versatile
VECTOR_BACKEND=qdrant                      # or pgvector (vectors in PostgreSQL)
API_BASE_URL=http://127.0.0.1:8000         # where the UI finds the API
```

`DATABASE_URL` also accepts `postgres://` and `postgresql://` URLs. Other keys in `.env.example` (`GITHUB_TOKEN`, `REDIS_URL`, `VECTOR_DB_URL`, ...) are reserved and not used yet.

### Public Demo Variables

Set these as secrets on the hosting platform (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)):

| Variable | Purpose | Default |
|---|---|---|
| `DEMO_MODE` | Visitors can only ask questions; management needs the admin password | off locally, on when hosted |
| `ADMIN_PASSWORD` | Unlocks repository management (sidebar → *Owner access*) | none (required when hosted) |
| `DEMO_SESSION_QUERIES` | Questions per visitor per hour | 8 |
| `DEMO_QUERIES_PER_HOUR` | Questions per hour across all visitors | 40 |

### Groq Model Selection

The structured-output mode is chosen per model:

| Model | Structured output |
|---|---|
| `qwen/qwen3.8-27b` | json_schema |
| `openai/gpt-oss-20b` | json_object |
| `openai/gpt-oss-120b` | prompt only (no `response_format`) |
| any other model, e.g. `llama-3.3-70b-versatile` | json_schema |

Change the model with `QUERY_ANALYZER_MODEL=model-name` in `.env`.

---

## Known Limitations

1. **Long symbol bodies may be truncated.** When a class body spans many lines, the answer-generation token budget may cut off the explanation before the full method body.

2. **Database-config questions.** Queries phrased broadly ("explain the db config") are sometimes classified as `feature_flow` rather than `cross_cutting_concern`, so `settings.py` may not receive its full architectural bonus. Rephrase as *"How is the database configured in settings.py?"* for best results.

3. **Absence claims.** When a symbol is not found, the system reports coverage limitations rather than asserting repository-wide absence, since some file types or dynamically generated code may not be indexed.

4. **Symbols only for Python and JavaScript/TypeScript.** Other languages are searchable by text and meaning, but symbol and call-graph questions work best on those three.

5. **Rate limits.** Groq's free tier has daily token limits, and each investigation uses several thousand tokens. Use `delay_between_queries=3.0` or more in bulk requests.

6. **Hosted memory.** Streamlit Community Cloud's free tier has limited memory, so very large repositories may fail to index there.

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for a full list.

**Error pop-up in the UI.** Read *What you can try* first. The error ID in the pop-up matches the log entry (UI log, or the API log for server errors).

**Qdrant lock error.** Another process (or the server) holds the file lock:
```powershell
.\stop_server.ps1
```

**Empty evidence.** The repository isn't indexed yet, or the index is stale:
```powershell
python backend/scripts/reindex.py --repo-id 1
```

**Query analysis failures.** The system falls back to deterministic extraction and continues. Check the server logs for `[fallback]` markers.

**Rate limit hit.** The bulk endpoint skips the remaining queries and reports `rate_limit_hit: true`. Wait for the quota to reset or reduce the batch size.

---

## Acknowledgements

- [Tree-sitter](https://tree-sitter.github.io/): incremental parsing
- [LangGraph](https://langchain-ai.github.io/langgraph/): agent workflow
- [Qdrant](https://qdrant.tech/) and [pgvector](https://github.com/pgvector/pgvector): vector search
- [Groq](https://groq.com/): LLM inference
- [FastAPI](https://fastapi.tiangolo.com/): web framework
- [Streamlit](https://streamlit.io/): web UI
- [sentence-transformers](https://sbert.net/): embeddings
- [SQLAlchemy](https://sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/): ORM and migrations
