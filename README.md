# AI Open-Source Project Investigator

An intelligent code analysis system that understands repository architecture, traces feature flows, and provides context-aware evidence for technical questions. Built to help developers quickly understand unfamiliar codebases.

## 🎯 Overview

This system combines multiple retrieval strategies—symbol search, text matching, semantic search, and relationship tracing—to answer questions ranging from "Where is X defined?" to "Explain the entire project architecture."

### Key Capabilities

- **Architecture-Aware Retrieval**: Automatically discovers and prioritizes README files, entry points, configuration, routes, and models for architectural questions
- **Intelligent Question Classification**: Distinguishes between symbol lookups, feature flows, architectural overviews, and cross-cutting concerns
- **Multi-Source Evidence Fusion**: Combines deterministic (symbol/text) and semantic search with call-graph relationships
- **Smart Evidence Ranking**: Prioritizes exact matches, architectural significance, and source agreement

---

## 🏗️ System Architecture

### High-Level Flow

```
User Query
    ↓
Query Analysis (LLM) → Classify question type + Extract search terms
    ↓
┌─────────────────────────────────────────┐
│ Multi-Stage Retrieval                   │
├─────────────────────────────────────────┤
│ Architectural Overview?                 │
│  → Discover: README, entry points,      │
│    config, routes, models               │
│                                         │
│ Feature Flow / Cross-Cutting?           │
│  → Run: Symbol + Text + Semantic        │
│  → Extract: Call relationships          │
│                                         │
│ Symbol Lookup?                          │
│  → Direct: Symbol search only           │
└─────────────────────────────────────────┘
    ↓
Evidence Collection → Merge, deduplicate, rank
    ↓
Response Transformation → API format
    ↓
JSON Response with ranked evidence
```

---

## 📁 Project Structure

```
AI-OpenSource-Project-Investigator/
├── backend/
│   └── app/
│       ├── main.py                    # FastAPI application & endpoints
│       ├── core/
│       │   ├── config.py             # Configuration settings
│       │   └── database.py           # SQLAlchemy setup
│       ├── models/                   # Database models
│       │   ├── repository.py         # Repository metadata
│       │   ├── repository_file.py    # Indexed files
│       │   ├── code_symbol.py        # Functions, classes, methods
│       │   ├── code_chunk.py         # Semantic chunks
│       │   ├── code_call.py          # Call relationships
│       │   ├── code_import.py        # Import statements
│       │   └── query_analysis.py     # Query classification types
│       └── services/
│           ├── retrieval_service.py              # 🔥 Main orchestrator
│           ├── query_analyzer_service.py         # LLM-powered classification
│           ├── evidence_collector_service.py     # Ranking & deduplication
│           ├── architectural_file_service.py     # Key file discovery
│           ├── code_search_service.py            # Symbol search
│           ├── repository_search_service.py      # Text search (ripgrep)
│           ├── semantic_search_service.py        # Vector similarity
│           ├── code_relationship_service.py      # Call graph queries
│           ├── code_retrieval_service.py         # Source extraction
│           ├── github_service.py                 # Repository cloning
│           ├── repository_scanner.py             # File discovery
│           ├── code_indexer.py                   # Tree-sitter parsing
│           ├── embedding_service.py              # Text embeddings
│           ├── vector_service.py                 # Qdrant interface
│           ├── vector_indexing_service.py        # Chunk indexing
│           └── llm_service.py                    # Groq API client
├── data/
│   └── qdrant/                       # Vector database storage
├── check_data.py                     # Database inspection utility
├── check_redis.py                    # Redis connection test
├── check_relationships.py            # Call graph inspection
├── start_server.ps1                  # Production server launcher
├── start_server_dev.ps1              # Development server launcher
├── stop_server.ps1                   # Server cleanup script
└── TROUBLESHOOTING.md                # Common issues & solutions
```

---

## 🔧 Core Services

### 1. **Retrieval Service** (`retrieval_service.py`)
**Purpose**: Main orchestration layer that coordinates all retrieval strategies.

