"""
Evidence Signals

Deterministic, convention-based classification of code evidence.

Shared by evidence ranking, evidence evaluation and citation validation so that
all three agree on what a piece of evidence actually demonstrates:

- file roles (runtime frontend/backend code vs. tests, admin registrations,
  migrations, generated bundles and documentation)
- frontend HTTP calls and backend URL route registrations, and whether they match
- backend ORM / SQL operations vs. client-side storage (localStorage etc.)
- UI event handlers, service-method calls, response handling
- code identifiers mentioned in natural-language claims
"""

import re

# ---------------------------------------------------------------------------
# File roles
# ---------------------------------------------------------------------------

ROLE_TEST = "test"
ROLE_ADMIN = "admin"
ROLE_MIGRATION = "migration"
ROLE_GENERATED = "generated"
ROLE_DOCUMENTATION = "documentation"
ROLE_CONFIG = "config"
ROLE_FRONTEND = "frontend"
ROLE_BACKEND = "backend"
ROLE_OTHER = "other"

# Roles that never prove runtime behaviour.
INDIRECT_ROLES = {
    ROLE_TEST,
    ROLE_ADMIN,
    ROLE_MIGRATION,
    ROLE_GENERATED,
    ROLE_DOCUMENTATION,
}

_TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|fixtures)/"
    r"|(^|/)test_[^/]*$"
    r"|_test\.[a-z]+$"
    r"|(^|/)tests?\.py$"
    r"|\.(spec|test)\.[jt]sx?$",
    re.IGNORECASE,
)
_ADMIN_RE = re.compile(r"(^|/)admin\.py$", re.IGNORECASE)
_MIGRATION_RE = re.compile(r"(^|/)(migrations|alembic/versions)/", re.IGNORECASE)
_GENERATED_DIR_RE = re.compile(
    r"(^|/)(dist|build|public|\.angular|node_modules|backups?)/",
    re.IGNORECASE,
)
# Hashed bundle names such as main-HIOYHFQA.js (case-sensitive on purpose).
_HASHED_BUNDLE_RE = re.compile(r"[\w-]+[-.][A-Z0-9]{6,}\.js$|\.[a-f0-9]{8,}\.js$")
_DOC_RE = re.compile(r"\.(md|rst|adoc)$|(^|/)docs?/|(?<!requirements)\.txt$", re.IGNORECASE)

CONFIG_FILENAMES = {
    "settings.py", "config.py", "configuration.py", ".env.example",
    "application.yml", "application.properties", "appsettings.json",
    "angular.json", "tsconfig.json", "vite.config.ts", "next.config.js",
    "app.config.ts", "environment.ts", "environment.prod.ts",
}

FRONTEND_EXTS = (".ts", ".tsx", ".js", ".jsx", ".vue", ".svelte", ".html")
BACKEND_EXTS = (".py", ".java", ".go", ".rb", ".php", ".cs", ".kt", ".rs")
_SERVER_DIRS = {"backend", "server", "api", "functions"}
_CLIENT_DIRS = {"frontend", "client", "web", "app", "src"}


def normalize_path(path: str | None) -> str:
    return (path or "").replace("\\", "/")


def file_role(path: str | None) -> str:
    """Classify a repository file path by the role it plays in the project."""
    norm = normalize_path(path)
    low = norm.lower()
    name = low.rsplit("/", 1)[-1]

    if not low:
        return ROLE_OTHER
    if _MIGRATION_RE.search(low):
        return ROLE_MIGRATION
    if _TEST_RE.search(low):
        return ROLE_TEST
    if _ADMIN_RE.search(low):
        return ROLE_ADMIN
    if _GENERATED_DIR_RE.search(low) or _HASHED_BUNDLE_RE.search(norm) or ".min." in name:
        return ROLE_GENERATED
    if _DOC_RE.search(low) and not name.startswith("requirements"):
        return ROLE_DOCUMENTATION
    if name in CONFIG_FILENAMES or "/environments/" in low:
        return ROLE_CONFIG
    if name.endswith(BACKEND_EXTS):
        return ROLE_BACKEND
    if name.endswith(FRONTEND_EXTS):
        dirs = set(low.split("/")[:-1])
        if dirs & _SERVER_DIRS and not dirs & {"frontend", "client", "web"}:
            return ROLE_BACKEND
        return ROLE_FRONTEND
    return ROLE_OTHER


def is_indirect(path: str | None) -> bool:
    return file_role(path) in INDIRECT_ROLES


def is_backend_runtime(path: str | None) -> bool:
    role = file_role(path)
    return role == ROLE_BACKEND or (role == ROLE_CONFIG and normalize_path(path).lower().endswith(".py"))


def is_frontend_runtime(path: str | None) -> bool:
    return file_role(path) == ROLE_FRONTEND


def evidence_text(item: dict) -> str:
    return str(item.get("snippet") or item.get("content") or item.get("source") or item.get("text") or "")


# ---------------------------------------------------------------------------
# HTTP calls (client side) and URL routes (server side)
# ---------------------------------------------------------------------------

_HTTP_CALL_RE = re.compile(
    r"\b(?:http\w*|axios|api\w*|client)\s*\.\s*(get|post|put|patch|delete)\s*"
    r"(?:<[^()]*?>)?\s*\(\s*([`'\"])([^`'\"]*)\2",
    re.IGNORECASE,
)
_FETCH_RE = re.compile(r"\bfetch\s*\(\s*([`'\"])([^`'\"]*)\1", re.IGNORECASE)
_FETCH_METHOD_RE = re.compile(r"method\s*:\s*['\"](\w+)['\"]", re.IGNORECASE)

