# AI Open-Source Project Investigator

An agentic code-analysis system that understands repository architecture, traces feature flows, and answers technical questions with evidence grounded directly in source code.

---

## What It Does

Point the system at any public GitHub repository. After indexing, ask natural-language questions and get answers backed by cited source snippets — not guesses.

**Example questions:**
- *"How does the checkout flow work from the Angular UI to the Django backend?"*
- *"Locate CartSyncView and explain its GET and POST implementation."*
- *"What are the exact Angular and Django versions?"*
- *"How is Razorpay payment verification implemented?"*

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI (Python) |
| Database | PostgreSQL + SQLAlchemy 2.0 |
| Migrations | Alembic |
| Vector DB | Qdrant (local file mode) |
| Embeddings | `BAAI/bge-small-en-v1.5` (sentence-transformers) |
| LLM | Groq API (configurable model) |
| Parsing | Tree-sitter + Python AST |

---

## Project Structure

```
AI-OpenSource-Project-Investigator/
├── backend/
│   ├── alembic/                    # Database migrations
│   │   ├── env.py                  # Migration environment
│   │   └── versions/               # Migration files
│   ├── app/
│   │   ├── main.py                 # FastAPI app + all endpoints
│   │   ├── core/
│   │   │   ├── config.py           # Pydantic settings (reads .env)
│   │   │   └── database.py         # SQLAlchemy engine + session
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   │   ├── repository.py
│   │   │   ├── repository_file.py
│   │   │   ├── code_symbol.py
│   │   │   ├── code_chunk.py
│   │   │   ├── code_call.py
│   │   │   ├── code_import.py
│   │   │   ├── query_analysis.py   # Pydantic schema for LLM output
│   │   │   └── investigation_answer.py  # Response schema
│   │   └── services/
│   │       ├── retrieval_service.py           # Main orchestrator
│   │       ├── query_analyzer_service.py      # LLM + fallback
│   │       ├── answer_generation_service.py   # Evidence-grounded answers
│   │       ├── evidence_collector_service.py  # Merge + rank evidence
│   │       ├── architectural_file_service.py  # Find key files
│   │       ├── code_search_service.py         # Symbol DB search
│   │       ├── repository_search_service.py   # Full-text file search
│   │       ├── semantic_search_service.py     # Qdrant vector search
│   │       ├── code_relationship_service.py   # Call graph queries
│   │       ├── code_retrieval_service.py      # Source extraction
│   │       ├── code_indexer.py                # Parse + index
│   │       ├── code_chunk_service.py          # Chunking
│   │       ├── code_analyzer.py               # Python AST
│   │       ├── tree_sitter_analyzer.py        # JS/TS AST
│   │       ├── embedding_service.py           # sentence-transformers
│   │       ├── vector_service.py              # Qdrant client
│   │       ├── vector_indexing_service.py     # Embed + upsert chunks
│   │       ├── github_service.py              # Clone repos
│   │       ├── repository_scanner.py          # File discovery
│   │       ├── repository_service.py          # Repo CRUD
│   │       ├── language_service.py            # Extension → language
│   │       ├── file_filter_service.py         # Exclusion rules
│   │       ├── import_search_service.py       # Import DB search
│   │       └── search_normalizer.py           # Query normalisation
│   ├── scripts/
│   │   ├── reindex.py              # Re-index a repository
│   │   ├── run_regression.py       # Run 10 benchmark queries
│   │   ├── verify_schema.py        # Check DB matches models
│   │   ├── apply_migrations.ps1    # Apply pending migrations
│   │   └── create_migration.ps1    # Generate a new migration
│   └── tests/
│       ├── test_query_analyzer_service.py  # 25 tests
│       ├── test_evidence_grounding.py      # 16 tests
│       ├── test_llm_service.py             # 15 tests
│       └── test_service_units.py           # 40 tests
├── data/
│   └── qdrant/                     # Qdrant local storage
├── alembic.ini                     # Alembic config
├── requirements.txt
├── check_data.py                   # Inspect indexed DB stats
├── check_relationships.py          # Inspect call graph data
├── start_server.ps1
├── start_server_dev.ps1
├── stop_server.ps1
└── TROUBLESHOOTING.md
```

---

## Quick Start

### 1. Prerequisites

