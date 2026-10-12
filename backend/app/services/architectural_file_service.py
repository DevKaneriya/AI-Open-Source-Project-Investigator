"""
Architectural File Discovery Service

Identifies key architectural files in a repository without full scanning.
Uses database queries and pattern matching to find:
- README files
- Application entry points
- Dependency manifests
- Configuration files
- API routes
- Database models
"""

from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.models.repository_file import RepositoryFile


class ArchitecturalFileService:
    """Discovers architecturally significant files for broad questions."""
    
    # Priority file patterns by category
    README_PATTERNS = ["readme.md", "readme.rst", "readme.txt", "readme"]
    
    ENTRY_POINT_PATTERNS = {
        "python": ["main.py", "manage.py", "app.py", "__main__.py", "wsgi.py", "asgi.py"],
        "javascript": ["index.js", "main.js", "app.js", "server.js"],
        "typescript": ["main.ts", "app.ts", "index.ts", "server.ts"],
        "java": ["Application.java", "Main.java"],
        "go": ["main.go"],
    }
    
    DEPENDENCY_PATTERNS = [
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "Gemfile",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "Cargo.toml",
    ]
    
    CONFIG_PATTERNS = [
        "settings.py",
        "config.py",
        "configuration.py",
        ".env.example",
        "application.yml",
        "application.properties",
        "config.json",
        "appsettings.json",
    ]
    
    ROUTE_PATTERNS = {
        "python": ["urls.py", "routes.py", "api.py", "views.py"],
        "javascript": ["routes.js", "router.js", "api.js"],
        "typescript": ["routes.ts", "router.ts", "api.ts", "app.routes.ts"],
    }
    
    MODEL_PATTERNS = {
        "python": ["models.py", "schema.py", "entities.py"],
        "javascript": ["models.js", "schema.js"],
        "typescript": ["models.ts", "schema.ts", "entities.ts"],
    }
    
    def discover_key_files(
        self,
        db: Session,
        repository_id: int,
    ) -> dict:
        """
        Discover key architectural files for a repository.
        
        Returns dict with categorized file lists:
        {
            "readme": [RepositoryFile, ...],
            "entry_points": [RepositoryFile, ...],
            "dependencies": [RepositoryFile, ...],
            "configuration": [RepositoryFile, ...],
            "routes": [RepositoryFile, ...],
            "models": [RepositoryFile, ...],
        }
        """
        all_files = self._get_all_files(db, repository_id)
        
        return {
            "readme": self._find_readme_files(all_files),
            "entry_points": self._find_entry_points(all_files),
            "dependencies": self._find_dependencies(all_files),
            "configuration": self._find_configuration(all_files),
            "routes": self._find_routes(all_files),
            "models": self._find_models(all_files),
        }
    
    def discover_by_language(
        self,
        db: Session,
        repository_id: int,
        language: str,
    ) -> dict:
        """Discover key files filtered by primary language."""
        all_files = self._get_all_files(db, repository_id)
        language_files = [f for f in all_files if f.language == language]
        
        return {
            "entry_points": self._find_entry_points_by_language(language_files, language),
            "routes": self._find_routes_by_language(language_files, language),
            "models": self._find_models_by_language(language_files, language),
        }
    
    @staticmethod
    def _get_all_files(
        db: Session,
        repository_id: int,
    ) -> list[RepositoryFile]:
        """Get all repository files from database."""
        statement = (
            select(RepositoryFile)
            .where(RepositoryFile.repository_id == repository_id)
        )
        return db.scalars(statement).all()
    
    def _find_readme_files(
        self,
        files: list[RepositoryFile],
    ) -> list[RepositoryFile]:
        """Find README files."""
        readme_files = []
        
        for file in files:
            filename_lower = file.path.lower().split("/")[-1].split("\\")[-1]
            
            if any(pattern in filename_lower for pattern in self.README_PATTERNS):
                readme_files.append(file)
        
        # Prioritize root README
        root_readme = [f for f in readme_files if "/" not in f.path and "\\" not in f.path]
        if root_readme:
            return root_readme[:1] + [f for f in readme_files if f not in root_readme][:2]
        
        return readme_files[:3]
    
    def _find_entry_points(
        self,
        files: list[RepositoryFile],
    ) -> list[RepositoryFile]:
        """Find application entry points across all languages."""
        entry_points = []
        
        for file in files:
            filename = file.path.split("/")[-1].split("\\")[-1].lower()
            
            for language, patterns in self.ENTRY_POINT_PATTERNS.items():
                if any(pattern.lower() == filename for pattern in patterns):
                    entry_points.append(file)
                    break
        
        # Prioritize root-level entry points
        root_entries = [f for f in entry_points if f.path.count("/") + f.path.count("\\") <= 1]
        other_entries = [f for f in entry_points if f not in root_entries]
        
        return root_entries[:3] + other_entries[:3]
    
    def _find_entry_points_by_language(
        self,
        files: list[RepositoryFile],
        language: str,
    ) -> list[RepositoryFile]:
        """Find entry points for a specific language."""
        patterns = self.ENTRY_POINT_PATTERNS.get(language.lower(), [])
        
        entry_points = []
        for file in files:
            filename = file.path.split("/")[-1].split("\\")[-1].lower()
            if any(pattern.lower() == filename for pattern in patterns):
                entry_points.append(file)
        
        return entry_points[:5]
    
    def _find_dependencies(
        self,
        files: list[RepositoryFile],
    ) -> list[RepositoryFile]:
        """Find dependency manifest files."""
        dep_files = []
        
        for file in files:
            filename = file.path.split("/")[-1].split("\\")[-1]
            
            if filename in self.DEPENDENCY_PATTERNS:
                dep_files.append(file)
        
        # Prioritize root-level dependency files
        root_deps = [f for f in dep_files if "/" not in f.path and "\\" not in f.path]
        other_deps = [f for f in dep_files if f not in root_deps]
        
        return root_deps + other_deps[:3]
    
    def _find_configuration(
        self,
        files: list[RepositoryFile],
    ) -> list[RepositoryFile]:
        """Find configuration files."""
        config_files = []
        
        for file in files:
            filename = file.path.split("/")[-1].split("\\")[-1]
            
            if any(pattern == filename for pattern in self.CONFIG_PATTERNS):
                config_files.append(file)
        
        return config_files[:5]
    
    def _find_routes(
        self,
        files: list[RepositoryFile],
    ) -> list[RepositoryFile]:
        """Find API route definition files."""
        route_files = []
        
        for file in files:
            filename = file.path.split("/")[-1].split("\\")[-1]
            
            for language, patterns in self.ROUTE_PATTERNS.items():
                if any(pattern == filename for pattern in patterns):
                    route_files.append(file)
                    break
        
        return route_files[:5]
    
    def _find_routes_by_language(
        self,
        files: list[RepositoryFile],
        language: str,
    ) -> list[RepositoryFile]:
        """Find route files for a specific language."""
        patterns = self.ROUTE_PATTERNS.get(language.lower(), [])
        
        route_files = []
        for file in files:
            filename = file.path.split("/")[-1].split("\\")[-1]
            if any(pattern == filename for pattern in patterns):
                route_files.append(file)
        
        return route_files[:5]
    
    def _find_models(
        self,
        files: list[RepositoryFile],
    ) -> list[RepositoryFile]:
        """Find database model definition files."""
        model_files = []
        
        for file in files:
            filename = file.path.split("/")[-1].split("\\")[-1]
            
            for language, patterns in self.MODEL_PATTERNS.items():
                if any(pattern == filename for pattern in patterns):
                    model_files.append(file)
                    break
        
        return model_files[:5]
    
    def _find_models_by_language(
        self,
        files: list[RepositoryFile],
        language: str,
    ) -> list[RepositoryFile]:
        """Find model files for a specific language."""
        patterns = self.MODEL_PATTERNS.get(language.lower(), [])
        
        model_files = []
        for file in files:
            filename = file.path.split("/")[-1].split("\\")[-1]
            if any(pattern == filename for pattern in patterns):
                model_files.append(file)
        
        return model_files[:5]
    
    def get_file_category(self, file_path: str) -> str | None:
        """Determine the architectural category of a file."""
        filename = file_path.split("/")[-1].split("\\")[-1].lower()
        
        if any(pattern in filename for pattern in self.README_PATTERNS):
            return "readme"
        
        for language, patterns in self.ENTRY_POINT_PATTERNS.items():
            if any(pattern.lower() == filename for pattern in patterns):
                return "entry_point"
        
        if filename in [p.lower() for p in self.DEPENDENCY_PATTERNS]:
            return "dependency"
        
        if filename in [p.lower() for p in self.CONFIG_PATTERNS]:
            return "configuration"
        
        for language, patterns in self.ROUTE_PATTERNS.items():
            if any(pattern.lower() == filename for pattern in patterns):
                return "routes"
        
        for language, patterns in self.MODEL_PATTERNS.items():
            if any(pattern.lower() == filename for pattern in patterns):
                return "models"
        
        return None
