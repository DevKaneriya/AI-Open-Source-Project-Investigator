# AI OpenSource Project Investigator - Development Notes

## Recent Improvements

### Architecture-Aware Retrieval System (Complete)

The retrieval system now intelligently adapts its search strategy based on question type:

#### Question Type Classification
- **Symbol Lookup**: "Where is X defined?" → Standard symbol search
- **Call Site Lookup**: "Where is X called?" → Symbol search + relationships
- **Feature Flow**: "How does feature X work?" → Multi-file tracing with relationships
- **Architectural Overview**: "Explain this project" → README + entry points + structure files
- **Cross-Cutting Concern**: "How is caching implemented?" → Config files + relevant code
- **End-to-End Flow**: "How does data flow from frontend to DB?" → Full stack tracing
- **Dependency Analysis**: "What external services are used?" → Dependency files + integration points

#### Multi-Stage Retrieval
For broad architectural questions:
1. Discovers key files (README, entry points, config, routes, models)
2. Extracts full content of architectural files
3. Runs standard searches with reduced semantic limit
4. Applies architectural file bonuses in evidence ranking

#### Code Relationships
- Re-enabled call graph relationships
- Shows which functions call which other functions
- Question-type aware (only for flow/dependency questions)
- Filters malformed entries automatically

## Key Services

### `retrieval_service.py`
- Main orchestration service
- Multi-stage retrieval routing
- Parallel search execution
- Evidence collection

### `architectural_file_service.py`
- Discovers key architectural files by category
- Language-aware patterns
- Priority-based ordering

### `evidence_collector_service.py`
- Ranks and deduplicates evidence
- Applies architectural bonuses
- Returns top N most relevant items

### `query_analyzer_service.py`
- LLM-powered query analysis
- Classifies question type
- Extracts symbol/text/import queries

## Utility Scripts

- `check_data.py` - View database statistics (repositories, symbols, chunks)
- `check_redis.py` - Check Redis connection
- `check_relationships.py` - View call graph relationships

## Running the Backend

### Using Helper Scripts (Recommended)

**Windows PowerShell:**
```powershell
# Start server (production mode)
.\start_server.ps1

# Start server with auto-reload (development mode)
.\start_server_dev.ps1

# Stop server and clean up
.\stop_server.ps1
```

The helper scripts automatically:
- Kill any stale processes on port 8000
- Remove Qdrant lock files
- Start the server cleanly

### Manual Start (Alternative)

```bash
# Activate virtual environment
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac

# Start server
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

**Note:** If you get a Qdrant lock error, run `.\stop_server.ps1` first.

## API Endpoints

- `GET /repositories/{id}/retrieve?query=...&limit=10` - Main retrieval endpoint
- `GET /query-analyze?query=...` - Test query classification
- `GET /repositories/{id}/search?query=...` - Symbol search only
- `GET /repositories/{id}/semantic-search?query=...&limit=10` - Semantic search only

## Testing Queries

**Architectural Overview:**
- "Explain this full project and its architecture"
- "What is the structure of this codebase?"

**Cross-Cutting Concern:**
- "How is caching implemented?"
- "How is authentication handled?"

**Symbol Lookup:**
- "Where is the CartItemAdmin class defined?"
- "Find the readCookie function"

**Feature Flow:**
- "How does the shopping cart work?"
- "Trace the checkout process"

## Notes

- All temporary test files are automatically cleaned up after completion
- Keep utility scripts (`check_*.py`) for debugging
- Server auto-reloads on code changes when using `--reload` flag
- Qdrant vector DB stores semantic embeddings in `data/qdrant/`
