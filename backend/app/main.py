from fastapi import FastAPI
from pydantic import BaseModel

from backend.app.core.config import settings
from backend.app.services.github_service import GitHubService
from backend.app.services.repository_scanner import RepositoryScanner


app = FastAPI(
    title=settings.app_name,
    description="Agentic AI platform for investigating open-source software repositories.",
    version="0.1.0",
)


github_service = GitHubService()
repository_scanner = RepositoryScanner()


class RepositoryRequest(BaseModel):
    repository_url: str


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }


@app.post("/repositories/clone")
def clone_repository(request: RepositoryRequest):
    repository_path = github_service.clone_repository(
        request.repository_url
    )

    return {
        "message": "Repository cloned successfully",
        "repository_path": repository_path,
    }


@app.post("/repositories/scan")
def scan_repository(request: RepositoryRequest):
    repository_path = github_service.clone_repository(
        request.repository_url
    )

    scan_result = repository_scanner.scan(
        repository_path
    )

    return scan_result