_DJANGO_ROUTE_RE = re.compile(
    r"\b(?:re_)?path\(\s*r?(['\"])(.*?)\1\s*,\s*([A-Za-z_][\w.]*)"
)
_EXPRESS_ROUTE_RE = re.compile(
    r"\b(?:app|router|api|server)\s*\.\s*(get|post|put|patch|delete|all)\s*\(\s*"
    r"(['\"`])(.*?)\2\s*,\s*(?:[\w.]+\s*,\s*)*([A-Za-z_][\w.]*)",
    re.IGNORECASE,
)
_DECORATOR_ROUTE_RE = re.compile(
    r"@\s*\w+\s*\.\s*(get|post|put|patch|delete|route|api_route)\s*\(\s*(['\"])(.*?)\2"
    r"[^\n]*\n(?:\s*@[^\n]*\n)*\s*(?:async\s+)?def\s+(\w+)",
    re.IGNORECASE,
)


def _path_segments(path: str) -> list[str]:
    return [seg for seg in path.strip("/").split("/") if seg]


def normalize_client_path(raw: str) -> list[str]:
    """Turn a client URL expression into comparable path segments.

    ``${environment.apiUrl}/accounts/cart/add/`` -> ``["accounts", "cart", "add"]``
    """
    value = re.sub(r"\$\{[^}]*\}", "{}", raw or "")
    value = re.sub(r"^[a-z]+://[^/]+", "", value, flags=re.IGNORECASE)
    value = value.split("?", 1)[0]
    segments = []
    for seg in _path_segments(value):
        if seg == "{}" and not segments:
            continue  # leading base-URL interpolation
        segments.append("*" if "{}" in seg or seg.startswith(":") else seg.lower())
    return segments


def normalize_route_path(raw: str) -> list[str]:
    value = (raw or "").strip().lstrip("^").rstrip("$")
    value = re.sub(r"\(\?P<\w+>[^)]*\)", "*", value)
    value = re.sub(r"<[^>]+>", "*", value)
    value = re.sub(r"\{[^}]+\}", "*", value)
    return [("*" if seg.startswith(":") else seg.lower()) for seg in _path_segments(value)]


