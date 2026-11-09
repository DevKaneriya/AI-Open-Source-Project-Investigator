"""
Regression tests for broad repository questions (the suggested example queries).

The repository inventory scans every runtime file, so broad questions
("which data models exist", "what are the API endpoints") can be answered
completely, and absences ("this repository has no backend") are established
facts instead of perpetual "missing stage" gaps.
"""

import json
from types import SimpleNamespace

import pytest

from backend.app.services import evidence_signals as sig
from backend.app.services import repository_inventory_service as inventory
from backend.app.services.citation_validation_service import CitationValidationService
from backend.app.services.evidence_evaluation_service import EvidenceEvaluationService
from backend.app.services.llm_service import LLMService

SUGGESTED = {
    "Give me an overview of the architecture": ("architectural_overview", "architecture"),
    "Where is authentication handled?": ("cross_cutting_concern", "inventory_authentication"),
    "What are the main API endpoints?": ("architectural_overview", "inventory_endpoints"),
    "Which data models exist?": ("symbol_lookup", "inventory_data_models"),
    "Which frameworks and versions are used?": ("dependency_analysis", "inventory_dependencies"),
    "Trace a request from the frontend to the database": ("end_to_end_flow", "flow"),
}


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def frontend_only_repo(tmp_path):
    """A React/three.js site: no backend, no HTTP calls, no auth, dev-server health endpoints."""
    _write(tmp_path, "frontend/package.json", json.dumps({
        "name": "site",
        "dependencies": {"react": "^19.2.0", "react-dom": "^19.2.0", "three": "^0.185.1",
                         "zustand": "^5.0.0", "axios": "1.18.0", "react-scripts": "5.0.1"},
        "devDependencies": {"@craco/craco": "7.1.0"},
        "scripts": {"start": "craco start"},
    }, indent=2))
    _write(tmp_path, "frontend/package-lock.json", json.dumps({
        "packages": {
            "node_modules/react": {"version": "19.2.8"},
            "node_modules/react-dom": {"version": "19.2.8"},
            "node_modules/three": {"version": "0.185.1"},
            "node_modules/zustand": {"version": "5.0.3"},
            "node_modules/axios": {"version": "1.18.0"},
        },
    }, indent=2))
    _write(tmp_path, "frontend/src/index.js",
           'import React from "react";\nimport ReactDOM from "react-dom/client";\nimport App from "@/App";\n\n'
           'const root = ReactDOM.createRoot(document.getElementById("root"));\nroot.render(<App />);\n')
    _write(tmp_path, "frontend/src/App.js",
           'import Experience from "@/components/Experience";\n\nexport default function App() {\n  return <Experience />;\n}\n')
    _write(tmp_path, "frontend/src/components/Experience.jsx",
           'import * as THREE from "three";\nimport { useStore } from "@/store";\n\n'
           'export default function Experience() {\n  const open = useStore((s) => s.menuOpen);\n'
           '  return <div onClick={() => useStore.getState().setMenuOpen(!open)} />;\n}\n')
    _write(tmp_path, "frontend/src/store.js",
           'import { create } from "zustand";\n\nexport const useStore = create((set) => ({\n'
           '  menuOpen: false,\n  setMenuOpen: (v) => set({ menuOpen: v }),\n}));\n')
    _write(tmp_path, "frontend/src/constants/testIds/auth.js",
           "export const LOGIN = {\n  emailInput: 'login-email-input',\n  passwordInput: 'login-password-input',\n};\n")
    _write(tmp_path, "frontend/plugins/health/health-endpoints.js",
           "function setup(devServer) {\n  devServer.app.get(\"/health\", (req, res) => {\n    res.json({ ok: true });\n  });\n"
           "  devServer.app.get(\"/health/live\", (req, res) => res.json({ alive: true }));\n}\nmodule.exports = setup;\n")
    _write(tmp_path, "frontend/craco.config.js", "module.exports = { webpack: {} };\n")
    return tmp_path