**Key Methods**:
- `retrieve(db, repository_id, query, semantic_limit)`: Main entry point
- `_staged_architectural_retrieval()`: Discovers and extracts key architectural files
- `_build_source_context()`: Extracts source code for matched locations
- `_build_relationships()`: Retrieves call-graph relationships
- `_transform_evidence_for_response()`: Formats output for API

**Process Flow**:
1. Analyze query with LLM
2. Determine if architectural/feature/symbol question
3. Run appropriate retrieval strategy
4. Merge all evidence sources
5. Rank and return top results

**Question-Type Routing**:
- **ARCHITECTURAL_OVERVIEW**: Discover README → entry points → dependencies → routes → models
- **CROSS_CUTTING_CONCERN**: Find config files → dependencies → middleware
- **FEATURE_FLOW**: Symbol + text + semantic + relationships
- **SYMBOL_LOOKUP**: Direct symbol search only

---

### 2. **Query Analyzer Service** (`query_analyzer_service.py`)
**Purpose**: LLM-powered query understanding and classification.

**Key Methods**:
- `analyze(query)`: Returns `QueryAnalysis` object

**Output**:
```python
{
    "intent": "Understand shopping cart implementation",
    "question_type": "feature_flow",  # 8 types
    "concepts": ["cart", "items", "checkout"],
    "symbol_queries": ["Cart", "CartItem", "addToCart"],
    "text_queries": ["shopping cart", "add to cart"],
    "import_queries": ["cart", "cart_service"]
}
```

**Question Types**:
1. **SYMBOL_LOOKUP**: "Where is X defined?"
2. **CALL_SITE_LOOKUP**: "Where is X called?"
3. **FILE_EXPLANATION**: "What does file.py do?"
4. **FEATURE_FLOW**: "How does feature X work?"
5. **ARCHITECTURAL_OVERVIEW**: "Explain the architecture"
6. **END_TO_END_FLOW**: "Trace checkout from frontend to database"
7. **CROSS_CUTTING_CONCERN**: "How is caching implemented?"
8. **DEPENDENCY_ANALYSIS**: "What external services are used?"

---

### 3. **Evidence Collector Service** (`evidence_collector_service.py`)
**Purpose**: Merges results from multiple sources, deduplicates, and ranks evidence.

**Key Methods**:
- `collect(retrieval_results, max_evidence, question_type)`: Main collection
- `_merge_candidates()`: Deduplicates overlapping evidence
- `_candidate_key()`: Groups evidence by location
- `_combined_score()`: Rewards agreement between sources
- `_get_architectural_bonus()`: Awards bonuses for important files

**Scoring System**:
```python
TYPE_BASE_SCORES = {
    "source_context": 1.00,   # Actual source code
    "symbol": 0.98,            # Exact symbol match
    "text_match": 0.82,        # Text grep match
    "import": 0.68,            # Import statement
    "relationship": 0.66,      # Call graph entry
    "semantic": 0.35,          # Vector similarity
}

ARCHITECTURAL_BONUSES = {
    "readme": +0.30,
    "entry_point": +0.25,
    "routes": +0.20,
    "models": +0.15,
    "configuration": +0.15,
    "dependency": +0.10,
}
```

**Deduplication Strategy**:
- Groups by file path + line range
- Merges symbol + source_context for same location
- Awards corroboration bonus when multiple methods find same code

---

### 4. **Architectural File Service** (`architectural_file_service.py`)
**Purpose**: Discovers key architectural files without full repository scan.

**Key Methods**:
- `discover_key_files(db, repository_id)`: Returns categorized files
- `get_file_category(file_path)`: Classifies file type

**Discovery Categories**:
- **README**: Root README.md prioritized
- **Entry Points**: main.py, manage.py, index.ts, app.py
- **Dependencies**: requirements.txt, package.json, pom.xml
- **Configuration**: settings.py, config.py, .env.example
- **Routes**: urls.py, routes.ts, router.js
- **Models**: models.py, schema.py, entities.ts

**Language-Aware Patterns**:
- Python: manage.py, settings.py, models.py, urls.py
- JavaScript/TypeScript: index.ts, app.ts, routes.ts
- Java: Application.java, pom.xml
- Go: main.go, go.mod

---