_METHOD_HEADER_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|async|override)\s+)*([A-Za-z_]\w*)\s*\([^()]*\)\s*(?::[^{=;]*)?\{\s*$"
    r"|^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)
_CONTROL_WORDS = {"if", "for", "while", "switch", "catch", "function", "return", "constructor", "else"}


def enclosing_method(content: str, position: int) -> str | None:
    """Name of the last method/function header before *position* in *content*."""
    name = None
    for match in _METHOD_HEADER_RE.finditer(content[:position]):
        candidate = match.group(1) or match.group(2)
        if candidate and candidate not in _CONTROL_WORDS:
            name = candidate
    return name


def _call_context(content: str, start: int, end: int) -> str:
    """Code around an HTTP call: from its enclosing method header to the call's arguments."""
    before = content[max(0, start - 400):start]
    # Cut at the previous blank line so the context stays within one method.
    cut = before.rfind("\n\n")
    if cut != -1:
        before = before[cut:]
    return before + content[start:end + 120]


def extract_http_calls(content: str) -> list[dict]:
    """Return client-side HTTP calls: ``[{"method", "path", "segments", "context"}]``."""
    content = content or ""
    calls = []
    for match in _HTTP_CALL_RE.finditer(content):
        segments = normalize_client_path(match.group(3))
        if segments:
            calls.append({
                "method": match.group(1).lower(),
                "path": match.group(3),
                "segments": segments,
                "context": _call_context(content, match.start(), match.end()),
                "enclosing": enclosing_method(content, match.start()),
                "position": match.start(),
            })
    for match in _FETCH_RE.finditer(content):
        segments = normalize_client_path(match.group(2))
        if not segments:
            continue
        tail = content[match.end(): match.end() + 200]
        method_match = _FETCH_METHOD_RE.search(tail)
        calls.append({
            "method": method_match.group(1).lower() if method_match else "get",
            "path": match.group(2),
            "segments": segments,
            "context": _call_context(content, match.start(), match.end()),
            "enclosing": enclosing_method(content, match.start()),
            "position": match.start(),
        })
    return calls


_CONSUMER_RE = re.compile(r"\.(?:subscribe|then)\s*\(")
_CALLBACK_PARAM_RE = re.compile(r"^\s*(?:async\s*)?(?:\(\s*(\w+)[^)]*\)|(\w+))\s*=>")
_NEXT_KEY_RE = re.compile(r"\s*\{\s*next\s*:")
# RxJS operators whose callback receives the response value.
_PIPE_OPERATOR_RE = re.compile(r"\b(?:tap|map|switchMap|mergeMap|concatMap|exhaustMap|filter)\s*\(")
_NO_ARG_CALLBACK_RE = re.compile(r"\s*(?:async\s*)?\(\s*\)\s*=>")
_LOGGING_RE = re.compile(r"\bconsole\.\w+\s*\([^()]*\)\s*;?")


def _call_statement(content: str, position: int) -> str:
    """The statement starting at the call: up to the next blank line or HTTP call."""
    tail = content[position:position + 700]
    nxt = _HTTP_CALL_RE.search(tail, 5)
    if nxt:
        tail = tail[:nxt.start()]
    blank = tail.find("\n\n")
    if blank != -1:
        tail = tail[:blank]
    return tail


def _callback_reads_param(args: str) -> bool | None:
    """Whether the arrow callback at the start of *args* uses its parameter (None: no parameter)."""
    param = _CALLBACK_PARAM_RE.match(args)
    if not param:
        return None
    name = param.group(1) or param.group(2)
    return bool(re.search(rf"\b{re.escape(name)}\b", args[param.end():param.end() + 400]))


def _balanced(text: str) -> str | None:
    """*text* up to the close of the argument list opened just before it (None if cut off)."""
    depth = 1
    for idx, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return text[:idx]
    return None


def response_consumption(content: str, position: int) -> str:
    """How the result of the call starting at *position* is used.

    ``consumed``: a callback, pipe operator (``tap``/``map``...) or assignment reads the response;
    ``acknowledged``: a success callback runs on the response without reading its body
    (e.g. ``next: () => { this.router.navigate(...) }``) — handled, not fire-and-forget;
    ``ignored``: ``.subscribe()``/``.then()`` without a callback that uses it;
    ``returned``: the observable/promise is handed back to the caller;
    ``unknown``: nothing conclusive in the evidence.
    """
    content = content or ""
    line_start = content.rfind("\n", 0, position) + 1
    prefix = content[line_start:position]
    tail = _call_statement(content, position)
    # ``return this.http.post(...).pipe(tap(res => this.setCurrentUser(res.user)))``
    # reads the response before handing the observable back.
    for op in _PIPE_OPERATOR_RE.finditer(tail):
        if ".pipe" in tail[:op.start()] and _callback_reads_param(tail[op.end():]):
            return "consumed"
    if re.search(r"\breturn\b", prefix):
        return "returned"
    if re.search(r"=\s*(?:await\s+)?(?:(?:firstValueFrom|lastValueFrom)\s*\(\s*)?(?:this\.)?[\w.]*$", prefix):
        return "consumed"
    consumer = _CONSUMER_RE.search(tail)
    if not consumer:
        return "unknown"
    args = tail[consumer.end():]
    if re.match(r"\s*\)", args):
        return "ignored"
    next_key = _NEXT_KEY_RE.match(args)
    if next_key:
        args = args[next_key.end():]
    reads = _callback_reads_param(args)
    if reads:
        return "consumed"
    no_arg = _NO_ARG_CALLBACK_RE.match(args)
    if reads is False or no_arg:
        # A callback cut off by the evidence window proves nothing either way.
        body = _balanced(content[position + consumer.end():])
        if body is None:
            return "unknown"
        if not no_arg:
            return "ignored"
        # A no-argument success callback that does real work (state change,
        # navigation) runs only once the response arrives.
        callback = body[next_key.end() if next_key else 0:]
        callback = callback[_NO_ARG_CALLBACK_RE.match(callback).end():] if _NO_ARG_CALLBACK_RE.match(callback) else callback
        work = _LOGGING_RE.sub("", callback.split("error:", 1)[0])
        return "acknowledged" if re.search(r"[A-Za-z_]\w*\s*(?:\(|=[^=>])", work) else "ignored"
    return "consumed" if re.match(r"\s*(?:this\.)?\w+", args) else "unknown"


def _closes(text: str) -> bool:
    """Whether the argument list opened just before *text* is closed within it."""
    depth = 1
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return True
    return False


def method_is_complete(content: str, name: str) -> bool:
    """Whether the method *name* appears in *content* with its whole body."""
    body = method_body(content, name)
    if body is None:
        return False
    if re.match(r"\s*(?:async\s+)?def\s", body):
        return True  # indentation-delimited; client calls are checked in brace languages
    open_at = body.find("{")
    return open_at != -1 and _closes(body[open_at + 1:])


def member_call_positions(content: str, method: str) -> list[int]:
    """Start offsets of ``x.method(`` calls in *content*."""
    return [m.start() for m in re.finditer(rf"\.\s*{re.escape(method)}\s*\(", content or "")]


def method_body(content: str, name: str) -> str | None:
    """Source of the method/function *name* inside *content* (header included).

    Python bodies end at the first non-blank line indented at or below the
    header; brace languages end at the matching closing brace.
    """
    content = content or ""
    py = re.search(rf"^([ \t]*)(?:async\s+)?def\s+{re.escape(name)}\s*\(", content, re.MULTILINE)
    if py:
        indent = len(py.group(1).expandtabs())
        lines = content[py.start():].split("\n")
        body = [lines[0]]
        for line in lines[1:]:
            if (
                line.strip()
                and len(line.expandtabs()) - len(line.expandtabs().lstrip()) <= indent
                and not line.lstrip().startswith((")", "]", "}"))
            ):
                break
            body.append(line)
        return "\n".join(body).rstrip()
    brace = re.search(
        rf"^[ \t]*(?:(?:public|private|protected|static|async|override|export|function)\s+)*"
        rf"{re.escape(name)}\s*\([^()]*\)[^{{;\n]*\{{",
        content,
        re.MULTILINE,
    )
    if brace:
        depth = 0
        for idx in range(brace.end() - 1, len(content)):
            if content[idx] == "{":
                depth += 1
            elif content[idx] == "}":
                depth -= 1
                if depth == 0:
                    return content[brace.start():idx + 1]
        return content[brace.start():]
    return None


_BUILTIN_CALLS = {
    "print", "len", "str", "int", "float", "dict", "list", "set", "tuple", "isinstance", "getattr",
    "setattr", "hasattr", "range", "enumerate", "zip", "sorted", "min", "max", "sum", "any", "all",
    "super", "type", "open", "round", "abs", "bool", "format", "repr", "iter", "next", "map", "filter",
}


def helper_calls(body: str) -> list[str]:
    """Helper functions/methods called inside *body*: ``self.x(``, ``this.x(`` and bare ``x(``."""
    body = body or ""
    header_end = body.find("\n")
    code = body[header_end + 1:] if header_end != -1 else ""
    # Comments and string literals are not calls.
    code = re.sub(r"(?m)(?:#|//).*$", "", code)
    code = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "''", code)
    names: list[str] = []
    for match in re.finditer(r"(?:\b(?:self|this)\.|(?<![\w.]))([a-z_][A-Za-z0-9_]*)\s*\(", code):
        name = match.group(1)
        if (
            name not in _BUILTIN_CALLS and name not in _CONTROL_WORDS and name not in _KEYWORDS
            and name not in names
        ):
            names.append(name)
    return names


_KEYWORDS = {"except", "elif", "and", "or", "not", "in", "is", "with", "assert", "yield", "await", "lambda", "new", "typeof"}


_ORM_CALL_RE = re.compile(r"\b([A-Z]\w*)\.objects\.(\w+)\s*\(")
_INSTANCE_WRITE_RE = re.compile(r"\b([a-z_]\w*)\.(save|delete)\s*\(")
_CRUD_KIND = {
    "create": "create", "get_or_create": "create", "update_or_create": "create", "bulk_create": "create",
    "bulk_update": "update", "update": "update",
}


def orm_operations(content: str, base_line: int = 1) -> list[dict]:
    """Model-level ORM operations: ``[{"model", "operation", "kind", "line", "code"}]``.

    ``kind`` is create/read/update/delete. A queryset chained with
    ``.delete()``/``.update()`` counts as that write; instance ``.save()``/
    ``.delete()`` are reported with ``model=None``.
    """
    ops = []
    for offset, line in enumerate((content or "").split("\n")):
        model_calls = list(_ORM_CALL_RE.finditer(line))
        for match in model_calls:
            rest = line[match.end():]
            if re.search(r"\)\s*\.\s*delete\s*\(", rest):
                kind = "delete"
            elif re.search(r"\)\s*\.\s*update\s*\(", rest):
                kind = "update"
            else:
                kind = _CRUD_KIND.get(match.group(2), "read")
            ops.append({"model": match.group(1), "operation": match.group(2), "kind": kind,
                        "line": base_line + offset, "code": line.strip()})
        if not model_calls:
            inst = _INSTANCE_WRITE_RE.search(line)
            if inst and inst.group(1) not in ("self", "serializer", "session", "db", "request", "os", "path"):
                ops.append({"model": None, "operation": inst.group(2),
                            "kind": "update" if inst.group(2) == "save" else "delete",
                            "line": base_line + offset, "code": line.strip()})
    return ops


_NUMERIC_TERMS = {
    "subtotal", "total", "amount", "price", "quantity", "qty", "count", "sum", "tax", "discount",
    "cost", "balance", "fee", "shipping",
}
_COMPUTE_WORDS_RE = re.compile(
    r"\b(calculat\w*|comput\w*|summ?\w*|totals?|subtotals?|add(?:s|ed)? up|increment\w*|decrement\w*)\b",
    re.IGNORECASE,
)


def numeric_terms(question: str) -> list[str]:
    """Quantities a calculation question asks about (``subtotal``, ``total`` ...)."""
    found = []
    for word in re.findall(r"[a-z]+", (question or "").lower()):
        base = word[:-1] if word.endswith("s") and word[:-1] in _NUMERIC_TERMS else word
        if base in _NUMERIC_TERMS and base not in found:
            found.append(base)
    return found


def is_calculation_question(question: str) -> bool:
    return bool(_COMPUTE_WORDS_RE.search(question or "")) and bool(numeric_terms(question))


def computations(content: str, term: str, base_line: int = 1) -> list[dict]:
    """Lines that compute *term*: an assignment/property whose name contains it with an
    arithmetic value (``* + - /``, ``reduce``, ``sum``), or ``++``/``--``/``+=``/``-=`` on it."""
    pattern = re.compile(
        rf"\b([\w.]*{re.escape(term)}\w*)\s*(\+\+|--|[+\-*/]=|=(?!=)|:)\s*([^\n;]*)",
        re.IGNORECASE,
    )
    arithmetic_re = re.compile(r"[*+/]|\s-\s|\breduce\s*\(|\bsum\s*\(|\bF\(")
    out = []
    current_def = ""
    for offset, line in enumerate((content or "").split("\n")):
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*", "<")):
            continue
        header = re.match(r"(?:async\s+)?def\s+(\w+)|(?:(?:public|private|static|async)\s+)*(\w+)\s*\([^()]*\)[^{;]*\{\s*$", stripped)
        if header:
            current_def = header.group(1) or header.group(2) or ""
        if (
            stripped.startswith("return ") and _names_term(current_def, term)
            and arithmetic_re.search(stripped[7:])
        ):
            out.append({"term": term, "line": base_line + offset, "code": stripped[:160]})
            continue
        for match in pattern.finditer(line):
            name, operator, value = match.group(1), match.group(2), match.group(3)
            if not _names_term(name, term):
                continue
            arithmetic = operator in ("++", "--", "+=", "-=", "*=", "/=") or arithmetic_re.search(value)
            if arithmetic and not re.match(r"\s*(?:models|serializers)\.", value):
                out.append({"term": term, "line": base_line + offset, "code": stripped[:160]})
                break
    return out


def _names_term(name: str, term: str) -> bool:
    """Whether identifier *name* names quantity *term* (``totalAmount`` -> total, not ``subtotal`` -> total)."""
    term = term.lower()
    parts = [p.lower() for p in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", (name or "").split(".")[-1])]
    for part in parts:
        if part.startswith(term):
            return True
        if part.endswith(term) and not any(part.endswith(o) and len(o) > len(term) for o in _NUMERIC_TERMS):
            return True
    return False


_DELTA_CALL_RE = re.compile(r"\.(\w+)\s*\([^()\n]*,\s*([+-]?\s*1)\s*\)")


def delta_calls(content: str) -> list[tuple[str, int]]:
    """Calls passing a literal +1/-1 as last argument: ``addToCart(id, v, -1)`` -> ("addToCart", -1)."""
    return [(m.group(1), int(m.group(2).replace(" ", ""))) for m in _DELTA_CALL_RE.finditer(content or "")]


_WRITE_INTENT_RE = re.compile(
    r"\b(updat\w*|add\w*|creat\w*|sav\w*|writ\w*|delet\w*|remov\w*|submit\w*|log ?in|sign ?in|"
    r"register\w*|increas\w*|decreas\w*|checkout|pay\w*|post)\b",
    re.IGNORECASE,
)
_READ_INTENT_RE = re.compile(
    r"\b(retriev\w*|fetch\w*|load\w*|list\w*|get|gets|show\w*|display\w*|view\w*|returned|read\w*)\b",
    re.IGNORECASE,
)


def rank_http_calls(calls: list[dict], terms: set[str], question: str) -> list[dict]:
    """Calls most relevant to the question: domain terms near the call + HTTP-method intent.

    Returns only the best-scoring calls (ties kept); calls with no domain
    relevance at all are dropped.
    """
    wants_write = bool(_WRITE_INTENT_RE.search(question or ""))
    wants_read = bool(_READ_INTENT_RE.search(question or ""))
    scored = []
    for call in calls:
        haystack = ("/".join(call["segments"]) + " " + call.get("context", "")).lower()
        score = sum(1 for t in terms if _stem(t) in haystack)
        if score == 0:
            continue
        if call["method"] == "get" and wants_read:
            score += 1
        if call["method"] != "get" and wants_write:
            score += 1
        scored.append((score, call))
    if not scored:
        return []
    best = max(score for score, _ in scored)
    seen, ranked = set(), []
    for score, call in scored:
        key = (call["method"], tuple(call["segments"]))
        if score == best and key not in seen:
            seen.add(key)
            ranked.append(call)
    return ranked


def extract_routes(content: str) -> list[dict]:
    """Return backend route registrations: ``[{"path", "segments", "handler", "method"}]``."""
    routes = []
    for match in _DJANGO_ROUTE_RE.finditer(content or ""):
        handler = match.group(3).split(".")
        if handler[-1] in ("as_view", "urls"):
            handler = handler[:-1]
        name = handler[-1] if handler else ""
        if name in ("include", "admin", "site", ""):
            continue
        routes.append({
            "path": match.group(2),
            "segments": normalize_route_path(match.group(2)),
            "handler": name,
            "method": None,
        })
    for match in _EXPRESS_ROUTE_RE.finditer(content or ""):
        routes.append({
            "path": match.group(3),
            "segments": normalize_route_path(match.group(3)),
            "handler": match.group(4).split(".")[-1],
            "method": match.group(1).lower(),
        })
    for match in _DECORATOR_ROUTE_RE.finditer(content or ""):
        method = match.group(1).lower()
        routes.append({
            "path": match.group(3),
            "segments": normalize_route_path(match.group(3)),
            "handler": match.group(4),
            "method": None if method in ("route", "api_route") else method,
        })
    return routes


def url_matches(client_segments: list[str], route_segments: list[str]) -> bool:
    """A route matches when its segments equal the tail of the client path.

    Routers usually mount sub-route files under a prefix (``include()``), so the
    client path ``accounts/cart/add`` matches the route ``cart/add``.
    """
    if not route_segments or len(route_segments) > len(client_segments):
        return False
    tail = client_segments[-len(route_segments):]
    return all(r == "*" or c == "*" or r == c for c, r in zip(tail, route_segments))


def route_search_terms(client_segments: list[str]) -> list[str]:
    """Text-search terms likely to locate the route registration for a client path."""
    concrete = [seg for seg in client_segments if seg != "*"]
    terms = []
    # Most specific first; routers often mount sub-routes under a prefix
    # (``accounts/`` + ``login/``), so fall back to the last segment.
    if len(concrete) >= 2:
        terms.append("/".join(concrete[-2:]) + "/")
    if concrete:
        terms.append(concrete[-1] + "/")
    return list(dict.fromkeys(terms))


def handler_name_hypotheses(client_segments: list[str]) -> list[str]:
    """Conventional handler names derived from a URL (``cart/add`` -> ``CartAddView``)."""
    concrete = [re.sub(r"[^a-z0-9]", "", seg) for seg in client_segments if seg != "*"]
    concrete = [seg for seg in concrete if seg]
    if not concrete:
        return []
    tail = concrete[-2:]
    pascal = "".join(seg.capitalize() for seg in tail)
    return [f"{pascal}View", pascal, "_".join(tail)]


# ---------------------------------------------------------------------------
# Definitions, member calls, handler methods
# ---------------------------------------------------------------------------

_CLASS_DEF_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:class|interface)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)
_FUNC_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"
    r"|^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)"
    r"|^\s*(?:export\s+)?const\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\("
    r"|^\s*(?:(?:public|private|protected|static|async|override)\s+)*([A-Za-z_]\w*)\s*\([^()]*\)\s*(?::\s*[^{=;]+)?\{",
    re.MULTILINE,
)
_NOT_METHOD_NAMES = {"if", "for", "while", "switch", "catch", "function", "return", "constructor", "else"}
_MEMBER_CALL_RE = re.compile(r"\bthis\.(\w+)\.(\w+)\s*\(")
_HANDLER_METHOD_RE = re.compile(
    r"\bdef\s+(get|post|put|patch|delete|list|create|retrieve|update|partial_update|destroy)\s*\(\s*self",
)
_VIEWSET_METHODS = {
    "list": "get", "retrieve": "get", "create": "post",
    "update": "put", "partial_update": "patch", "destroy": "delete",
}