@pytest.fixture
def full_stack_repo(tmp_path):
    """FastAPI + SQLAlchemy backend with a Streamlit (Python) frontend."""
    _write(tmp_path, "requirements.txt", "fastapi\nsqlalchemy>=2.0\nstreamlit>=1.50.0\nrequests\n")
    _write(tmp_path, "backend/app/database.py",
           'import os\nfrom sqlalchemy import create_engine\nengine = create_engine(os.environ["DATABASE_URL"])\n')
    _write(tmp_path, "backend/app/models.py",
           "from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column\n\n\nclass Base(DeclarativeBase):\n    pass\n\n\n"
           "class Repository(Base):\n    __tablename__ = \"repositories\"\n    id: Mapped[int] = mapped_column(primary_key=True)\n"
           "    url: Mapped[str] = mapped_column()\n")
    _write(tmp_path, "backend/app/main.py",
           "from fastapi import FastAPI\nfrom pydantic import BaseModel\n\napp = FastAPI(title=\"api\")\n\n\n"
           "class RepositoryRequest(BaseModel):\n    repository_url: str\n\n\n"
           "@app.get(\"/health\")\ndef health():\n    return {\"status\": \"ok\"}\n\n\n"
           "@app.post(\"/repositories\")\ndef add_repository(\n    request: RepositoryRequest,\n):\n"
           "    return repository_service.add_repository(request.repository_url)\n")
    _write(tmp_path, "backend/app/repository_service.py",
           "class RepositoryService:\n    def add_repository(self, db, url):\n        repo = Repository(url=url)\n"
           "        db.add(repo)\n        db.commit()\n        return repo\n")
    _write(tmp_path, "frontend/api_client.py",
           "import requests\n\n\nclass ApiClient:\n    def __init__(self):\n        self._session = requests.Session()\n\n"
           "    def _request(self, method, path, **kw):\n        return self._session.request(method, path, **kw)\n\n"
           "    def add_repository(self, url):\n        return self._request(\n            \"POST\",\n            \"/repositories\",\n"
           "            json={\"repository_url\": url},\n        )\n\n"
           "    def health_check(self):\n        return self._request(\"GET\", \"/health\")\n")
    _write(tmp_path, "frontend/views.py",
           "import streamlit as st\n\n\ndef render(api_client):\n    url = st.text_input(\"Repository URL\")\n"
           "    if st.button(\"Add\"):\n        repo = api_client.add_repository(url)\n        st.success(repo)\n")
    _write(tmp_path, "frontend/access.py",
           "import hmac, os\n\n\ndef _check_password(value):\n"
           "    return hmac.compare_digest(value, os.environ.get(\"ADMIN_PASSWORD\", \"\"))\n")
    return tmp_path


def _scan(root):
    inventory.RepositoryInventoryService._cache.clear()
    return inventory.RepositoryInventoryService().scan(str(root))


class TestInventoryScan:
    def test_frontend_only_repository(self, frontend_only_repo):
        inv = _scan(frontend_only_repo)
        assert inv.complete
        assert not inv.has_backend and not inv.has_persistence
        assert [s.file for s in inv.get("frontend_entry")] == ["frontend/src/index.js"]
        assert not inv.get("http_calls")
        assert not inv.get("auth"), "test-id constants are not an auth implementation"
        assert inv.get("auth_mentions")
        endpoints = inv.get("endpoints")
        assert {s.label for s in endpoints} == {"GET /health", "GET /health/live"}
        assert all(s.kind == "dev-server" for s in endpoints)
        assert not inv.app_endpoints
        stores = [s for s in inv.get("state_stores") if s.kind == "store"]
        assert stores and stores[0].file == "frontend/src/store.js"

        npm = inv.manifests[0]
        assert npm.locked["react"] == "19.2.8"
        assert ("axios", "frontend/package.json") in inv.unused_packages()
        assert all(name not in ("react-scripts", "react", "three") for name, _ in inv.unused_packages())
        assert inventory.root_component_files(inv) == ["frontend/src/App.js", "frontend/src/components/Experience.jsx"]

    def test_full_stack_repository(self, full_stack_repo):
        inv = _scan(full_stack_repo)
        assert inv.has_backend and inv.has_persistence
        assert {s.label for s in inv.app_endpoints} == {"GET /health", "POST /repositories"}
        kinds = {s.label: s.kind for s in inv.get("data_models")}
        assert kinds["Repository"] == "SQLAlchemy model"
        assert kinds["RepositoryRequest"] == "Pydantic schema"
        assert "Base" not in kinds
        assert inv.get("http_calls"), "Python (Streamlit) client calls are client network calls"
        assert any(s.file == "frontend/access.py" for s in inv.get("auth"))

    def test_string_literals_do_not_count_as_signals(self, tmp_path):
        _write(tmp_path, "backend/checks.py",
               'PATTERNS = ["DATABASES", "createRoot(", "IsAuthenticated"]\n'
               'MESSAGE = "set DATABASE_URL before starting"\n')
        inv = _scan(tmp_path)
        assert not inv.get("db_config") and not inv.get("frontend_entry") and not inv.get("auth")

    def test_manifest_windows_fit_the_prompt_snippet_budget(self, frontend_only_repo):
        inv = _scan(frontend_only_repo)
        windows = inventory.manifest_windows(inv, 6)
        assert windows and all(len(w["content"]) <= 1600 for w in windows)
        covered = "\n".join(w["content"] for w in windows)
        assert '"react": "^19.2.0"' in covered

    def test_definition_windows_cover_whole_class(self, full_stack_repo):
        inv = _scan(full_stack_repo)
        windows = inventory.definition_windows(inv, "data_models", keep=lambda s: s.kind == "SQLAlchemy model")
        assert len(windows) == 1 and "url: Mapped[str]" in windows[0]["content"]

    def test_generic_flow_seed_follows_one_request(self, full_stack_repo):
        inv = _scan(full_stack_repo)
        seeded = inventory.generic_flow_windows(inv, "Trace a request from the frontend to the database")
        files = [w["file"] for w in seeded]
        assert files == ["frontend/api_client.py", "frontend/views.py", "backend/app/main.py"]
        assert "repository_service.add_repository" in seeded[-1]["content"], "the whole handler, past its signature"