### 5. **Code Search Service** (`code_search_service.py`)
**Purpose**: Symbol search across indexed code symbols.

**Key Methods**:
- `search(db, repository_id, query)`: Fuzzy symbol matching

**Indexed Symbols**:
- Functions, classes, methods, variables, constants
- Qualified names (e.g., `MyClass.method`)
- Language-aware parsing via Tree-sitter

**Search Strategy**:
- Exact matches first
- Prefix matches second
- Contains matches third
- Case-insensitive fuzzy matching

**Output**:
```python
{
    "id": 123,
    "name": "addToCart",
    "symbol_type": "method",
    "qualified_name": "CartService.addToCart",
    "file": "services/cart.py",
    "language": "python",
    "start_line": 45,
    "end_line": 67,
    "signature": "def addToCart(self, item_id, quantity):"
}
```

---

### 6. **Repository Search Service** (`repository_search_service.py`)
**Purpose**: Fast text search using ripgrep.

**Key Methods**:
- `search_text(repository_path, query)`: Full-text search

**Features**:
- Uses ripgrep for high-speed search
- Respects .gitignore
- Returns file path, line number, matched text
- Case-insensitive by default

**Output**:
```python
{
    "file": "backend/views.py",
    "line": 128,
    "text": "def checkout_cart(request):"
}
```

---

### 7. **Semantic Search Service** (`semantic_search_service.py`)
**Purpose**: Vector similarity search over code chunks.

**Key Methods**:
- `search(db, repository_id, query, limit)`: Semantic matching

**Process**:
1. Embed query with sentence-transformers
2. Search Qdrant vector database
3. Return top-K similar chunks
4. Include similarity scores

**Chunking Strategy**:
- Symbol-aware chunking (whole functions/classes)
- Context-preserving splits
- 200-500 tokens per chunk
- Overlap for continuity

**Output**:
```python
{
    "score": 0.87,
    "chunk_id": 456,
    "file": "cart/cart.py",
    "symbol": "CartManager",
    "symbol_type": "class",
    "start_line": 15,
    "end_line": 120,
    "content": "class CartManager:\n    def add_item..."
}
```

---

### 8. **Code Relationship Service** (`code_relationship_service.py`)
**Purpose**: Queries call-graph relationships.

**Key Methods**:
- `get_calls(db, repository_id, symbol_name)`: Get what a symbol calls
- `get_callers(db, repository_id, symbol_name)`: Get who calls a symbol

**Database Schema**:
```python
CodeCall:
    caller: str          # Calling function/method
    caller_file: str     # Source file
    caller_line: int     # Line number
    called_name: str     # Called function
```

**Use Cases**:
- Feature flow tracing: "What does checkout call?"
- Impact analysis: "What calls authenticate()?"
- Dependency tracking: "What uses Database.query?"

---

### 9. **Code Indexer** (`code_indexer.py`)
**Purpose**: Parses code and extracts symbols, imports, and relationships.

**Key Methods**:
- `index_repository(db, repository_id, local_path)`: Full indexing

**Process**:
1. Scan repository for code files
2. Parse with Tree-sitter (language-aware AST)
3. Extract:
   - Functions, classes, methods
   - Import statements
   - Function calls (call graph)
4. Store in SQLite database

**Supported Languages**:
- Python, JavaScript, TypeScript, Java, Go, C++, Ruby

---

### 10. **LLM Service** (`llm_service.py`)
**Purpose**: Groq API interface for query analysis.

**Key Methods**:
- `analyze_query(query)`: Send query to LLM for structured analysis

**Configuration**:
- Model: `llama-3.3-70b-versatile` (fast, high quality)
- Temperature: 0.0 (deterministic)
- JSON mode: Structured output
- Timeout: 30 seconds

---

## 🌐 API Endpoints

### 1. **POST `/repositories`**
**Purpose**: Add a repository to the system.

**Request**:
```json
{
    "repository_url": "https://github.com/user/repo"
}
```

**Response**:
```json
{
    "id": 1,
    "name": "repo",
    "url": "https://github.com/user/repo",
    "status": "cloned",
    "local_path": "/tmp/repo_xyz/repository"
}
```