def defined_names(item: dict) -> set[str]:
    """Names defined by an evidence item (symbol + class/function definitions in its code)."""
    names = set()
    for key in ("symbol", "qualified_name"):
        value = item.get(key)
        if value:
            names.update(part for part in str(value).split(".") if part)
    content = evidence_text(item)
    names.update(match for match in _CLASS_DEF_RE.findall(content))
    for groups in _FUNC_DEF_RE.findall(content):
        names.update(name for name in groups if name and name not in _NOT_METHOD_NAMES)
    return names


def class_names(item: dict) -> set[str]:
    names = set(_CLASS_DEF_RE.findall(evidence_text(item)))
    symbol = item.get("symbol")
    if symbol and str(item.get("symbol_type") or "").lower() in ("class", "interface"):
        names.add(str(symbol))
    return names


def member_calls(content: str) -> list[tuple[str, str]]:
    """``this.cartService.addToCart(`` -> ``("cartService", "addToCart")``."""
    return _MEMBER_CALL_RE.findall(content or "")


def handled_http_methods(content: str) -> set[str]:
    methods = set()
    for name in _HANDLER_METHOD_RE.findall(content or ""):
        methods.add(_VIEWSET_METHODS.get(name, name))
    return methods


# ---------------------------------------------------------------------------
# Persistence, storage, responses, UI events
# ---------------------------------------------------------------------------