class TestSuggestedQuestionProfiles:
    @pytest.mark.parametrize("question,expected", list(SUGGESTED.items()))
    def test_profile(self, question, expected):
        question_type, profile = expected
        assert EvidenceEvaluationService.evaluation_profile(question, question_type) == profile

    @pytest.mark.parametrize("question,question_type,profile", [
        ("Which models reference CartItem?", "call_site_lookup", "call_site"),
        ("Where is the /api/cart/sync endpoint handled?", "symbol_lookup", "symbol_lookup"),
        ("How does login work from the Angular UI to Django?", "end_to_end_flow", "flow"),
        ("What are the exact Angular and Django versions?", "dependency_analysis", "inventory_dependencies"),
    ])
    def test_specific_questions_keep_their_profiles(self, question, question_type, profile):
        assert EvidenceEvaluationService.evaluation_profile(question, question_type) == profile


def _item(eid, file, start, end, content=None, root=None):
    if content is None and root is not None:
        content = "\n".join((root / file).read_text(encoding="utf-8").splitlines()[start - 1:end])
    return {"evidence_id": eid, "file": file, "start_line": start, "end_line": end, "snippet": content or ""}


class TestInventoryEvaluation:
    def _evaluator(self):
        return EvidenceEvaluationService(llm_service=SimpleNamespace())

    def test_auth_absence_is_sufficient(self, frontend_only_repo):
        inv = _scan(frontend_only_repo)
        evidence = [_item("E1", "frontend/src/constants/testIds/auth.js", 1, 4, root=frontend_only_repo)]
        result = self._evaluator().evaluate(
            "Where is authentication handled?", evidence, "cross_cutting_concern",
            current_round=0, max_rounds=1, search_coverage={"inventory": inv.to_coverage()},
        )
        assert result.sufficiency == "sufficient"
        stage = next(s for s in result.flow_stages if s.stage == "authentication_implementation")
        assert stage.verified_absent

    def test_trace_in_frontend_only_repo_marks_request_stages_absent(self, frontend_only_repo):
        inv = _scan(frontend_only_repo)
        evidence = [_item("E1", "frontend/src/components/Experience.jsx", 1, 7, root=frontend_only_repo)]
        result = self._evaluator().evaluate(
            "Trace a request from the frontend to the database", evidence, "end_to_end_flow",
            current_round=0, max_rounds=1, search_coverage={"inventory": inv.to_coverage()},
        )
        assert result.sufficiency == "sufficient", result.reasoning
        absent = {s.stage for s in result.flow_stages if s.verified_absent}
        assert {"client_service", "url_routing", "backend_handler", "persistence"} <= absent

    def test_architecture_of_frontend_only_repo(self, frontend_only_repo):
        inv = _scan(frontend_only_repo)
        root = frontend_only_repo
        evidence = [
            _item("E1", "frontend/src/index.js", 1, 6, root=root),
            _item("E2", "frontend/craco.config.js", 1, 1, root=root),
            _item("E3", "frontend/src/store.js", 1, 6, root=root),
        ]
        result = self._evaluator().evaluate(
            "Give me an overview of the architecture", evidence, "architectural_overview",
            current_round=0, max_rounds=1, search_coverage={"inventory": inv.to_coverage()},
        )
        assert result.sufficiency == "sufficient", result.reasoning
        assert any(s.stage == "state_management" and s.present for s in result.flow_stages)

    def test_without_inventory_absence_is_not_assumed(self, frontend_only_repo):
        evidence = [_item("E1", "frontend/src/components/Experience.jsx", 1, 7, root=frontend_only_repo)]
        result = self._evaluator().evaluate(
            "Trace a request from the frontend to the database", evidence, "end_to_end_flow",
            current_round=1, max_rounds=1, search_coverage={},
        )
        assert result.sufficiency != "sufficient"

    def test_dependency_notes_report_locked_and_unused(self, frontend_only_repo):
        inv = _scan(frontend_only_repo)
        evidence = [_item("E1", "frontend/package.json", 1, 20, root=frontend_only_repo)]
        result = self._evaluator().evaluate(
            "Which frameworks and versions are used?", evidence, "dependency_analysis",
            current_round=0, max_rounds=1, search_coverage={"inventory": inv.to_coverage()},
        )
        assert result.sufficiency == "sufficient"
        notes = " ".join(result.grounding_notes)
        assert "^19.2.0 (locked 19.2.8)" in notes
        assert "`axios`" in notes and "never imported" in notes