**Process**:
1. Clone repository
2. Create database record
3. Return repository metadata

---

### 2. **POST `/repositories/{id}/index`**
**Purpose**: Parse and index repository code.

**Response**:
```json
{
    "repository_id": 1,
    "files_indexed": 138,
    "symbols_indexed": 618,
    "imports_indexed": 245,
    "relationships_indexed": 623
}
```

**Process**:
1. Scan repository for code files
2. Parse with Tree-sitter
3. Extract symbols, imports, calls
4. Store in database

**Duration**: ~30 seconds for medium repo

---

### 3. **POST `/repositories/{id}/chunks`**
**Purpose**: Create semantic chunks for vector search.

**Response**:
```json
{
    "repository_id": 1,
    "chunks_created": 670
}
```

**Process**:
1. Read indexed code files
2. Split into symbol-aware chunks
3. Store with metadata (file, symbol, lines)

---

### 4. **POST `/repositories/{id}/vector-index`**
**Purpose**: Generate embeddings and index in Qdrant.

**Response**:
```json
{
    "repository_id": 1,
    "chunks_indexed": 670,
    "collection": "repository_code"
}
```

**Process**:
1. Load chunks from database
2. Generate embeddings (sentence-transformers)
3. Index in Qdrant vector database

**Duration**: ~2-3 minutes for medium repo

---

### 5. **GET `/repositories/{id}/retrieve`** 🔥 **MAIN ENDPOINT**
**Purpose**: Intelligent code retrieval with multi-source fusion.

**Request**:
```
GET /repositories/1/retrieve?query=How does the shopping cart work?&limit=10
```

**Response**:
```json
{
    "query": "How does the shopping cart work?",
    "repository": {
        "id": 1,
        "name": "Crumbs",
        "url": "https://github.com/user/repo"
    },
    "query_analysis": {
        "intent": "Understand shopping cart feature implementation",
        "question_type": "feature_flow",
        "concepts": ["shopping", "cart", "items"],
        "symbol_queries": ["Cart", "CartItem", "addToCart"],
        "text_queries": ["shopping cart", "cart service"],
        "import_queries": ["cart"]
    },
    "evidence": [
        {
            "file": "frontend/cart.ts",
            "language": "typescript",
            "symbol": "Cart",
            "symbol_type": "class",
            "start_line": 15,
            "end_line": 116,
            "snippet": "export class Cart {\n  items: CartItem[] = [];\n  ...",
            "relevance_score": 1.15,
            "types": ["symbol", "source_context"],
            "exact_match": true
        }
    ],
    "raw_results": {
        "symbols": [...],
        "text_matches": [...],
        "semantic": [...],
        "relationships": [...],
        "architectural_files": [...]
    },
    "summary": {
        "symbol_matches": 43,
        "text_matches": 4,
        "semantic_matches": 10,
        "source_contexts": 10,
        "relationships": 0,
        "architectural_files": 0,
        "evidence_selected": 12,
        "execution_time_seconds": 7.12
    },
    "metadata": {
        "version": "1.0",
        "timestamp": 1234567890,
        "relationships_enabled": true
    }
}
```

**Process**:
1. **Query Analysis**: LLM classifies question type
2. **Routing**: Select appropriate strategy
3. **Multi-Source Retrieval**:
   - Symbol search for exact matches
   - Text search for keywords
   - Semantic search for conceptual similarity
   - Relationship extraction for call graphs
   - Architectural file discovery (if applicable)
4. **Evidence Fusion**: Merge and deduplicate
5. **Ranking**: Score by relevance, exact matches, architectural significance
6. **Response**: Return top N evidence items

**Query Parameters**:
- `query` (required): Natural language question
- `limit` (optional): Max semantic results (default: 10)

---

### 6. **GET `/repositories/{id}/search`**
**Purpose**: Direct symbol search (no semantic).

**Request**:
```
GET /repositories/1/search?query=CartItem
```

**Response**:
```json
{
    "query": "CartItem",
    "results": [
        {
            "name": "CartItem",
            "symbol_type": "class",
            "file": "models.py",
            "start_line": 45,
            "end_line": 67
        }
    ]
}
```

