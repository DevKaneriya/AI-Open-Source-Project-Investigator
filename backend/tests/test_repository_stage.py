"""
Tests for repository pipeline stage tracking (backend derivation) and the
frontend readiness helpers built on it.
"""

from unittest.mock import MagicMock

import pytest

from backend.app.models.repository import Repository
from backend.app.services.repository_service import RepositoryService
from frontend.utils.pipeline import (
    backend_outdated,
    completed_steps,
    is_ready,
    pick_default_repo,
    remaining_steps,
    repo_stage,
    stage_label,
    stage_unknown,
)


def _repo(status: str, local_path: str | None = "/tmp/repo") -> Repository:
    return Repository(id=1, url="https://github.com/o/r", name="r", status=status, local_path=local_path)


@pytest.mark.parametrize(
    "status, files, chunks, vectors, expected",
    [
        # Legacy rows only ever reached "cloned": infer from the data present
        ("cloned", 0, 0, 0, "cloned"),
        ("cloned", 10, 0, 0, "parsed"),
        ("cloned", 10, 40, 0, "chunked"),
        ("cloned", 10, 40, 40, "indexed"),
        ("cloned", 10, 40, None, "chunked"),
        # Recorded stages are authoritative...
        ("chunked", 10, 40, 40, "chunked"),  # re-chunked; vectors are stale
        ("indexed", 10, 40, 40, "indexed"),
        ("indexed", 10, 40, None, "indexed"),  # Qdrant unavailable
        # ...but downgrade when their data is gone
        ("indexed", 10, 0, 40, "parsed"),
        ("indexed", 10, 40, 0, "chunked"),
        ("parsed", 0, 0, 0, "cloned"),
        # Transitional / failed states pass through
        ("cloning", 0, 0, 0, "cloning"),
        ("failed", 0, 0, 0, "failed"),
        ("pending", 0, 0, 0, "pending"),
    ],
)
def test_derive_stage(status, files, chunks, vectors, expected):
    local_path = None if status in ("pending", "cloning", "failed") else "/tmp/repo"
    assert RepositoryService._derive_stage(_repo(status, local_path), files, chunks, vectors) == expected


def test_list_repositories_includes_counts_and_stage():
    db = MagicMock()
    repo = _repo("cloned")
    db.scalars.return_value.all.return_value = [repo]
    db.execute.side_effect = [
        MagicMock(all=MagicMock(return_value=[(1, 12)])),  # files
        MagicMock(all=MagicMock(return_value=[(1, 30)])),  # chunks
    ]
    vector_service = MagicMock()
    vector_service.count_repository.return_value = 30

    listed = RepositoryService().list_repositories(db, vector_service)

    assert listed[0]["file_count"] == 12
    assert listed[0]["chunk_count"] == 30
    assert listed[0]["vector_count"] == 30
    assert listed[0]["stage"] == "indexed"


def test_list_repositories_tolerates_vector_store_failure():
    db = MagicMock()
    db.scalars.return_value.all.return_value = [_repo("cloned")]
    db.execute.side_effect = [MagicMock(all=MagicMock(return_value=[])), MagicMock(all=MagicMock(return_value=[]))]
    vector_service = MagicMock()
    vector_service.count_repository.side_effect = RuntimeError("qdrant down")

    listed = RepositoryService().list_repositories(db, vector_service)

    assert listed[0]["vector_count"] is None
    assert listed[0]["stage"] == "cloned"


def test_set_status_updates_and_ignores_missing():
    db = MagicMock()
    repo = _repo("cloned")
    db.get.return_value = repo
    RepositoryService().set_status(db, 1, "parsed")
    assert repo.status == "parsed"
    db.commit.assert_called_once()

    db.reset_mock()
    db.get.return_value = None
    RepositoryService().set_status(db, 2, "parsed")
    db.commit.assert_not_called()


def test_frontend_stage_helpers():
    ready = {"id": 1, "stage": "indexed"}
    partial = {"id": 2, "stage": "parsed"}
    legacy = {"id": 3, "status": "cloned"}  # backend without stage field

    assert is_ready(ready) and not is_ready(partial) and not is_ready(legacy)
    assert repo_stage(legacy) == "cloned"
    assert completed_steps(ready) == 4
    assert completed_steps(partial) == 2
    assert completed_steps({"stage": "failed"}) == 0
    assert [s.stage for s in remaining_steps(partial)] == ["chunked", "indexed"]
    assert [s.stage for s in remaining_steps(legacy)] == ["parsed", "chunked", "indexed"]
    assert remaining_steps(ready) == []
    assert pick_default_repo([partial, ready])["id"] == 1
    assert pick_default_repo([partial])["id"] == 2
    assert pick_default_repo([]) is None


def test_outdated_backend_detection():
    legacy = {"id": 3, "status": "cloned"}  # backend without stage tracking
    current = {"id": 4, "status": "cloned", "stage": "indexed"}

    assert stage_unknown(legacy) and not stage_unknown(current)
    assert stage_label(legacy) == "Status unknown"
    assert backend_outdated([current, legacy])
    assert not backend_outdated([current])
    assert not backend_outdated([])