class TestAbsenceClaimValidation:
    def test_complete_scan_backs_repository_wide_absence(self, frontend_only_repo):
        cov = _scan(frontend_only_repo).to_coverage()
        backs = CitationValidationService._inventory_backs_absence
        assert backs("The repository has no backend and no database.", cov)
        assert backs("The frontend makes no HTTP requests.", cov)
        assert backs("No authentication is implemented.", cov)
        assert backs("`axios` is declared but never imported.", cov)
        assert not backs("`react` is never imported.", cov), "react is imported"

    def test_incomplete_scan_backs_nothing(self, frontend_only_repo):
        cov = {**_scan(frontend_only_repo).to_coverage(), "complete": False}
        assert not CitationValidationService._inventory_backs_absence("The repository has no backend.", cov)

    def test_full_stack_absence_claims_are_rejected(self, full_stack_repo):
        cov = _scan(full_stack_repo).to_coverage()
        backs = CitationValidationService._inventory_backs_absence
        assert not backs("The repository has no backend.", cov)
        assert not backs("There is no database.", cov)


class TestRateLimitRetry:
    def test_wait_is_read_from_the_error(self):
        error = RuntimeError("Error code: 429 - Rate limit reached ... Please try again in 29.115s. Need more tokens?")
        assert 29 < LLMService._retry_delay(error, 0) < 31

    def test_milliseconds_and_minutes(self):
        assert LLMService._retry_delay(RuntimeError("rate limit: try again in 450ms"), 0) < 2
        assert 63 < LLMService._retry_delay(RuntimeError("rate_limit_exceeded try again in 1m2.5s"), 0) < 64
        assert LLMService._retry_delay(RuntimeError("rate limit: try again in 5m0s"), 0) == LLMService.MAX_SINGLE_WAIT_SECONDS

    def test_daily_quota_is_not_retried(self):
        error = RuntimeError("Error code: 429 - Rate limit reached on tokens per day (TPD): Limit 200000. "
                             "Please try again in 12m30s.")
        assert LLMService._retry_delay(error, 0) is None

    def test_non_transient_errors_are_not_retried(self):
        assert LLMService._retry_delay(RuntimeError("Invalid API key"), 0) is None

    def test_json_fences_are_stripped(self):
        assert LLMService._strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert LLMService._strip_json_fences('Here you go: {"a": 1} done') == '{"a": 1}'


def test_python_client_calls_match_fastapi_routes():
    client = 'return self._request("GET", f"/repositories/{repository_id}/investigate", params=p)'
    calls = sig.extract_http_calls(client)
    assert calls and calls[0]["method"] == "get"
    routes = sig.extract_routes('@app.get("/repositories/{repository_id}/investigate")\ndef investigate():\n    pass\n')
    assert sig.url_matches(calls[0]["segments"], routes[0]["segments"])
    assert sig.file_role("frontend/api_client.py") == sig.ROLE_FRONTEND
    assert sig.file_role("backend/app/main.py") == sig.ROLE_BACKEND
