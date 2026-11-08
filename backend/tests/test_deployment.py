"""
Tests for the hosting changes: database URL normalisation, the pgvector
store, re-cloning missing checkouts, and the public demo-mode safeguards.

The pgvector tests need a PostgreSQL server with the ``vector`` extension and
run only when PGVECTOR_TEST_URL is set, e.g.
    docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test pgvector/pgvector:pg17
    PGVECTOR_TEST_URL=postgresql://postgres:test@localhost:55432/postgres
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.app.core.config import Settings


# ── Settings ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("postgresql://u:p@host/db?sslmode=require", "postgresql+psycopg://u:p@host/db?sslmode=require"),
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
    ],
)
def test_database_url_uses_psycopg3_driver(raw, expected):
    assert Settings(database_url=raw, _env_file=None).database_url == expected


def test_vector_backend_is_validated():
    assert Settings(vector_backend=" PGVector ", _env_file=None).vector_backend == "pgvector"
    with pytest.raises(ValueError):
        Settings(vector_backend="faiss", _env_file=None)


# ── pgvector store (real database) ───────────────────────────────────────────

PGVECTOR_URL = os.environ.get("PGVECTOR_TEST_URL")


@pytest.fixture
def pg_store():
    if not PGVECTOR_URL:
        pytest.skip("PGVECTOR_TEST_URL not set")
    from sqlalchemy import create_engine, text

    from backend.app.services.pgvector_service import PgVectorService

    url = Settings(database_url=PGVECTOR_URL, _env_file=None).database_url
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS chunk_embeddings, code_chunks"))
        # Minimal stand-in for the real table: the store only needs its ids.
        conn.execute(text("CREATE TABLE code_chunks (id BIGINT PRIMARY KEY)"))
        conn.execute(text("INSERT INTO code_chunks SELECT generate_series(1, 20)"))
    store = PgVectorService(engine)
    yield store, engine
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS chunk_embeddings, code_chunks"))
    engine.dispose()


def _point(pid, repo, vector):
    padded = list(vector) + [0.0] * (384 - len(vector))
    return SimpleNamespace(id=pid, vector=padded, payload={"repository_id": repo, "file_id": pid, "content": f"c{pid}"})


def test_pgvector_search_is_scoped_and_ranked(pg_store):
    store, _ = pg_store
    store.upsert([
        _point(1, 4, [1.0, 0.0]),
        _point(2, 4, [0.6, 0.8]),
        _point(3, 14, [1.0, 0.0]),  # other repo, identical vector
    ])

    hits = store.search([1.0, 0.0] + [0.0] * 382, repository_id=4, limit=5)

    assert [h.id for h in hits] == [1, 2]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.6)
    assert hits[0].payload == {"repository_id": 4, "file_id": 1, "content": "c1"}


def test_pgvector_upsert_count_delete(pg_store):
    store, _ = pg_store
    store.upsert([_point(i, 5 if i <= 3 else 6, [1.0, float(i)]) for i in range(1, 6)])
    store.upsert([_point(1, 5, [0.0, 1.0])])  # re-upsert replaces, doesn't duplicate

    assert store.count_repository(5) == 3
    assert store.count_repository(6) == 2

    store.delete_repository(5)
    assert store.count_repository(5) == 0
    assert store.count_repository(6) == 2


def test_pgvector_rows_follow_their_chunks(pg_store):
    from sqlalchemy import text

    store, engine = pg_store
    store.upsert([_point(7, 9, [1.0])])
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM code_chunks WHERE id = 7"))
    assert store.count_repository(9) == 0


def test_pgvector_schema_setup_is_idempotent(pg_store):
    from backend.app.services.pgvector_service import PgVectorService

    store, engine = pg_store
    store.upsert([_point(1, 4, [1.0])])
    PgVectorService(engine)  # e.g. a second worker or a restart
    assert store.count_repository(4) == 1


# ── Re-cloning missing checkouts ─────────────────────────────────────────────

def _repo(local_path, url="https://github.com/o/r"):
    return SimpleNamespace(id=1, url=url, local_path=local_path)


def test_existing_checkout_is_left_alone(tmp_path):
    from backend.app.services import repository_workspace

    repo = _repo(str(tmp_path))
    with patch.object(repository_workspace._github_service, "clone_repository") as clone:
        repository_workspace.ensure_local_copy(MagicMock(), repo)
    clone.assert_not_called()


def test_never_cloned_repository_is_left_alone():
    from backend.app.services import repository_workspace

    repo = _repo(None)
    with patch.object(repository_workspace._github_service, "clone_repository") as clone:
        repository_workspace.ensure_local_copy(MagicMock(), repo)
    clone.assert_not_called()
    assert repo.local_path is None


def test_missing_checkout_is_recloned(tmp_path):
    from backend.app.services import repository_workspace

    repo = _repo(str(tmp_path / "wiped-on-restart"))
    db = MagicMock()
    new_path = tmp_path / "fresh"
    new_path.mkdir()
    with patch.object(repository_workspace._github_service, "clone_repository", return_value=str(new_path)) as clone:
        repository_workspace.ensure_local_copy(db, repo)

    clone.assert_called_once_with("https://github.com/o/r")
    assert repo.local_path == str(new_path)
    db.commit.assert_called_once()


# ── Demo mode ────────────────────────────────────────────────────────────────

@pytest.fixture
def demo_env(monkeypatch):
    import streamlit as st

    from frontend.utils import access

    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("DEMO_SESSION_QUERIES", "2")
    monkeypatch.setenv("DEMO_QUERIES_PER_HOUR", "3")
    access._global_query_log.clear()
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    yield access
    access._global_query_log.clear()
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def test_admin_is_default_outside_demo_mode(monkeypatch):
    from frontend.utils import access

    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert not access.demo_mode()
    assert access.is_admin()


def test_demo_visitor_is_not_admin_until_unlocked(demo_env):
    import streamlit as st

    assert demo_env.demo_mode() and not demo_env.is_admin()
    assert not demo_env._check_password("wrong")
    assert demo_env._check_password("s3cret")
    st.session_state[demo_env.ADMIN_KEY] = True
    assert demo_env.is_admin()


def test_empty_admin_password_never_unlocks(demo_env, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    assert not demo_env._check_password("")


def test_demo_rate_limits(demo_env):
    import streamlit as st

    assert demo_env.reserve_query("x" * 501) is not None  # too long, not counted
    assert demo_env.reserve_query("q1") is None
    assert demo_env.reserve_query("q2") is None
    assert "2 questions per hour" in demo_env.reserve_query("q3")  # session limit

    # A new visitor still shares the global hourly budget (3 here)
    del st.session_state[demo_env.SESSION_HITS_KEY]
    assert demo_env.reserve_query("q4") is None
    del st.session_state[demo_env.SESSION_HITS_KEY]
    assert "busy" in demo_env.reserve_query("q5")


def test_admin_bypasses_rate_limits(demo_env):
    import streamlit as st

    st.session_state[demo_env.ADMIN_KEY] = True
    for _ in range(5):
        assert demo_env.reserve_query("q") is None


def test_demo_history_is_never_written_to_disk(demo_env, tmp_path, monkeypatch):
    from frontend.utils import history_manager

    history_file = tmp_path / "history.json"
    monkeypatch.setattr(history_manager, "HISTORY_FILE", history_file)

    history_manager.save_history_entry({"query": "q", "repository_id": 1})

    assert not history_file.exists()
    assert history_manager.load_history()[0]["query"] == "q"
