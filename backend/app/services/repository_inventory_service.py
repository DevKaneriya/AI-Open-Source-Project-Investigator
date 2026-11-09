"""
Repository Inventory

A deterministic, repository-wide scan that answers the broad questions top-k
search cannot: which manifests and framework versions exist, where the
application starts, which HTTP endpoints / client calls / data models /
authentication code / database configuration exist anywhere in the repo.

Every category records the sites it found. Because the scan covers every
runtime source file (it is not a ranked search), an empty category is an
established absence, which lets the evaluator mark a stage as verified absent
("this repository has no backend") instead of perpetually "missing", and lets
the citation validator accept repository-wide negative claims about it.

The scan is cached per repository path and invalidated when files change.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.services import evidence_signals as sig

logger = logging.getLogger(__name__)

MAX_FILES = 5000
MAX_FILE_BYTES = 600_000

_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".angular", ".next",
    ".nuxt", ".svelte-kit", "coverage", ".turbo", ".cache", "target", "vendor", ".idea", ".vscode", "out",
}
_CODE_EXTS = sig.FRONTEND_EXTS + sig.BACKEND_EXTS + (".mjs", ".cjs", ".prisma", ".sql", ".graphql", ".gql", ".php")
_MANIFEST_NAMES = {
    "package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml", "pipfile", "setup.py", "setup.cfg",
    "go.mod", "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "gemfile", "composer.json",
}
_LOCK_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock"}
_CONFIG_NAMES = sig.CONFIG_FILENAMES | {
    "craco.config.js", "vite.config.js", "vite.config.mjs", "webpack.config.js", "next.config.mjs", "next.config.ts",
    "nuxt.config.ts", "svelte.config.js", "tailwind.config.js", "tailwind.config.ts", "postcss.config.js",
    "vercel.json", "netlify.toml", "dockerfile", "docker-compose.yml", "docker-compose.yaml", "jsconfig.json",
    "babel.config.js", ".babelrc", "nest-cli.json", "app.json", "firebase.json", "settings.gradle",
}

# Well-known frameworks / libraries worth naming in an answer (manifest name -> label).
KEY_PACKAGES = {
    # frontend
    "react": "React", "react-dom": "React DOM", "next": "Next.js", "vue": "Vue", "nuxt": "Nuxt",
    "@angular/core": "Angular", "svelte": "Svelte", "@sveltejs/kit": "SvelteKit", "solid-js": "Solid",
    "preact": "Preact", "react-native": "React Native", "expo": "Expo", "astro": "Astro", "gatsby": "Gatsby",
    "react-router-dom": "React Router", "react-router": "React Router", "@tanstack/react-query": "TanStack Query",
    "redux": "Redux", "@reduxjs/toolkit": "Redux Toolkit", "zustand": "Zustand", "mobx": "MobX", "pinia": "Pinia",
    "rxjs": "RxJS", "@ngrx/store": "NgRx", "swr": "SWR", "axios": "Axios",
    "three": "three.js", "@react-three/fiber": "React Three Fiber", "@react-three/drei": "drei",
    "@react-three/postprocessing": "R3F postprocessing", "gsap": "GSAP", "framer-motion": "Framer Motion",
    "tailwindcss": "Tailwind CSS", "bootstrap": "Bootstrap", "@mui/material": "Material UI",
    "styled-components": "styled-components", "react-hook-form": "React Hook Form", "zod": "Zod",
    "typescript": "TypeScript", "vite": "Vite", "webpack": "webpack", "react-scripts": "Create React App (react-scripts)",
    "@craco/craco": "CRACO", "jest": "Jest", "vitest": "Vitest", "electron": "Electron",
    # node backend
    "express": "Express", "@nestjs/core": "NestJS", "fastify": "Fastify", "koa": "Koa", "hapi": "hapi",
    "mongoose": "Mongoose", "prisma": "Prisma", "@prisma/client": "Prisma Client", "sequelize": "Sequelize",
    "typeorm": "TypeORM", "knex": "Knex", "pg": "node-postgres", "mysql2": "mysql2", "sqlite3": "sqlite3",
    "firebase": "Firebase", "@supabase/supabase-js": "Supabase", "next-auth": "NextAuth", "passport": "Passport",
    "jsonwebtoken": "jsonwebtoken", "socket.io": "Socket.IO", "graphql": "GraphQL", "@apollo/server": "Apollo Server",
    # python
    "django": "Django", "djangorestframework": "Django REST framework", "fastapi": "FastAPI", "flask": "Flask",
    "starlette": "Starlette", "sqlalchemy": "SQLAlchemy", "alembic": "Alembic", "pydantic": "Pydantic",
    "celery": "Celery", "uvicorn": "Uvicorn", "gunicorn": "Gunicorn", "streamlit": "Streamlit",
    "langchain": "LangChain", "langgraph": "LangGraph", "torch": "PyTorch", "tensorflow": "TensorFlow",
    "transformers": "Transformers", "pandas": "pandas", "numpy": "NumPy", "scikit-learn": "scikit-learn",
    "psycopg": "psycopg", "psycopg2": "psycopg2", "psycopg2-binary": "psycopg2", "redis": "redis", "groq": "Groq SDK",
    "openai": "OpenAI SDK", "anthropic": "Anthropic SDK", "qdrant-client": "Qdrant client",
    "sentence-transformers": "sentence-transformers", "djangorestframework-simplejwt": "Simple JWT",
    "pytest": "pytest",
}

_TOOLING_PACKAGES = {
    "react-scripts", "@craco/craco", "tailwindcss", "typescript", "vite", "webpack", "jest", "vitest", "postcss",
    "autoprefixer", "cra-template", "tailwindcss-animate", "prettier", "nodemon", "ts-node", "concurrently",
    "web-vitals", "@vitejs/plugin-react", "sass", "less",
}
_JS_IMPORT_RE = re.compile(
    r"""(?:\bfrom\s+|\bimport\s+|\brequire\s*\(\s*|\bimport\s*\(\s*)['"]((?:@[\w.-]+/)?[\w.-]+)[^'"]*['"]"""
)

_BACKEND_PACKAGES = {
    "express", "@nestjs/core", "fastify", "koa", "hapi", "django", "djangorestframework", "fastapi", "flask",
    "starlette", "uvicorn", "gunicorn",
}

# --- detection patterns (per line) -------------------------------------------------

_ENDPOINT_RE = re.compile(
    r"\b(?:app|router|server|api|fastify|routes?|\w+Router)\s*\.\s*(get|post|put|patch|delete|all|options|head)\s*"
    r"\(\s*(['\"`])(/[^'\"`]*)\2",
    re.IGNORECASE,
)
_DECORATOR_ENDPOINT_RE = re.compile(
    r"@\s*[\w.]*\s*\.\s*(get|post|put|patch|delete|route|api_route|websocket)\s*\(\s*(['\"])(.*?)\2", re.IGNORECASE,
)
_NEST_ENDPOINT_RE = re.compile(r"@(Get|Post|Put|Patch|Delete)\s*\(\s*(?:(['\"])(.*?)\2)?\s*\)")
_SPRING_ENDPOINT_RE = re.compile(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\s*\(\s*(?:value\s*=\s*)?(?:(['\"])(.*?)\2)?")
_DJANGO_PATH_RE = re.compile(r"\b(?:re_)?path\(\s*r?(['\"])(.*?)\1\s*,\s*([A-Za-z_][\w.]*)")
_FRONTEND_ROUTE_RE = re.compile(
    r"<Route\b[^>]*\bpath\s*=\s*\{?\s*(['\"`])([^'\"`]*)\1"
    r"|\bpath\s*:\s*(['\"`])([^'\"`]*)\3\s*,\s*(?:component|loadComponent|element|loadChildren|redirectTo|children|Component)\b"
)
_NETWORK_CALL_RE = re.compile(
    r"\bfetch\s*\(|\baxios(?:\s*\.\s*(?:get|post|put|patch|delete|request|create))?\s*\(|\bnew\s+WebSocket\s*\(|"
    r"\bXMLHttpRequest\b|\buseSWR\s*\(|\$\.ajax\s*\(|\bhttp(?:Client)?\s*\.\s*(?:get|post|put|patch|delete|request)\s*[<(]|"
    r"\bio\s*\(\s*['\"`]|\bnew\s+EventSource\s*\(|\bsupabase\s*\.\s*from\s*\(|\bgetDocs?\s*\(|\bonSnapshot\s*\("
    r"|\brequests\.(?:get|post|put|patch|delete|request|Session)\s*\(|\bhttpx\.(?:get|post|AsyncClient|Client)\b"
    r"|\b_?(?:request|send)\s*\(\s*['\"](?:GET|POST|PUT|PATCH|DELETE)['\"]|\burlopen\s*\(|\b_?session\.request\s*\("
)
_ENTRY_RE = re.compile(
    sig._ENTRY_POINT_RE.pattern
    + r"|\bif\s+__name__\s*==\s*['\"]__main__['\"]|\bfunc\s+main\s*\(\s*\)|\bpublic\s+static\s+void\s+main\s*\("
    + r"|\bSpringApplication\.run\s*\(|\bNestFactory\.create\s*\(|\bhydrateRoot\s*\(|\bnew\s+Vue\s*\(|\bst\.set_page_config\s*\(",
)
_MODEL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Django model", re.compile(r"^\s*class\s+(\w+)\s*\([^)]*\bmodels\.Model\b")),
    ("SQLAlchemy model", re.compile(r"^\s*class\s+(\w+)\s*\([^)]*\b(?:Base|DeclarativeBase|db\.Model)\b[^)]*\)\s*:")),
    ("SQLModel table", re.compile(r"^\s*class\s+(\w+)\s*\([^)]*\bSQLModel\b[^)]*table\s*=\s*True")),
    ("Pydantic schema", re.compile(r"^\s*class\s+(\w+)\s*\([^)]*\b(?:BaseModel|BaseSettings)\b")),
    ("Python dataclass", re.compile(r"^\s*@dataclass")),
    ("Mongoose schema", re.compile(r"\b(\w+)\s*=\s*new\s+(?:mongoose\.)?Schema\s*\(|mongoose\.model\s*\(\s*['\"](\w+)")),
    ("Entity (TypeORM/JPA)", re.compile(r"^\s*@Entity\b")),
    ("Sequelize model", re.compile(r"\bsequelize\.define\s*\(\s*['\"](\w+)|class\s+(\w+)\s+extends\s+Model\b")),
    ("Prisma model", re.compile(r"^\s*model\s+(\w+)\s*\{")),
    ("Rails model", re.compile(r"^\s*class\s+(\w+)\s*<\s*(?:ApplicationRecord|ActiveRecord::Base)")),
    ("GORM model", re.compile(r"\bgorm\.Model\b")),
    ("SQL table", re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)", re.IGNORECASE)),
]
_TYPE_DEF_RE = re.compile(
    r"^\s*(?:export\s+)?(?:interface\s+(\w+)|type\s+(\w+)\s*=\s*\{|(?:const|let)\s+(\w+)\s*=\s*z\.object\s*\()"
)
_STATE_STORE_RE = re.compile(
    r"\bcreate\s*\(\s*(?:\(\s*set\b|set\s*=>|\(set,\s*get\))|\bcreateStore\s*\(|\bconfigureStore\s*\(|\bcreateSlice\s*\("
    r"|\bdefineStore\s*\(|\bcreateReducer\s*\(|\bReact\.createContext\s*\(|\bcreateContext\s*\(|\bnew\s+Vuex\.Store\s*\("
    r"|\bmakeAutoObservable\s*\(|\bBehaviorSubject\s*<"
)
_DB_CONFIG_RE = re.compile(
    sig._DB_CONFIG_RE.pattern
    + r"|^\s*datasource\s+\w+\s*\{|\bnew\s+Sequelize\s*\(|\bknex\s*\(\s*\{|\bnew\s+DataSource\s*\(|\bcreateConnection\s*\("
    + r"|\bMongoClient\s*\(|\bsqlite3\.connect\s*\(|\bpsycopg2?\.connect\s*\(|\bspring\.datasource\b|\bgetFirestore\s*\("
    + r"|\bcreateClient\s*\(\s*(?:process\.env\.\w*SUPABASE|['\"]https://\w+\.supabase)|\bnew\s+PrismaClient\s*\(|\bQdrantClient\s*\("
)
_AUTH_IMPL_RE = re.compile(
    sig._AUTH_RE.pattern
    + r"|\blogin_required\b|(?:\b|_)(?:check|verify)_password\s*\(|\bset_password\s*\(|\bbcrypt\b|\bpasslib\b|\bOAuth2PasswordBearer\b"
    + r"|\bhmac\.compare_digest\s*\(|\bADMIN_PASSWORD\b|\b(?:is_admin|is_owner|require_admin|admin_unlocked)\b"
    + r"|\bjwt\.(?:encode|decode|sign|verify)\s*\(|@PreAuthorize\b|\bSecurityFilterChain\b|django\.contrib\.auth\b"
    + r"|AUTHENTICATION_BACKENDS|rest_framework_simplejwt|\bsignInWith\w+\s*\(|firebase/auth|\bnext-auth\b|\buseSession\s*\("
    + r"|@auth0/|\bsupabase\.auth\.|\bBearer\s|\blocalStorage\.setItem\s*\(\s*['\"](?:token|access\w*|jwt|auth\w*)"
    + r"|\b(?:auth|authService|this\.auth)\s*\.\s*(?:login|logout|signIn|signOut|register)\s*\(|\bverify_password\s*\("
    + r"|\bget_current_user\b|\bHTTPBearer\b|\bAPIKeyHeader\b|\bauthMiddleware\b|\brequireAuth\b|\bisAuthenticated\b",
)
_AUTH_MENTION_RE = re.compile(
    r"\b(?:auth|authenticat\w*|authoriz\w*|login|logout|sign[_-]?(?:in|up|out)|password|credentials?)\b", re.IGNORECASE,
)
_AUTH_PATH_RE = re.compile(r"(?:^|[/_.-])(?:auth\w*|login|signin|session)(?:[/_.-]|$)", re.IGNORECASE)
_TOKEN_STORAGE_RE = re.compile(r"localStorage\.setItem\s*\(\s*['\"](?:token|access\w*|jwt|auth\w*)")
_STRING_RE = re.compile(r"(['\"`])(?:\\.|(?!\1).)*\1")
_IMPORT_RE = re.compile(r"^\s*(?:import\b|from\s+\S+\s+import\b|export\s+\*\s+from\b|(?:const|let|var)\s+\w+\s*=\s*require\()")


@dataclass
class InventorySite:
    file: str
    line: int
    label: str
    code: str
    kind: str = ""

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "label": self.label, "code": self.code[:160], "kind": self.kind}


@dataclass
class Manifest:
    file: str
    kind: str
    dependencies: dict[str, str] = field(default_factory=dict)
    dev_dependencies: dict[str, str] = field(default_factory=dict)
    locked: dict[str, str] = field(default_factory=dict)
    lockfile: str | None = None
    scripts: dict[str, str] = field(default_factory=dict)
    lines: dict[str, int] = field(default_factory=dict)
    total_lines: int = 0


@dataclass
class RepositoryInventory:
    root: str
    files_scanned: int = 0
    complete: bool = True
    file_paths: list[str] = field(default_factory=list)
    manifests: list[Manifest] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    readme: str | None = None
    sites: dict[str, list[InventorySite]] = field(default_factory=dict)
    frontend_files: int = 0
    backend_files: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    imported_packages: set[str] = field(default_factory=set)

    def unused_packages(self) -> list[tuple[str, str]]:
        """(package, manifest) for declared npm runtime packages that no scanned file imports."""
        out = []
        for m in self.manifests:
            if m.kind != "npm":
                continue
            scripts = " ".join(m.scripts.values())
            for name in m.dependencies:
                low = name.lower()
                if low in _TOOLING_PACKAGES or low.startswith(("@types/", "eslint", "@babel/")) or low in scripts:
                    continue
                if low not in self.imported_packages:
                    out.append((name, m.file))
        return out

    CATEGORIES = (
        "frontend_entry", "backend_entry", "endpoints", "frontend_routes", "http_calls", "data_models",
        "type_definitions", "state_stores", "db_config", "db_operations", "auth", "auth_mentions",
    )

    def get(self, category: str) -> list[InventorySite]:
        return self.sites.get(category, [])

    def has(self, category: str) -> bool:
        return bool(self.sites.get(category))

    @property
    def app_endpoints(self) -> list[InventorySite]:
        return [s for s in self.get("endpoints") if s.kind != "dev-server"]

    @property
    def has_backend(self) -> bool:
        return bool(self.app_endpoints or self.get("backend_entry") or self.backend_files)

    @property
    def has_persistence(self) -> bool:
        return bool(self.get("db_config") or self.get("db_operations") or self.persistent_models)

    @property
    def persistent_models(self) -> list[InventorySite]:
        return [s for s in self.get("data_models") if s.kind not in ("Pydantic schema", "Python dataclass")]

    def key_packages(self) -> list[tuple[str, str, str, str, Manifest]]:
        """(package, label, declared, locked) for every well-known package across manifests."""
        out = []
        for manifest in self.manifests:
            for deps in (manifest.dependencies, manifest.dev_dependencies):
                for name, declared in deps.items():
                    if name.lower() in KEY_PACKAGES:
                        out.append((name, KEY_PACKAGES[name.lower()], declared, manifest.locked.get(name, ""), manifest))
        return out

    def to_coverage(self) -> dict:
        """Compact, JSON-safe record stored in the investigation's search coverage."""
        return {
            "complete": self.complete,
            "files_scanned": self.files_scanned,
            "frontend_files": self.frontend_files,
            "backend_files": self.backend_files,
            "has_backend": self.has_backend,
            "has_persistence": self.has_persistence,
            "manifests": [m.file for m in self.manifests],
            "config_files": self.config_files[:20],
            "readme": self.readme,
            "packages": [
                {
                    "name": name, "label": label, "declared": declared, "locked": locked,
                    "manifest": manifest.file, "line": manifest.lines.get(name, 1), "lockfile": manifest.lockfile,
                    "dev": name in manifest.dev_dependencies,
                }
                for name, label, declared, locked, manifest in self.key_packages()
            ],
            "unused_packages": [{"name": n, "manifest": f} for n, f in self.unused_packages()][:40],
            "manifest_summaries": [
                {"file": m.file, "kind": m.kind, "dependencies": len(m.dependencies),
                 "dev_dependencies": len(m.dev_dependencies), "lockfile": m.lockfile,
                 "scripts": dict(list(m.scripts.items())[:6])}
                for m in self.manifests
            ],
            "counts": {c: len(self.get(c)) for c in self.CATEGORIES},
            "sites": {c: [s.to_dict() for s in self.get(c)[:25]] for c in self.CATEGORIES},
        }


class RepositoryInventoryService:
    """Builds (and caches) a RepositoryInventory for a repository checkout."""

    _cache: dict[str, tuple[float, str, RepositoryInventory]] = {}
    _lock = threading.Lock()
    CACHE_SECONDS = 600

    def scan(self, repository_path: str) -> RepositoryInventory:
        root = Path(repository_path).resolve()
        key = str(root)
        files = self._walk(root)
        signature = f"{len(files)}:{max((m for _, m in files), default=0):.0f}"
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[1] == signature and time.time() - cached[0] < self.CACHE_SECONDS:
                return cached[2]
        inventory = self._build(root, files)
        with self._lock:
            self._cache[key] = (time.time(), signature, inventory)
        return inventory

    # ------------------------------------------------------------------ walk

    @staticmethod
    def _walk(root: Path) -> list[tuple[Path, float]]:
        found: list[tuple[Path, float]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".") or d == ".github")
            for name in sorted(filenames):
                low = name.lower()
                if not (low.endswith(_CODE_EXTS) or low in _MANIFEST_NAMES or low in _LOCK_NAMES
                        or low in _CONFIG_NAMES or low.startswith("readme")):
                    continue
                path = Path(dirpath) / name
                try:
                    found.append((path, path.stat().st_mtime))
                except OSError:
                    continue
                if len(found) >= MAX_FILES:
                    return found
        return found

    # ----------------------------------------------------------------- build

    def _build(self, root: Path, files: list[tuple[Path, float]]) -> RepositoryInventory:
        inv = RepositoryInventory(root=str(root), complete=len(files) < MAX_FILES)
        inv.sites = {c: [] for c in RepositoryInventory.CATEGORIES}
        lockfiles: dict[str, Path] = {}

        for path, _ in files:
            rel = path.relative_to(root).as_posix()
            low = path.name.lower()
            inv.file_paths.append(rel)
            if low in _LOCK_NAMES:
                lockfiles[str(Path(rel).parent.as_posix())] = path
                continue
            if low.startswith("readme") and (inv.readme is None or rel.count("/") < inv.readme.count("/")):
                inv.readme = rel
            if low in _MANIFEST_NAMES:
                manifest = self._parse_manifest(path, rel)
                if manifest:
                    inv.manifests.append(manifest)
                if not low.endswith(_CODE_EXTS):
                    continue
            if low in _CONFIG_NAMES or sig.file_role(rel) == sig.ROLE_CONFIG:
                inv.config_files.append(rel)
            if not low.endswith(_CODE_EXTS):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    inv.complete = False
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            inv.files_scanned += 1
            ext = path.suffix.lower()
            inv.languages[ext] = inv.languages.get(ext, 0) + 1
            if sig.is_indirect(rel):
                continue
            if sig.is_frontend_runtime(rel):
                inv.frontend_files += 1
            elif sig.is_backend_runtime(rel) and ext != ".sql":
                inv.backend_files += 1
            if not rel.endswith(".py"):
                inv.imported_packages |= {m.lower() for m in _JS_IMPORT_RE.findall(text)}
            self._scan_file(inv, rel, text)

        for manifest in inv.manifests:
            folder = str(Path(manifest.file).parent.as_posix())
            lock = lockfiles.get(folder)
            if lock and manifest.kind == "npm" and lock.name.lower() == "package-lock.json":
                manifest.lockfile = Path(lock).relative_to(root).as_posix()
                manifest.locked = self._npm_locked_versions(lock, set(manifest.dependencies) | set(manifest.dev_dependencies))
        # A backend framework declared in a manifest counts as backend code
        # only through the files that use it; declared-only is reported in notes.
        return inv

    def _scan_file(self, inv: RepositoryInventory, rel: str, text: str) -> None:
        lines = text.split("\n")
        frontend = sig.is_frontend_runtime(rel)
        dev_tooling = bool(re.search(r"devServer|webpack|setupMiddlewares", text)) and frontend
        add = inv.sites.setdefault

        seen: set[tuple[str, int, str]] = set()

        def site(category: str, number: int, label: str, kind: str = "") -> None:
            bucket = add(category, [])
            if len(bucket) < 400 and (category, number, label) not in seen:
                seen.add((category, number, label))
                bucket.append(InventorySite(rel, number, label, lines[number - 1].strip(), kind))

        name = rel.rsplit("/", 1)[-1].lower()
        if name in ("manage.py", "wsgi.py", "asgi.py"):
            site("backend_entry", 1, name)

        model_next_class = None
        python = rel.endswith(".py")
        in_docstring = False
        for number, line in enumerate(lines, start=1):
            if len(line) > 600:
                continue
            stripped = line.strip()
            if python and (stripped.count('"""') + stripped.count("'''")) % 2 == 1:
                in_docstring = not in_docstring
                continue
            if in_docstring or not stripped or stripped.startswith(("//", "#", "*", "/*")):
                continue
            # Signals are matched on code with string literals blanked, so
            # patterns quoted in strings/regexes/messages do not count.
            code = _STRING_RE.sub('""', line)
            is_import = bool(_IMPORT_RE.search(line))

            if _ENTRY_RE.search(code):
                site("frontend_entry" if frontend else "backend_entry", number, stripped[:80])

            for match in _ENDPOINT_RE.finditer(line):
                site("endpoints", number, f"{match.group(1).upper()} {match.group(3)}", "dev-server" if dev_tooling else "")
            for match in _DECORATOR_ENDPOINT_RE.finditer(line):
                method = match.group(1).upper()
                site("endpoints", number, f"{'ANY' if method in ('ROUTE', 'API_ROUTE') else method} {match.group(3)}")
            if not frontend:
                for match in _NEST_ENDPOINT_RE.finditer(line):
                    site("endpoints", number, f"{match.group(1).upper()} {match.group(3) or '/'}")
                for match in _SPRING_ENDPOINT_RE.finditer(line):
                    site("endpoints", number, f"{match.group(1).upper()} {match.group(3) or '/'}")
                for match in _DJANGO_PATH_RE.finditer(line):
                    handler = match.group(3).split(".")
                    if handler[-1] in ("as_view", "urls"):
                        handler = handler[:-1]
                    if handler and handler[-1] not in ("include", "admin", "site"):
                        site("endpoints", number, f"/{match.group(2).lstrip('^/')} -> {handler[-1]}")
                    elif handler and handler[-1] == "include":
                        site("endpoints", number, f"/{match.group(2).lstrip('^/')} (include)", "include")
            if frontend:
                for match in _FRONTEND_ROUTE_RE.finditer(line):
                    site("frontend_routes", number, match.group(2) if match.group(2) is not None else match.group(4))
                if _NETWORK_CALL_RE.search(code):
                    site("http_calls", number, stripped[:80])
            elif _NETWORK_CALL_RE.search(code):
                site("http_calls", number, stripped[:80], "outbound")

            for kind, pattern in _MODEL_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                if kind in ("Entity (TypeORM/JPA)", "Python dataclass"):
                    model_next_class = (kind, number)
                    break
                label = next((g for g in match.groups() if g), None) or stripped[:60]
                if label not in ("Base", "Model"):
                    site("data_models", number, label, kind)
                break
            else:
                if model_next_class and number - model_next_class[1] <= 4:
                    cls = re.search(r"\bclass\s+(\w+)", line)
                    if cls:
                        site("data_models", model_next_class[1], cls.group(1), model_next_class[0])
                        model_next_class = None

            if frontend or rel.endswith((".ts", ".tsx")):
                match = _TYPE_DEF_RE.search(line)
                if match:
                    site("type_definitions", number, next(g for g in match.groups() if g))
            if _STATE_STORE_RE.search(code):
                site("state_stores", number, stripped[:80],
                     "context" if re.search(r"createContext\s*\(|BehaviorSubject", code) else "store")
            if _DB_CONFIG_RE.search(code) or (re.search(r"\bDATABASE_URL\b|\bDATABASES\b", line) and "environ" in line):
                site("db_config", number, stripped[:80])
            if not frontend and (sig.orm_writes(code) or sig.orm_reads(code) or sig.has_raw_sql(line)):
                site("db_operations", number, stripped[:80])
            if is_import:
                continue
            if _AUTH_IMPL_RE.search(code) or _TOKEN_STORAGE_RE.search(line):
                site("auth", number, stripped[:80], "frontend" if frontend else "backend")
            elif len(inv.sites.get("auth_mentions", [])) < 60 and _AUTH_MENTION_RE.search(line) and (
                _AUTH_PATH_RE.search(rel) or _AUTH_MENTION_RE.search(code)
            ):
                site("auth_mentions", number, stripped[:80])

    # ------------------------------------------------------------- manifests

    @staticmethod
    def _parse_manifest(path: Path, rel: str) -> Manifest | None:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        lines = text.split("\n")
        name = path.name.lower()

        def line_of(pkg: str) -> int:
            pat = re.compile(rf"(?<![\w@/-]){re.escape(pkg)}(?![\w/-])", re.IGNORECASE)
            for idx, line in enumerate(lines, start=1):
                if pat.search(line):
                    return idx
            return 1

        manifest = Manifest(file=rel, kind="other", total_lines=len(lines))
        try:
            if name == "package.json":
                data = json.loads(text)
                manifest.kind = "npm"
                manifest.dependencies = {k: str(v) for k, v in (data.get("dependencies") or {}).items()}
                manifest.dev_dependencies = {
                    k: str(v) for k, v in {**(data.get("devDependencies") or {}), **(data.get("peerDependencies") or {})}.items()
                }
                manifest.scripts = {k: str(v) for k, v in (data.get("scripts") or {}).items()}
            elif name.startswith("requirements") and name.endswith(".txt"):
                manifest.kind = "pip"
                for line in lines:
                    m = re.match(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*((?:[=<>!~]=?|===)\s*[^;#\s]+(?:\s*,\s*[=<>!~]=?\s*[^;#\s,]+)*)?", line)
                    if m and not line.strip().startswith(("#", "-")):
                        manifest.dependencies[re.sub(r"\[.*\]", "", m.group(1)).lower()] = (m.group(2) or "").replace(" ", "") or "(unpinned)"
            elif name == "pyproject.toml":
                import tomllib
                data = tomllib.loads(text)
                manifest.kind = "python"
                for dep in (data.get("project") or {}).get("dependencies") or []:
                    m = re.match(r"^\s*([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(.*)$", dep)
                    if m:
                        manifest.dependencies[m.group(1).lower()] = m.group(2).strip() or "(unpinned)"
                poetry = ((data.get("tool") or {}).get("poetry") or {})
                for k, v in (poetry.get("dependencies") or {}).items():
                    if k.lower() != "python":
                        manifest.dependencies[k.lower()] = v if isinstance(v, str) else str(v.get("version", v))
            elif name == "go.mod":
                manifest.kind = "go"
                for m in re.finditer(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-/]+)\s+(v[\w.\-+]+)", text, re.MULTILINE):
                    manifest.dependencies[m.group(1)] = m.group(2)
            elif name == "cargo.toml":
                import tomllib
                data = tomllib.loads(text)
                manifest.kind = "cargo"
                for k, v in (data.get("dependencies") or {}).items():
                    manifest.dependencies[k] = v if isinstance(v, str) else str(v.get("version", ""))
            elif name == "composer.json":
                data = json.loads(text)
                manifest.kind = "composer"
                manifest.dependencies = {k: str(v) for k, v in (data.get("require") or {}).items()}
            elif name == "gemfile":
                manifest.kind = "bundler"
                for m in re.finditer(r"^\s*gem\s+['\"]([\w\-]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", text, re.MULTILINE):
                    manifest.dependencies[m.group(1)] = m.group(2) or "(unpinned)"
            elif name == "pom.xml":
                manifest.kind = "maven"
                for m in re.finditer(r"<artifactId>([^<]+)</artifactId>\s*(?:<version>([^<]+)</version>)?", text):
                    manifest.dependencies[m.group(1)] = m.group(2) or "(managed)"
            elif name.startswith("build.gradle"):
                manifest.kind = "gradle"
                for m in re.finditer(r"(?:implementation|api|compile)\s*\(?\s*['\"]([\w.\-]+):([\w.\-]+):([\w.\-]+)['\"]", text):
                    manifest.dependencies[m.group(2)] = m.group(3)
            else:
                return None
        except Exception as exc:  # malformed manifest: keep the file, without parsed deps
            logger.info("Could not parse manifest %s: %s", rel, exc)
        for pkg in list(manifest.dependencies) + list(manifest.dev_dependencies):
            manifest.lines[pkg] = line_of(pkg)
        return manifest

    @staticmethod
    def _npm_locked_versions(lock: Path, wanted: set[str]) -> dict[str, str]:
        try:
            if lock.stat().st_size > 25_000_000:
                return {}
            data = json.loads(lock.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            return {}
        locked = {}
        packages = data.get("packages") or {}
        for name in wanted:
            entry = packages.get(f"node_modules/{name}") or (data.get("dependencies") or {}).get(name)
            if isinstance(entry, dict) and entry.get("version"):
                locked[name] = str(entry["version"])
        return locked

    @staticmethod
    def npm_lock_lines(lock: Path, names: list[str]) -> dict[str, int]:
        """Line of ``"node_modules/<name>": {`` (or legacy ``"<name>": {``) for each name in a package-lock."""
        try:
            lines = lock.read_text(encoding="utf-8", errors="ignore").split("\n")
        except OSError:
            return {}
        wanted = {f'"node_modules/{n}": {{': n for n in names}
        found: dict[str, int] = {}
        for number, line in enumerate(lines, start=1):
            key = line.strip()
            if key in wanted and wanted[key] not in found:
                found[wanted[key]] = number
                if len(found) == len(wanted):
                    break
        return found


# ---------------------------------------------------------------------------
# Evidence windows
# ---------------------------------------------------------------------------

def _read_lines(root: str, rel: str) -> list[str]:
    try:
        return (Path(root) / rel).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []


def window(root: str, rel: str, start: int, end: int, label: str, category: str) -> dict | None:
    lines = _read_lines(root, rel)
    if not lines:
        return None
    start = max(1, start)
    end = min(len(lines), max(start, end))
    return {
        "file": rel,
        "start_line": start,
        "end_line": end,
        "content": "\n".join(lines[start - 1:end]),
        "symbol": label,
        "evidence_score": 1.3,
        "inventory_category": category,
        "traversal": f"repository inventory: {category}",
    }


def block_end(lines: list[str], start: int, cap: int = 45) -> int:
    """Last line (1-based) of the definition starting at *start*: indentation for Python, braces otherwise."""
    if not 1 <= start <= len(lines):
        return start
    head = lines[start - 1]
    if head.rstrip().endswith(":") or re.match(r"^\s*(?:@|class\s|def\s|async\s+def\s)", head):
        # Python: skip decorators to the def/class line, then follow the indented body.
        idx = start - 1
        while idx < len(lines) - 1 and lines[idx].lstrip().startswith("@"):
            # Multi-line decorators: skip to the line that closes them.
            while idx < len(lines) - 1 and lines[idx].count("(") > lines[idx].count(")") and not lines[idx + 1].lstrip().startswith(("def ", "async ", "class ", "@")):
                idx += 1
            idx += 1
        indent = len(lines[idx]) - len(lines[idx].lstrip())
        # A multi-line signature ends at the line closing with ':'.
        sig_end = idx
        while sig_end < min(len(lines) - 1, idx + 20) and not lines[sig_end].split("#")[0].rstrip().endswith(":"):
            sig_end += 1
        end = sig_end + 1
        for j in range(sig_end + 1, min(len(lines), start - 1 + cap)):
            text = lines[j]
            if text.strip() and len(text) - len(text.lstrip()) <= indent:
                break
            end = j + 1
        while end > start and not lines[end - 1].strip():
            end -= 1
        return end
    depth, opened = 0, False
    for j in range(start - 1, min(len(lines), start - 1 + cap)):
        depth += lines[j].count("{") - lines[j].count("}")
        opened = opened or "{" in lines[j]
        if opened and depth <= 0:
            return j + 1
    return min(len(lines), start - 1 + cap)


def definition_windows(inv: RepositoryInventory, category: str, max_windows: int = 8, keep=None,
                       cap: int = 40) -> list[dict]:
    """One window per definition site spanning the whole class/model body (bounded)."""
    windows = []
    for s in [s for s in inv.get(category) if keep is None or keep(s)][:max_windows]:
        lines = _read_lines(inv.root, s.file)
        end = block_end(lines, s.line, cap)
        w = window(inv.root, s.file, s.line, max(end, s.line + 2), s.label, category)
        if w:
            windows.append(w)
    return windows


def generic_flow_windows(inv: RepositoryInventory, question: str) -> list[dict]:
    """Seed evidence for a generic "trace a request" question: one representative
    client request, the UI code that triggers it, and the backend route handler."""
    calls = []
    for rel in dict.fromkeys(s.file for s in inv.get("http_calls") if s.kind != "outbound"):
        text = "\n".join(_read_lines(inv.root, rel))
        for call in sig.extract_http_calls(text):
            calls.append((rel, text, call))
    if not calls:
        return []
    chosen = sig.rank_http_calls([c for _, _, c in calls], set(), question)
    if not chosen:
        return []
    call = chosen[0]
    rel, text, _ = next(c for c in calls if c[2] is call)
    windows: list[dict] = []
    lines = text.split("\n")
    call_line = text[: call["position"]].count("\n") + 1
    method = call.get("enclosing")
    start = call_line
    if method:
        for idx in range(call_line - 1, -1, -1):
            if re.match(rf"^\s*(?:async\s+)?(?:def\s+)?{re.escape(method)}\s*\(", lines[idx]) or re.match(
                rf"^\s*(?:public\s+|private\s+|async\s+)*{re.escape(method)}\s*\(", lines[idx]
            ):
                start = idx + 1
                break
    w = window(inv.root, rel, start, max(block_end(lines, start, 40), call_line + 3), method or "client call", "client_request")
    if w:
        windows.append(w)

    # UI code calling the client method.
    if method:
        caller_re = re.compile(rf"\.\s*{re.escape(method)}\s*\(")
        for other in inv.file_paths:
            if other == rel or not sig.is_frontend_runtime(other) or sig.is_indirect(other):
                continue
            other_lines = _read_lines(inv.root, other)
            hit = next((i for i, l in enumerate(other_lines, start=1) if caller_re.search(l)), None)
            if hit:
                w = window(inv.root, other, max(1, hit - 12), hit + 12, f"calls {method}", "ui_caller")
                if w:
                    windows.append(w)
                break

    # Backend route + handler for the request.
    for site in inv.get("endpoints"):
        label_method, _, path = site.label.partition(" ")
        route_segments = sig.normalize_route_path(path.split(" ->")[0])
        if label_method.lower() not in (call["method"], "any") or not route_segments:
            continue
        if sig.url_matches(call["segments"], route_segments) and len(route_segments) == len(call["segments"]):
            route_lines = _read_lines(inv.root, site.file)
            end = block_end(route_lines, site.line, 40)
            w = window(inv.root, site.file, site.line, max(end, site.line + 3), site.label, "route_handler")
            if w:
                windows.append(w)
            break
    return windows


def site_windows(inv: RepositoryInventory, category: str, max_windows: int = 6, before: int = 2, after: int = 10,
                 max_span: int = 45, keep=None) -> list[dict]:
    """Merge a category's sites into compact per-file windows (most sites first)."""
    if max_windows <= 0:
        return []
    by_file: dict[str, list[InventorySite]] = {}
    for s in inv.get(category):
        if keep is None or keep(s):
            by_file.setdefault(s.file, []).append(s)
    ordered = sorted(by_file.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    windows: list[dict] = []
    for rel, sites in ordered:
        sites.sort(key=lambda s: s.line)
        group = [sites[0]]
        groups = []
        for s in sites[1:]:
            if s.line - group[0].line <= max_span - after:
                group.append(s)
            else:
                groups.append(group)
                group = [s]
        groups.append(group)
        for g in groups:
            labels = ", ".join(dict.fromkeys(s.label for s in g))[:120]
            w = window(inv.root, rel, g[0].line - before, g[-1].line + after, labels, category)
            if w:
                windows.append(w)
            if len(windows) >= max_windows:
                return windows
    return windows


def manifest_windows(inv: RepositoryInventory, max_windows: int = 4, max_chars: int = 1450) -> list[dict]:
    """Manifests (dependency section) ordered by how many well-known packages they declare."""
    scored = sorted(
        inv.manifests,
        key=lambda m: (-sum(1 for p in list(m.dependencies) + list(m.dev_dependencies) if p.lower() in KEY_PACKAGES),
                       m.file.count("/"), m.file),
    )
    # Manifests are split into windows that each fit the answer prompt's
    # per-snippet budget, so every declared version is visible and citable.
    windows = []
    for m in scored:
        lines = _read_lines(inv.root, m.file)[:400]
        start, size = 1, 0
        for number, line in enumerate(lines, start=1):
            size += len(line) + 1
            if size > max_chars and number > start:
                w = window(inv.root, m.file, start, number - 1, m.file.rsplit("/", 1)[-1], "manifests")
                if w:
                    windows.append(w)
                start, size = number, len(line) + 1
        if start <= len(lines):
            w = window(inv.root, m.file, start, len(lines), m.file.rsplit("/", 1)[-1], "manifests")
            if w:
                windows.append(w)
        if len(windows) >= max_windows:
            break
    return windows[:max_windows]


_PRIMARY_PACKAGES = (
    "next", "react", "vue", "nuxt", "@angular/core", "svelte", "@sveltejs/kit", "react-native", "express", "@nestjs/core",
    "fastify", "three", "@react-three/fiber", "typescript", "tailwindcss", "vite", "react-scripts",
)


def lockfile_windows(inv: RepositoryInventory, max_windows: int = 3) -> list[dict]:
    """Small package-lock windows showing the exact installed version of the primary frameworks."""
    windows = []
    for m in inv.manifests:
        if not m.lockfile or not m.locked:
            continue
        names = [p for p in _PRIMARY_PACKAGES if p in m.locked][:max_windows]
        lock_path = Path(inv.root) / m.lockfile
        for name, line in RepositoryInventoryService.npm_lock_lines(lock_path, names).items():
            w = window(inv.root, m.lockfile, line, line + 2, f"{name} {m.locked[name]} (locked)", "lockfile")
            if w:
                windows.append(w)
        if len(windows) >= max_windows:
            break
    return windows[:max_windows]


def config_windows(inv: RepositoryInventory, max_windows: int = 3) -> list[dict]:
    windows = []
    for rel in sorted(inv.config_files, key=lambda r: (r.count("/"), r))[:max_windows]:
        w = window(inv.root, rel, 1, 60, rel.rsplit("/", 1)[-1], "configuration")
        if w:
            windows.append(w)
    return windows


_DEFAULT_IMPORT_RE = re.compile(r"""^\s*import\s+(\w+)\s+from\s+['"]([^'"]+)['"]""", re.MULTILINE)
_RESOLVE_EXTS = ("", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", "/index.js", "/index.jsx", "/index.ts", "/index.tsx")


def _resolve_import(inv: RepositoryInventory, importer: str, spec: str) -> str | None:
    """Resolve a relative or ``@/``/``~/`` aliased import to a repository file."""
    known = set(inv.file_paths)
    base_dir = Path(importer).parent.as_posix()
    if spec.startswith("."):
        candidates = [os.path.normpath(f"{base_dir}/{spec}").replace("\\", "/")]
    elif spec.startswith(("@/", "~/")):
        parts = base_dir.split("/")
        roots = ["/".join(parts[: i + 1]) for i, p in enumerate(parts) if p == "src"] or [base_dir]
        candidates = [f"{roots[-1]}/{spec[2:]}"]
    else:
        return None
    for cand in candidates:
        for ext in _RESOLVE_EXTS:
            if cand + ext in known:
                return cand + ext
    return None


def root_component_files(inv: RepositoryInventory, depth: int = 2) -> list[str]:
    """Files rendered from the frontend entry point: entry -> App -> its top-level component(s)."""
    entries = [s.file for s in inv.get("frontend_entry")][:1]
    chain: list[str] = []
    frontier = entries
    for _ in range(depth):
        nxt = []
        for rel in frontier:
            text = "\n".join(_read_lines(inv.root, rel))
            for name, spec in _DEFAULT_IMPORT_RE.findall(text):
                if not re.search(rf"<{name}\b", text):
                    continue
                target = _resolve_import(inv, rel, spec)
                if target and target not in chain and target not in entries:
                    chain.append(target)
                    nxt.append(target)
        frontier = nxt[:2]
    return chain[:3]


def component_windows(inv: RepositoryInventory, max_windows: int = 2) -> list[dict]:
    windows = []
    for rel in root_component_files(inv)[:max_windows]:
        w = window(inv.root, rel, 1, 45, rel.rsplit("/", 1)[-1], "root_component")
        if w:
            windows.append(w)
    return windows


def readme_window(inv: RepositoryInventory) -> list[dict]:
    if not inv.readme:
        return []
    w = window(inv.root, inv.readme, 1, 60, "README", "readme")
    return [w] if w else []