- Python 3.12+
- PostgreSQL running locally
- `uv` package manager (`pip install uv`)
- Groq API key (free at [console.groq.com](https://console.groq.com))

### 2. Install Dependencies

```powershell
uv pip install -r requirements.txt
```

### 3. Configure Environment

Copy `.env.example` to `.env` and fill in values:

```bash
APP_ENV=development

DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/ai_opensource_investigator

GROQ_API_KEY=your_groq_api_key_here
QUERY_ANALYZER_MODEL=llama-3.3-70b-versatile
```

> Qdrant runs in local file mode — no server needed. Data is stored in `data/qdrant/`.

### 4. Set Up Database

```powershell
# Create the PostgreSQL database first, then:
.venv\Scripts\alembic.exe upgrade head
```

### 5. Start the Server

```powershell
# Development (with auto-reload)
.\start_server_dev.ps1

# Production
.\start_server.ps1
```

Server runs at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

---

## Indexing a Repository

Run these four steps in order. Each step depends on the previous one.

```bash
# 1. Clone the repository
POST /repositories
Body: {"repository_url": "https://github.com/user/repo"}

# 2. Parse and index symbols
POST /repositories/{id}/index

# 3. Create semantic chunks
POST /repositories/{id}/chunks

# 4. Generate embeddings and index in Qdrant
POST /repositories/{id}/vector-index
```

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

### Investigate Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/repositories/{id}/investigate` | Single evidence-grounded investigation |
| `POST` | `/repositories/{id}/investigate/bulk` | Up to 10 queries in one request |

**Single investigation:**
```bash
POST /repositories/1/investigate
Body: {"query": "How does the checkout flow work?"}
```

**Bulk investigation:**
```bash
POST /repositories/1/investigate/bulk?delay_between_queries=3.0
Body: [
  "Trace the checkout flow end-to-end.",
  "Explain how cart items are synchronized.",
  "What are the Angular and Django versions?"
]
```

Response includes per-query `stage_times` (retrieval, answer generation) and a summary with `avg_stage_times`.

### Retrieval Endpoint

```bash
GET /repositories/{id}/retrieve?query=How does auth work?&semantic_limit=10
```

Returns raw ranked evidence — useful for debugging what the system finds before LLM answer generation.

### Indexing Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/repositories` | Clone a repository |
| `POST` | `/repositories/{id}/index` | Parse and index symbols |
| `POST` | `/repositories/{id}/chunks` | Create semantic chunks |
| `POST` | `/repositories/{id}/vector-index` | Embed chunks into Qdrant |

### Search Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/repositories/{id}/search` | Symbol search |
| `GET` | `/repositories/{id}/search-text` | Full-text search |
| `GET` | `/repositories/{id}/semantic-search` | Vector similarity search |
| `GET` | `/repositories/{id}/relationships/calls` | Call graph for a symbol |
| `GET` | `/repositories/{id}/source` | Raw source extraction |
| `GET` | `/query-analyze` | Test query classification |

---

## How Retrieval Works

Every query goes through this pipeline:

```
User query
    │
    ▼
Query Analyzer  ──── LLM classifies type + extracts search terms
    │                 Falls back to deterministic extraction on LLM failure
    │
    ▼
Question type router
    │
    ├─── DEPENDENCY_ANALYSIS / ARCHITECTURAL_OVERVIEW
    │        └─► Staged: discover manifests + config files first
    │
    ├─── FEATURE_FLOW / END_TO_END_FLOW
    │        └─► Standard + follow called functions (targeted flow retrieval)
    │
    └─── SYMBOL_LOOKUP / all others
             └─► Standard search only
    │
    ▼
Parallel searches
    ├── Symbol DB search  (exact + fuzzy identifier matching)
    ├── Full-text search  (grep across all repository files)
    ├── Import search     (module/package references)
    └── Semantic search   (Qdrant vector similarity)
    │
    ▼
Evidence Collector
    ├── Merge architectural files into candidate pool
    ├── Deduplicate by file + line range
    ├── Score each candidate (base score by source type)
    ├── Apply architectural bonuses (manifests, config, README)
    └── Select top 12 evidence items
    │
    ▼
Answer Generation (LLM)
    └── Evidence-grounded answer with inline citations
```

### Question Types

| Type | Example | Strategy |
|---|---|---|
| `symbol_lookup` | "Where is CartSyncView defined?" | Symbol search only |
| `call_site_lookup` | "Where is addToCart called?" | Symbol + relationships |
| `file_explanation` | "What does views.py do?" | Source + semantic |
| `feature_flow` | "How does checkout work?" | All sources + follow calls |
| `architectural_overview` | "Explain this project" | Staged architectural files |
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
  semantic        → 0.35–0.70  vector similarity

Architectural bonuses (added on top):
  package manifests   → +0.45  (dependency questions)
  README              → +0.30  (architectural questions)
  entry points        → +0.25
  routes files        → +0.20
  models/config       → +0.15
```

---

## Evidence-Grounded Answers

The `investigate` endpoints generate answers using an LLM constrained to the retrieved evidence. The system:

- Cites every claim with evidence IDs (E1, E2, …)
- Refuses to invent code, functions, or side effects not in evidence
- Distinguishes confirmed code from docstring intentions
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

---

## Query Analysis with Fallback

The query analyzer uses a compact JSON schema (not the full Pydantic `$defs` output) to stay well within the LLM token budget. On any LLM failure, a deterministic fallback activates:

- Extracts CamelCase/snake_case identifiers → `symbol_queries`
- Strips stopwords → `text_queries`
- Classifies type from keywords (no LLM needed)
- Investigation continues — never aborts on query analysis failure

Responses marked `[FALLBACK]` in bulk results indicate the LLM was unavailable for that query.

---

## Supported File Types

The indexer parses and indexes these file types for symbols:

| Extension | Language | Symbol Extraction |
|---|---|---|
| `.py` | Python | AST (full: functions, classes, decorators, calls) |
| `.ts`, `.tsx` | TypeScript | Tree-sitter |
| `.js`, `.jsx` | JavaScript | Tree-sitter |
| `.java` | Java | Tree-sitter |
| `.go` | Go | Tree-sitter |
| `.cs` | C# | Tree-sitter |
| `.cpp`, `.cc`, `.c` | C/C++ | Tree-sitter |
| `.rs` | Rust | Tree-sitter |
| `.php` | PHP | Tree-sitter |
| `.rb` | Ruby | Tree-sitter |

These file types are indexed for content but have no symbol extraction (full-text and semantic search still work):

| Extension | Type |
|---|---|
| `.json` | Dependency manifests (package.json, etc.) |
| `.txt` | Plain text (requirements.txt, etc.) |
| `.toml` | Config (pyproject.toml, etc.) |
| `.yaml`, `.yml` | Config files |

**Excluded automatically:** `node_modules/`, `.venv/`, `dist/`, `build/`, lock files (`package-lock.json`, `yarn.lock`), compiled files (`.pyc`, `.min.js`), binary files, files over 1 MB.

---

## Database Migrations

This project uses Alembic for schema migrations.

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
| `577189d69fe0` | Initial migration — all models |
| `aeb8eee804c4` | Add `updated_at` to repositories, index on `url` |

---

## Testing

### Run All Tests

```powershell
python -m pytest backend/tests/ -v
```

96 tests, ~4 seconds.

### Test Coverage

| Test File | Count | Covers |
|---|---|---|
| `test_query_analyzer_service.py` | 25 | LLM responses, truncation, fallback, retry |
| `test_service_units.py` | 40 | Language detection, file filtering, retrieval, evidence ranking |
| `test_evidence_grounding.py` | 16 | Evidence validation, prompt guidance, citations |
| `test_llm_service.py` | 15 | Groq client, model modes, error handling |

### Regression Benchmark

Runs the same 10 queries against an indexed repository and reports success rate, confidence, citation count, and per-stage timing:

```powershell
# Server must be running
python backend/scripts/run_regression.py --delay 3.0
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

## Utilities

```powershell
# Inspect indexed database stats
python check_data.py

# Inspect call graph relationships
python check_relationships.py

# Re-index a repository after investigator code changes
python backend/scripts/reindex.py --repo-id 1

# Skip slow vector re-embedding (use after symbol-only changes)
python backend/scripts/reindex.py --repo-id 1 --skip-vectors
```

---

## Configuration Reference

### `.env` Variables

```bash
# Required
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/dbname
GROQ_API_KEY=your_groq_api_key

# Optional — defaults shown
APP_ENV=development
APP_NAME=AI Open-Source Project Investigator
QUERY_ANALYZER_MODEL=llama-3.3-70b-versatile

# Not required (Qdrant runs in local file mode)
# VECTOR_DB_URL=
# VECTOR_DB_API_KEY=
```

### Groq Model Selection

The system is tested with these models on Groq:

| Model | Structured output | Notes |
|---|---|---|
| `llama-3.3-70b-versatile` | json_object | Recommended default |
| `qwen/qwen3.8-27b` | json_schema | Full schema support |
| `openai/gpt-oss-20b` | json_object | Faster, less accurate |
| `openai/gpt-oss-120b` | prompt injection only | Use only if others unavailable |

Change model with `QUERY_ANALYZER_MODEL=model-name` in `.env`.

---

## Known Limitations

1. **Short symbol bodies may be truncated** — when a class body spans many lines, the LLM answer-generation token budget may cut off the explanation before the full method body.

2. **Database-config questions** — queries phrased broadly ("explain the db config") are sometimes classified as `feature_flow` rather than `cross_cutting_concern`, so `settings.py` may not receive its full architectural bonus. Rephrase as *"How is the database configured in settings.py?"* for best results.

3. **Absence claims** — when a symbol is not found, the system reports coverage limitations rather than asserting repository-wide absence, since some file types or dynamically generated code may not be indexed.

4. **Rate limits** — Groq free tier: 200k tokens/day. Each investigation query costs ~2,000–8,000 tokens. Use `delay_between_queries=3.0` in bulk requests to stay within rate limits.

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for a full list.

**Qdrant lock error** — another process (or the server) holds the file lock:
```powershell
.\stop_server.ps1
```

**Empty evidence** — repository not yet indexed or index is stale:
```powershell
python backend/scripts/reindex.py --repo-id 1
```

**Query analysis failures** — the system falls back to deterministic extraction and continues. Check server logs for `[fallback]` markers.

**Rate limit hit** — bulk endpoint automatically skips remaining queries and reports `rate_limit_hit: true`. Wait for quota reset (Groq resets daily) or reduce batch size.

---

## Acknowledgements

- [Tree-sitter](https://tree-sitter.github.io/) — incremental parsing
- [Qdrant](https://qdrant.tech/) — vector database
- [Groq](https://groq.com/) — LLM inference
- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [sentence-transformers](https://sbert.net/) — embeddings
- [SQLAlchemy](https://sqlalchemy.org/) + [Alembic](https://alembic.sqlalchemy.org/) — ORM and migrations