_ORM_WRITE_RE = re.compile(
    r"\.objects\.(?:create|get_or_create|update_or_create|bulk_create|bulk_update)\s*\("
    r"|\.(?:save|delete)\s*\(\s*(?:update_fields\s*=[^)]*)?\)"
    r"|\.(?:filter|exclude|all)\([^)]*\)\s*\.(?:update|delete)\s*\("
    r"|\bserializer\.save\s*\("
    r"|\b(?:session|db)\.(?:add|delete|merge|commit|flush)\s*\("
    r"|\.(?:insert_one|insert_many|update_one|update_many|delete_one|delete_many)\s*\("
    r"|\bprisma\.\w+\.(?:create|update|upsert|delete)\w*\s*\("
)
_ORM_READ_RE = re.compile(
    r"\.objects\.(?:get|filter|all|exclude|select_related|prefetch_related|values|annotate|aggregate|order_by|first|last|count)\s*\("
    r"|\b\w+_set\.(?:all|filter|get)\s*\("
    r"|\.(?:filter|exclude|select_related|prefetch_related|order_by)\s*\("
    r"|\b(?:session|db)\.(?:query|execute|get|scalars?|scalar)\s*\("
    r"|\bselect\(\s*[A-Z]\w*"
    r"|\.(?:find_one|findOne|findMany|findUnique|findAll)\s*\("
    r"|\bprisma\.\w+\.find\w*\s*\("
)
_RAW_SQL_RE = re.compile(
    r"\bcursor\.execute\s*\("
    r"|\.raw\s*\(\s*['\"]"
    r"|['\"]\s*(?:SELECT\s+[\w*,.\s]+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b",
    re.IGNORECASE,
)
_CLIENT_STORAGE_RE = re.compile(r"\b(?:localStorage|sessionStorage|indexedDB|AsyncStorage)\b|document\.cookie")
_BACKEND_RESPONSE_RE = re.compile(
    r"\breturn\s+(?:Response|JsonResponse|HttpResponse|jsonify|ResponseEntity)\b"
    r"|=\s*(?:Response|JsonResponse|HttpResponse)\s*\("
    r"|\bres\.(?:json|send|status)\s*\("
)
_FRONTEND_HANDLING_RE = re.compile(
    r"\.subscribe\s*\(\s*(?:\{|\(|\w+\s*=>|next\s*:)"
    r"|\.then\s*\(\s*(?:\(|\w+\s*=>)"
    r"|\bpipe\s*\(\s*(?:tap|map)\s*\("
)
_UI_EVENT_RE = re.compile(
    r"\((?:click|submit|ngSubmit|change|input|keyup|keydown)\)\s*="
    r"|\bon(?:Click|Submit|Change)\s*=|addEventListener\s*\(|@click|v-on:"
)
_AUTH_RE = re.compile(
    r"\bauthenticate\s*\(|permission_classes|IsAuthenticated|authentication_classes|JWT|jwt"
    r"|set_cookie|CanActivate|HttpInterceptor|Authorization|@login_required|passport\.",
)
_ENTRY_POINT_RE = re.compile(
    r"bootstrapApplication\s*\(|platformBrowserDynamic\s*\(|createRoot\s*\(|ReactDOM\.render\s*\("
    r"|createApp\s*\(|execute_from_command_line\s*\(|get_wsgi_application\s*\(|get_asgi_application\s*\("
    r"|\buvicorn\.run\s*\(|=\s*FastAPI\s*\(|=\s*Flask\s*\(|\bexpress\s*\(\s*\)|\bapp\.listen\s*\("
)
_DB_CONFIG_RE = re.compile(r"\bDATABASES\b|DATABASE_URL|dj_database_url|create_engine\s*\(|SQLALCHEMY_DATABASE_URI|mongoose\.connect")

