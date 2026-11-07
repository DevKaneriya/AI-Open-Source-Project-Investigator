"""
Keeps each repository's local checkout available.

Investigations read source files from the cloned checkout. On hosts with an
ephemeral disk the checkout disappears on restart while the index survives in
PostgreSQL, so a missing checkout is re-cloned on demand instead of failing.
"""

import logging
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.models.repository import Repository
from backend.app.services.github_service import GitHubService

logger = logging.getLogger(__name__)

_github_service = GitHubService()
_locks_guard = threading.Lock()
_locks: dict[int, threading.Lock] = {}


def _lock_for(repository_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(repository_id, threading.Lock())


def ensure_local_copy(db: Session, repository: Repository) -> None:
    """Re-clone the repository if it was cloned before but its checkout is gone.

    Repositories that were never cloned (no ``local_path``) are left alone so
    callers keep reporting "not cloned".
    """
    if not repository.local_path or Path(repository.local_path).is_dir():
        return
    if not repository.url:
        return

    # One clone per repository even when several requests arrive together.
    with _lock_for(repository.id):
        db.refresh(repository)
        if repository.local_path and Path(repository.local_path).is_dir():
            return

        logger.info("Checkout for repository %s is missing; re-cloning %s", repository.id, repository.url)
        repository.local_path = _github_service.clone_repository(repository.url)
        db.commit()
        db.refresh(repository)