---

### 7. **GET `/repositories/{id}/search-text`**
**Purpose**: Direct text search (ripgrep).

**Request**:
```
GET /repositories/1/search-text?query=add_to_cart
```

**Response**:
```json
{
    "query": "add_to_cart",
    "results": [
        {
            "file": "views.py",
            "line": 128,
            "text": "def add_to_cart(request):"
        }
    ]
}
```

---

### 8. **GET `/repositories/{id}/semantic-search`**
**Purpose**: Direct semantic search (vector similarity).

**Request**:
```
GET /repositories/1/semantic-search?query=checkout process&limit=5
```

**Response**:
```json
{
    "repository_id": 1,
    "query": "checkout process",
    "results": [
        {
            "score": 0.87,
            "file": "checkout.py",
            "content": "def process_checkout(cart):\n    ..."
        }
    ],
    "count": 5
}
```

---

### 9. **GET `/repositories/{id}/relationships/calls`**
**Purpose**: Get call graph for a symbol.

**Request**:
```
GET /repositories/1/relationships/calls?symbol_name=checkout
```

**Response**:
```json
{
    "symbol": "checkout",
    "calls": [
        {
            "caller": "checkout",
            "caller_file": "views.py",
            "caller_line": 45,
            "called_name": "validate_cart"
        },
        {
            "caller": "checkout",
            "caller_file": "views.py",
            "caller_line": 48,
            "called_name": "process_payment"
        }
    ]
}
```

---

### 10. **GET `/repositories/{id}/source`**
**Purpose**: Extract source code for specific lines.

**Request**:
```
GET /repositories/1/source?file_path=cart.py&start_line=10&end_line=50
```

**Response**:
```json
{
    "file": "cart.py",
    "start_line": 10,
    "end_line": 50,
    "source": "class Cart:\n    def __init__(self):\n        ..."
}
```

---

### 11. **GET `/query-analyze`**
**Purpose**: Test query analysis without retrieval.

**Request**:
```
GET /query-analyze?query=How does authentication work?
```

**Response**:
```json
{
    "intent": "Understand authentication implementation",
    "question_type": "cross_cutting_concern",
    "concepts": ["authentication", "auth", "login"],
    "symbol_queries": ["authenticate", "login", "JWT"],
    "text_queries": ["authentication", "auth flow"],
    "import_queries": ["auth", "jwt"]
}
```

---

## 🔄 Complete Workflow Example

### Scenario: "How does the shopping cart work?"

**Step 1: Repository Setup**
```bash
# Clone and index repository
POST /repositories {"repository_url": "..."}
POST /repositories/1/index
POST /repositories/1/chunks
POST /repositories/1/vector-index
```

**Step 2: Query**
```bash
GET /repositories/1/retrieve?query=How does the shopping cart work?
```

**Step 3: Internal Process**

1. **Query Analysis** (LLM):
   ```python
   {
       "question_type": "feature_flow",
       "symbol_queries": ["Cart", "CartItem", "addToCart"],
       "text_queries": ["shopping cart", "cart service"],
       "import_queries": ["cart"]
   }
   ```

2. **Parallel Searches**:
   - Symbol search: Finds `Cart`, `CartItem`, `CartService` classes
   - Text search: Finds "shopping cart" in comments/docs
   - Semantic search: Finds similar cart-related code

3. **Source Extraction**:
   - Retrieves actual source code for matched symbols
   - Expands context (±10 lines for single-line matches)

4. **Relationship Extraction**:
   - Finds what `addToCart` calls
   - Discovers `cart.add()`, `validate_item()`, etc.

5. **Evidence Collection**:
   - Merges symbol + source for same location
   - Deduplicates overlapping evidence
   - Ranks by relevance score

6. **Response**:
   ```json
   {
       "evidence": [
           {
               "file": "cart.ts",
               "symbol": "Cart",
               "snippet": "export class Cart {...}",
               "relevance_score": 1.15,
               "types": ["symbol", "source_context", "semantic"]
           }
       ]
   }
   ```

---

## 🧪 Testing

### Manual Testing