DB_ENGINES = {
    "postgresql": ("postgres",),
    "postgres": ("postgres",),
    "mysql": ("mysql",),
    "mariadb": ("mariadb", "mysql"),
    "sqlite": ("sqlite",),
    "mongodb": ("mongo",),
    "oracle": ("oracle",),
    "sql server": ("mssql", "sqlserver", "sql_server"),
    "dynamodb": ("dynamodb",),
}


def orm_writes(content: str) -> list[str]:
    return [m.group(0) for m in _ORM_WRITE_RE.finditer(content or "")]


def orm_reads(content: str) -> list[str]:
    return [m.group(0) for m in _ORM_READ_RE.finditer(content or "")]


def has_raw_sql(content: str) -> bool:
    return bool(_RAW_SQL_RE.search(content or ""))


def has_client_storage(content: str) -> bool:
    return bool(_CLIENT_STORAGE_RE.search(content or ""))


def has_backend_response(content: str) -> bool:
    return bool(_BACKEND_RESPONSE_RE.search(content or ""))


def has_frontend_handling(content: str) -> bool:
    return bool(_FRONTEND_HANDLING_RE.search(content or ""))


def has_ui_event(content: str) -> bool:
    return bool(_UI_EVENT_RE.search(content or ""))


def has_auth_signal(content: str) -> bool:
    return bool(_AUTH_RE.search(content or ""))


def has_entry_point_signal(content: str) -> bool:
    return bool(_ENTRY_POINT_RE.search(content or ""))


def has_db_config(content: str) -> bool:
    return bool(_DB_CONFIG_RE.search(content or ""))


def backend_persistence_ops(item: dict) -> dict:
    """ORM/SQL operations in an evidence item that count as *backend* persistence.

    Client-side storage, admin registrations, tests and migrations never count.
    """
    if not is_backend_runtime(item.get("file")):
        return {"writes": [], "reads": [], "raw_sql": False}
    content = evidence_text(item)
    return {
        "writes": orm_writes(content),
        "reads": orm_reads(content),
        "raw_sql": has_raw_sql(content),
    }


