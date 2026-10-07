from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.models.code_call import CodeCall
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.repository_file import RepositoryFile


class CodeRelationshipService:
    def get_calls(
        self,
        db: Session,
        repository_id: int,
        symbol_name: str,
    ) -> list[dict]:
        statement = (
            select(CodeSymbol, CodeCall, RepositoryFile)
            .join(
                CodeCall,
                CodeCall.symbol_id == CodeSymbol.id,
            )
            .join(
                RepositoryFile,
                RepositoryFile.id == CodeSymbol.file_id,
            )
            .where(
                RepositoryFile.repository_id == repository_id,
                CodeSymbol.name.ilike(symbol_name),
            )
        )

        results = db.execute(statement).all()

        relationships = []

        for symbol, call, repository_file in results:
            possible_targets = self._find_target_symbols(
                db,
                repository_id,
                call.called_name,
            )

            relationships.append(
                {
                    "caller": {
                        "name": symbol.name,
                        "type": symbol.symbol_type,
                        "file": repository_file.path,
                        "start_line": symbol.start_line,
                        "end_line": symbol.end_line,
                    },
                    "called_name": call.called_name,
                    "possible_targets": possible_targets,
                }
            )

        return relationships

    def _find_target_symbols(
        self,
        db: Session,
        repository_id: int,
        called_name: str,
    ) -> list[dict]:
        parts = called_name.split(".")
        target_name = parts[-1]

        statement = (
            select(CodeSymbol, RepositoryFile)
            .join(
                RepositoryFile,
                RepositoryFile.id == CodeSymbol.file_id,
            )
            .where(
                RepositoryFile.repository_id == repository_id,
                or_(
                    CodeSymbol.name.ilike(target_name),
                    CodeSymbol.qualified_name.ilike(called_name),
                ),
            )
        )

        results = db.execute(statement).all()

        return [
            {
                "name": symbol.name,
                "type": symbol.symbol_type,
                "qualified_name": symbol.qualified_name,
                "file": repository_file.path,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
            }
            for symbol, repository_file in results
        ]