```bash
# Start server
.\start_server.ps1

# Test architectural question
curl "http://localhost:8000/repositories/1/retrieve?query=Explain this project"

# Test feature flow
curl "http://localhost:8000/repositories/1/retrieve?query=How does authentication work?"

# Test symbol lookup
curl "http://localhost:8000/repositories/1/retrieve?query=Where is CartItemAdmin defined?"
```

### Database Inspection

```bash
# View indexed data
python check_data.py

# View relationships
python check_relationships.py

# Check Redis
python check_redis.py
```

---

## 📊 Performance Characteristics

### Retrieval Performance

| Query Type | Execution Time | Evidence Quality |
|------------|---------------|------------------|
| Symbol Lookup | 2-4s | Exact matches |
| Feature Flow | 5-8s | Comprehensive |
| Architectural Overview | 6-10s | High-level view |
| Cross-Cutting Concern | 4-6s | Config + code |

### Scaling

- **Small Repo** (<100 files): <5s retrieval
- **Medium Repo** (100-500 files): 5-10s retrieval
- **Large Repo** (>500 files): 10-20s retrieval

### Database Sizes

- SQLite: ~50MB for 500 files
- Qdrant: ~200MB for 1000 chunks
- Total: ~250MB per medium repository

---

## 🛠️ Configuration

### Environment Variables (`.env`)

```bash
# Database
DATABASE_URL=sqlite:///./repository_data.db

# Qdrant Vector Database
QDRANT_PATH=./data/qdrant

# Groq API (for query analysis)
GROQ_API_KEY=your_groq_api_key_here
QUERY_ANALYZER_MODEL=llama-3.3-70b-versatile

# Embedding Model
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIMENSION=384

# Application
APP_NAME=AI Open-Source Project Investigator
DEBUG=False
```

### Model Configuration

**Query Analyzer**: `llama-3.3-70b-versatile`
- Purpose: Question classification and query decomposition
- Speed: ~1-2 seconds per query
- Quality: High accuracy for technical questions

**Embedding Model**: `all-MiniLM-L6-v2`
- Purpose: Semantic similarity
- Dimension: 384
- Speed: ~50ms per query

---

## 🚀 Deployment

### Development

```powershell
# Start with auto-reload
.\start_server_dev.ps1
```

### Production

```powershell
# Start without auto-reload
.\start_server.ps1
```

### Docker (Future)

```bash
# Build
docker build -t ai-investigator .

# Run
docker run -p 8000:8000 -v ./data:/app/data ai-investigator
```

---

## 🐛 Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues.

### Quick Fixes

**Qdrant Lock Error**:
```powershell
.\stop_server.ps1
```

**Empty Results**:
```bash
# Re-index repository
POST /repositories/{id}/index
POST /repositories/{id}/vector-index
```

**Slow Queries**:
- Reduce `limit` parameter
- Check database size with `python check_data.py`

---

## 📈 Future Enhancements

### Planned Features

1. **Better Relationship Extraction**
   - Extract from merged evidence, not just initial symbol results
   - Support transitive call chains

2. **Improved Deduplication**
   - Handle overlapping line ranges (>50% overlap)
   - Smart merging for similar but not identical matches

3. **Query Refinement**
   - Interactive follow-up questions
   - Query suggestions based on repository

4. **Multi-Repository Search**
   - Cross-repository symbol search
   - Dependency analysis across repos

5. **Frontend UI**
   - Visual code navigation
   - Interactive relationship graphs
   - Side-by-side evidence comparison

---

## 🤝 Contributing

### Code Style

- Python: PEP 8, type hints
- Line length: 80 characters
- Docstrings: Google style

### Testing

```bash
# Run tests
pytest tests/

# Check coverage
pytest --cov=backend tests/
```

---

## 📝 License

[Your License Here]

---

## 📧 Contact

[Your Contact Information]

---

## 🙏 Acknowledgments

- **Tree-sitter**: AST parsing
- **Qdrant**: Vector database
- **Groq**: LLM API
- **FastAPI**: Web framework
- **sentence-transformers**: Embeddings
- **ripgrep**: Fast text search

---

**Built with ❤️ for developers who need to understand code fast.**
