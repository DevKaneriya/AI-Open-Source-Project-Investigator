from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.models.code_call import CodeCall
from backend.app.models.code_import import CodeImport
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.repository_file import RepositoryFile
from backend.app.services.code_analyzer import CodeAnalyzer
from backend.app.services.language_service import LanguageService
from backend.app.services.tree_sitter_analyzer import TreeSitterAnalyzer
from backend.app.services.file_filter_service import should_include_file

class CodeIndexer:
    def __init__(self):
        self.python_analyzer = CodeAnalyzer()
        self.language_service = LanguageService()
        self.tree_sitter_analyzer = TreeSitterAnalyzer()

    def index_repository(self, db: Session, repository_id: int, repository_path: str) -> dict:
        root = Path(repository_path)
        self._clear_existing_index(db, repository_id)

        indexed_files = 0
        indexed_symbols = 0
        indexed_imports = 0
        indexed_calls = 0
        unsupported_files = 0

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue
            
            if not should_include_file(root, file_path):
                continue

            language = self.language_service.detect(str(file_path))
            if not language:
                continue

            try:
                analysis = self._analyze_file(file_path, language)
                repository_file = self._create_file_record(
                    db, repository_id, root, file_path, language
                )
                db.flush()

                imports_count = self._store_imports(db, repository_file.id, analysis)
                symbols_count, calls_count = self._store_symbols(
                    db, repository_file.id, analysis
                )

                indexed_files += 1
                indexed_imports += imports_count
                indexed_symbols += symbols_count
                indexed_calls += calls_count

            except (SyntaxError, UnicodeDecodeError, OSError, ValueError):
                unsupported_files += 1
                continue

        db.commit()

        return {
            "repository_id": repository_id,
            "indexed_files": indexed_files,
            "indexed_symbols": indexed_symbols,
            "indexed_imports": indexed_imports,
            "indexed_calls": indexed_calls,
            "unsupported_files": unsupported_files,
        }

    def _analyze_file(self, file_path: Path, language: str) -> dict:
        # Manifest / plain-text files have no parseable symbols.
        # Return an empty analysis so the file record is created and
        # CodeChunkService can fall through to its fallback chunker.
        _CONTENT_ONLY_LANGUAGES = {"json", "text", "toml", "yaml"}
        if language in _CONTENT_ONLY_LANGUAGES:
            return {
                "file": str(file_path),
                "functions": [],
                "classes": [],
                "imports": [],
            }

        if language == "python":
            return self.python_analyzer.analyze_python_file(str(file_path))

        return self.tree_sitter_analyzer.analyze(str(file_path), language)

    @staticmethod
    def _create_file_record(
        db: Session,
        repository_id: int,
        root: Path,
        file_path: Path,
        language: str,
    ) -> RepositoryFile:
        repository_file = RepositoryFile(
            repository_id=repository_id,
            path=str(file_path.relative_to(root)),
            language=language,
            file_size=file_path.stat().st_size,
        )
        db.add(repository_file)
        return repository_file

    @staticmethod
    def _store_imports(db: Session, file_id: int, analysis: dict) -> int:
        count = 0
        for imported_module in analysis.get("imports", []):
            db.add(CodeImport(file_id=file_id, imported_module=imported_module))
            count += 1
        return count

    @staticmethod
    def _store_symbols(db: Session, file_id: int, analysis: dict) -> tuple[int, int]:
        symbol_count = 0
        call_count = 0

        # The analyzer keeps the existing "functions" collection for backward
        # compatibility, but each entry may now carry its real symbol type.
        # Example: a TypeScript type alias is stored as "type", not "function".
        for function in analysis.get("functions", []):
            symbol = CodeSymbol(
                file_id=file_id,
                name=function["name"],
                symbol_type=function.get("symbol_type", "function"),
                qualified_name=function["name"],
                start_line=function["line"],
                end_line=function["end_line"],
            )
            db.add(symbol)
            db.flush()

            call_count += CodeIndexer._store_calls(
                db, symbol.id, function.get("calls", [])
            )
            symbol_count += 1

        # Likewise, "classes" remains the compatibility collection, while
        # class/interface/enum entries preserve their actual symbol type.
        for class_data in analysis.get("classes", []):
            class_symbol = CodeSymbol(
                file_id=file_id,
                name=class_data["name"],
                symbol_type=class_data.get("symbol_type", "class"),
                qualified_name=class_data["name"],
                start_line=class_data["line"],
                end_line=class_data["end_line"],
            )
            db.add(class_symbol)
            db.flush()
            symbol_count += 1

            for method in class_data.get("methods", []):
                method_symbol = CodeSymbol(
                    file_id=file_id,
                    name=method["name"],
                    symbol_type=method.get("symbol_type", "method"),
                    qualified_name=f"{class_data['name']}.{method['name']}",
                    start_line=method["line"],
                    end_line=method["end_line"],
                )
                db.add(method_symbol)
                db.flush()

                call_count += CodeIndexer._store_calls(
                    db, method_symbol.id, method.get("calls", [])
                )
                symbol_count += 1

        return symbol_count, call_count

    @staticmethod
    def _store_calls(db: Session, symbol_id: int, calls: list[str]) -> int:
        count = 0
        for call_name in calls:
            db.add(CodeCall(symbol_id=symbol_id, called_name=call_name))
            count += 1
        return count

    @staticmethod
    def _clear_existing_index(db: Session, repository_id: int) -> None:
        existing_files = db.scalars(
            select(RepositoryFile).where(
                RepositoryFile.repository_id == repository_id
            )
        ).all()

        for repository_file in existing_files:
            existing_symbols = db.scalars(
                select(CodeSymbol).where(CodeSymbol.file_id == repository_file.id)
            ).all()

            for symbol in existing_symbols:
                db.execute(delete(CodeCall).where(CodeCall.symbol_id == symbol.id))

            db.execute(
                delete(CodeImport).where(CodeImport.file_id == repository_file.id)
            )
            db.execute(
                delete(CodeSymbol).where(CodeSymbol.file_id == repository_file.id)
            )

        db.execute(
            delete(RepositoryFile).where(
                RepositoryFile.repository_id == repository_id
            )
        )