# ---------------------------------------------------------------------------
# Natural-language helpers
# ---------------------------------------------------------------------------

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from",
    "how", "i", "if", "in", "is", "it", "of", "on", "or", "so", "that", "the",
    "their", "this", "to", "was", "what", "when", "where", "which", "who", "why",
    "with", "you", "your", "me", "please", "explain", "describe", "show", "tell",
    "find", "list", "trace", "flow", "work", "works", "handled", "handle", "handles",
    "overall", "project", "application", "app", "code", "including", "between",
    "compared", "versus", "via", "into", "then", "they", "them", "its", "all",
    "frontend", "backend", "database", "db", "ui", "api", "server", "client",
    "user", "users", "button", "click", "form", "submit", "submitting", "response",
    "request", "returned", "return", "write", "update", "updates", "angular", "react",
    "django", "successful", "operations", "operation", "functions", "methods", "method",
    "function", "call", "calls", "related", "dependencies", "implementation",
    "implemented", "retrieved", "retrieve", "information", "data", "logged", "customer",
    "used", "uses", "using", "architecture", "communication", "end", "stored", "store",
    "persist", "persisted", "model", "models", "query", "queries", "defined", "definition",
    "authenticated", "logic", "business", "entry", "point", "points", "configuration",
}


def domain_terms(question: str) -> set[str]:
    """Question words that identify the feature domain (``cart``, ``login`` ...)."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]+", question or "")
    terms = set()
    for word in words:
        # Split CamelCase identifiers into their parts as well.
        parts = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", word) or [word]
        for part in [word, *parts]:
            low = part.lower()
            if len(low) >= 3 and low not in STOP_WORDS:
                terms.add(low)
    return terms


def _stem(term: str) -> str:
    return term[:4] if len(term) >= 6 else term


def relevance_hits(item: dict, terms: set[str]) -> tuple[int, int]:
    """(# terms in path/symbol, # terms in content) for an evidence item."""
    if not terms:
        return 0, 0
    head = " ".join(
        normalize_path(str(item.get(key) or "")).lower()
        for key in ("file", "symbol", "qualified_name")
    )
    content = evidence_text(item).lower()
    head_hits = sum(1 for t in terms if _stem(t) in head)
    content_hits = sum(1 for t in terms if _stem(t) in content)
    return head_hits, content_hits


def is_relevant(item: dict, terms: set[str]) -> bool:
    """Relevant when the path/symbol names the domain, or the code mentions it repeatedly."""
    if not terms:
        return True
    head_hits, content_hits = relevance_hits(item, terms)
    return head_hits >= 1 or content_hits >= min(2, len(terms))


_BACKTICK_RE = re.compile(r"`([^`\n]{1,120})`")
_CODE_TOKEN_RE = re.compile(
    r"(?<![\w/.])(?:[A-Za-z_$][\w$]*\.)+[A-Za-z_$][\w$]*(?:\(\))?"   # dotted: a.b.c / views.py
    r"|\b[a-z]+(?:[A-Z][a-z0-9]+)+\b"                               # camelCase
    r"|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b"                        # PascalCase
    r"|\b[a-z0-9]+(?:_[a-z0-9]+)+\b"                                 # snake_case
)
_FILE_PATH_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.\w+")
_URL_PATH_RE = re.compile(r"(?<![\w.])(?:/[\w.<>:{}-]+){2,}/?")
_PROSE_TECH_WORDS = {
    "javascript", "typescript", "rxjs", "github", "node.js", "nodejs", "next.js",
    "vue.js", "e.g", "i.e", "etc", "langgraph", "e.g.", "i.e.",
}


def claim_identifiers(text: str) -> set[str]:
    """Code identifiers, file paths and URL paths mentioned in a claim."""
    found = set()
    for raw in _BACKTICK_RE.findall(text or ""):
        # Mis-paired backticks capture prose such as "), which extracts ".
        if raw[:1].isspace() or raw[-1:].isspace() or raw[:1] in ")]},;:." or not re.search(r"[A-Za-z_]", raw):
            continue
        token = raw.strip().rstrip("()").strip()
        if 2 <= len(token) <= 80 and not token.startswith("[E"):
            found.add(token)
    stripped = _BACKTICK_RE.sub(" ", text or "")
    for regex in (_FILE_PATH_RE, _URL_PATH_RE, _CODE_TOKEN_RE):
        for token in regex.findall(stripped):
            token = token.rstrip("()").rstrip(".,;:")
            if len(token) >= 3:
                found.add(token)
    return {token for token in found if token.lower() not in _PROSE_TECH_WORDS}


def identifier_grounded(token: str, haystack: str) -> bool:
    """Whether a claim identifier is visible in (lower-cased) evidence text."""
    tok = token.lower().strip().rstrip("()")
    if not tok:
        return True
    if tok in haystack:
        return True
    # Call expression: ground the callee; argument names are often paraphrased.
    if "(" in tok:
        callee = tok.split("(", 1)[0].strip()
        if callee and callee != tok:
            return identifier_grounded(callee, haystack)
    # URL path: the last two concrete segments are enough (routers mount prefixes).
    if "/" in tok and not re.search(r"\.\w+$", tok):
        segs = [s for s in normalize_route_path(tok) if s != "*"]
        if segs and "/".join(segs[-2:]) in haystack:
            return True
    # Dotted access: every part visible (e.g. CartAddView.post -> class CartAddView + def post).
    if "." in tok:
        parts = [p for p in re.split(r"[.\s]+", tok) if len(p) > 1 and p not in ("this", "self")]
        if parts and all(p in haystack for p in parts):
            return True
    # Code expression in backticks: all identifier-like words visible.
    words = re.findall(r"[a-z_][a-z0-9_]{2,}", tok)
    if words and len(words) > 1 and all(w in haystack for w in words):
        return True
    return False


def engines_mentioned(text: str) -> set[str]:
    low = (text or "").lower()
    return {engine for engine in DB_ENGINES if re.search(rf"\b{re.escape(engine)}\b", low)}


def engine_established(engine: str, evidence: list[dict]) -> bool:
    """A database engine is established only by runtime config/backend evidence."""
    needles = DB_ENGINES.get(engine, (engine,))
    for item in evidence:
        role = file_role(item.get("file"))
        if role in INDIRECT_ROLES:
            continue
        content = evidence_text(item).lower()
        if any(needle in content for needle in needles):
            return True
    return False


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

_CACHE_QUESTION_RE = re.compile(r"\bcach(?:e|es|ed|ing)\b", re.IGNORECASE)
# Code that reads, writes or wraps data in a cache. Backend/broker
# configuration (CACHES, REDIS_URL, CELERY_BROKER_URL) is not cache usage.
_CACHE_OP_RE = re.compile(
    r"\bcache\.(?:get|set|add|delete|delete_many|get_many|set_many|get_or_set|incr|decr|touch|clear|"
    r"delete_pattern|keys|ttl|expire)\s*\("
    r"|\bcache_page\s*\(|\bcache_control\s*\(|\bvary_on_\w+\s*\(|\bnever_cache\b|\bcaches\s*\["
    r"|\b(?:redis\w*|r|redis_client|client|conn)\.(?:get|set|setex|setnx|hget|hset|expire|delete|incr|mget)\s*\(",
)
_CACHE_CONFIG_RE = re.compile(r"\bCACHES\b|django_redis|RedisCache|LocMemCache|CACHE_BACKEND|\bREDIS_URL\b")
_CACHE_TTL_RE = re.compile(
    r"\bcache_page\s*\(\s*[\d\s*]+|\bcache\.\w+\([^)\n]*(?:timeout\s*=|,\s*\d)|\bsetex\s*\(|\.expire\s*\("
    r"|\bmax_age\s*=|['\"]TIMEOUT['\"]\s*:|\bCACHE_TTL\b"
)
_CACHE_KEY_RE = re.compile(
    r"\bcache\.\w+\(\s*(?:f?['\"]|\w*key)|\bkey_prefix\s*=|['\"]KEY_PREFIX['\"]|\bmake_key\s*\(|\bget_cache_key\s*\("
    r"|\bcache_page\s*\("
)
_CACHE_INVALIDATION_RE = re.compile(
    r"\bcache\.(?:delete|delete_many|clear|delete_pattern|touch)\s*\(|\binvalidat\w*\s*\(|\.expire\s*\("
)
_CACHE_BYPASS_RE = re.compile(r"\bnever_cache\b|no_cache\s*=\s*True|\bvary_on_\w+\s*\(|['\"]no-cache['\"]")
_BROKER_CONFIG_RE = re.compile(r"\bCELERY_BROKER_URL\b|\bCELERY_RESULT_BACKEND\b|\bbroker_url\b")

CACHE_ASPECTS = {
    # aspect: (question words, code pattern)
    "cache_ttl": (re.compile(r"\bttl\b|\bexpir\w*|\btimeouts?\b|how long", re.IGNORECASE), _CACHE_TTL_RE),
    "cache_keys": (re.compile(r"\bkeys?\b|key prefix", re.IGNORECASE), _CACHE_KEY_RE),
    "cache_invalidation": (re.compile(r"\binvalidat\w*|\bevict\w*|\bpurg\w*|\bstale\b|\bclear\w*", re.IGNORECASE),
                           _CACHE_INVALIDATION_RE),
    "cache_bypass": (re.compile(r"\bbypass\w*|\bskip\w*|never[_ ]cache|no-cache", re.IGNORECASE), _CACHE_BYPASS_RE),
}


def is_cache_question(question: str) -> bool:
    return bool(_CACHE_QUESTION_RE.search(question or ""))


def cache_operations(content: str) -> list[str]:
    return [m.group(0).strip() for m in _CACHE_OP_RE.finditer(content or "")]


def has_cache_config(content: str) -> bool:
    return bool(_CACHE_CONFIG_RE.search(content or ""))


def has_broker_config(content: str) -> bool:
    return bool(_BROKER_CONFIG_RE.search(content or ""))


# ---------------------------------------------------------------------------
# Flow destinations ("... and then through to checkout")
# ---------------------------------------------------------------------------

_DESTINATION_RES = (
    re.compile(
        r"\b(?:then|finally|until|through|all the way|proceeds?|continu\w*|carri\w*|moves?|goes)\b"
        r"(?:\s+\w+){0,3}?\s+(?:to|into|until)\s+(?:the\s+)?([a-z][\w-]*)",
        re.IGNORECASE,
    ),
    re.compile(r"\b[a-z][\w]*-to-([a-z][\w]*)\b", re.IGNORECASE),
)
_GENERIC_DESTINATIONS = STOP_WORDS | {
    "account", "accounts", "page", "screen", "component", "service", "view", "browser", "end", "finish",
    "completion", "storage", "session", "state", "their", "his", "her", "next",
}


def flow_destinations(question: str) -> list[str]:
    """Stages a flow question explicitly says the flow must reach (``checkout`` in
    "merged on login and then carried through to checkout")."""
    found = []
    for pattern in _DESTINATION_RES:
        for match in pattern.finditer(question or ""):
            term = match.group(1).lower()
            if len(term) >= 3 and term not in _GENERIC_DESTINATIONS and term not in found:
                found.append(term)
    return found


def implements_term(item: dict, term: str) -> bool:
    """The item's own path, symbol or definitions name *term* (``CheckoutView``,
    ``checkout/checkout.ts``) — a string mention elsewhere does not count."""
    stem = _stem(term.lower())
    head = " ".join(
        normalize_path(str(item.get(key) or "")).lower() for key in ("file", "symbol", "qualified_name")
    )
    return stem in head or any(stem in name.lower() for name in defined_names(item))
