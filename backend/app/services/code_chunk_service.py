from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.code_chunk import CodeChunk
from backend.app.models.code_symbol import CodeSymbol
from backend.app.models.repository_file import RepositoryFile
from backend.app.services.file_filter_service import should_include_file


class CodeChunkService:
    DOCUMENTATION_EXTENSIONS = {
        ".md",
        ".mdx",
        ".txt",
        ".rst",
    }

    FALLBACK_CHUNK_SIZE = 80

    def create_chunks(
        self,
        db: Session,
        repository_id: int,
        repository_path: str,
    ) -> list[CodeChunk]:
        self._clear_existing_chunks(
            db=db,
            repository_id=repository_id,
        )

        statement = (
            select(RepositoryFile)
            .where(
                RepositoryFile.repository_id
                == repository_id
            )
        )

        repository_files = db.scalars(
            statement
        ).all()

        chunks = []

        for repository_file in repository_files:
            file_path = (
                Path(repository_path)
                / repository_file.path
            )

            if not file_path.is_file():
                continue
            
            if not should_include_file(repository_path, file_path):
                continue

            symbols = self._get_file_symbols(
                db=db,
                file_id=repository_file.id,
            )

            if symbols:
                file_chunks = (
                    self._create_symbol_chunks(
                        repository_id=repository_id,
                        file_id=repository_file.id,
                        file_path=file_path,
                        symbols=symbols,
                    )
                )

            elif file_path.suffix.lower() in (
                self.DOCUMENTATION_EXTENSIONS
            ):
                file_chunks = (
                    self._create_documentation_chunks(
                        repository_id=repository_id,
                        file_id=repository_file.id,
                        file_path=file_path,
                    )
                )

            else:
                file_chunks = (
                    self._create_fallback_chunks(
                        repository_id=repository_id,
                        file_id=repository_file.id,
                        file_path=file_path,
                    )
                )

            chunks.extend(file_chunks)

        if chunks:
            db.add_all(chunks)

        db.commit()

        return chunks

    @staticmethod
    def _get_file_symbols(
        db: Session,
        file_id: int,
    ) -> list[CodeSymbol]:
        statement = (
            select(CodeSymbol)
            .where(
                CodeSymbol.file_id == file_id
            )
            .order_by(
                CodeSymbol.start_line,
                CodeSymbol.end_line,
            )
        )

        return db.scalars(
            statement
        ).all()

    def _create_symbol_chunks(
        self,
        repository_id: int,
        file_id: int,
        file_path: Path,
        symbols: list[CodeSymbol],
    ) -> list[CodeChunk]:
        lines = self._read_lines(file_path)

        chunks = []

        for symbol in symbols:
            source = self._get_source_range(
                lines=lines,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
            )

            if not source.strip():
                continue

            chunks.append(
                CodeChunk(
                    repository_id=repository_id,
                    file_id=file_id,
                    symbol_id=symbol.id,
                    content=source,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                )
            )

        return chunks

    def _create_documentation_chunks(
        self,
        repository_id: int,
        file_id: int,
        file_path: Path,
    ) -> list[CodeChunk]:
        lines = self._read_lines(file_path)

        if not lines:
            return []

        chunks = []

        current_start = None
        current_lines = []

        for line_number, line in enumerate(
            lines,
            start=1,
        ):
            is_heading = self._is_markdown_heading(
                line
            )

            if is_heading and current_lines:
                chunk = self._build_text_chunk(
                    repository_id=repository_id,
                    file_id=file_id,
                    start_line=current_start,
                    lines=current_lines,
                )

                if chunk:
                    chunks.append(chunk)

                current_start = line_number
                current_lines = [line]

            else:
                if current_start is None:
                    current_start = line_number

                current_lines.append(line)

        if current_lines:
            chunk = self._build_text_chunk(
                repository_id=repository_id,
                file_id=file_id,
                start_line=current_start,
                lines=current_lines,
            )

            if chunk:
                chunks.append(chunk)

        return chunks

    def _create_fallback_chunks(
        self,
        repository_id: int,
        file_id: int,
        file_path: Path,
    ) -> list[CodeChunk]:
        lines = self._read_lines(file_path)

        if not lines:
            return []

        chunks = []

        for start in range(
            0,
            len(lines),
            self.FALLBACK_CHUNK_SIZE,
        ):
            chunk_lines = lines[
                start:start
                + self.FALLBACK_CHUNK_SIZE
            ]

            if not chunk_lines:
                continue

            content = "\n".join(
                chunk_lines
            )

            if not content.strip():
                continue

            chunks.append(
                CodeChunk(
                    repository_id=repository_id,
                    file_id=file_id,
                    symbol_id=None,
                    content=content,
                    start_line=start + 1,
                    end_line=(
                        start
                        + len(chunk_lines)
                    ),
                )
            )

        return chunks

    @staticmethod
    def _build_text_chunk(
        repository_id: int,
        file_id: int,
        start_line: int | None,
        lines: list[str],
    ) -> CodeChunk | None:
        if start_line is None:
            return None

        content = "\n".join(lines)

        if not content.strip():
            return None

        return CodeChunk(
            repository_id=repository_id,
            file_id=file_id,
            symbol_id=None,
            content=content,
            start_line=start_line,
            end_line=(
                start_line
                + len(lines)
                - 1
            ),
        )

    @staticmethod
    def _get_source_range(
        lines: list[str],
        start_line: int,
        end_line: int,
    ) -> str:
        start_index = max(
            start_line - 1,
            0,
        )

        end_index = min(
            end_line,
            len(lines),
        )

        return "\n".join(
            lines[start_index:end_index]
        )

    @staticmethod
    def _is_markdown_heading(
        line: str,
    ) -> bool:
        stripped = line.strip()

        return (
            stripped.startswith("#")
            and not stripped.startswith("###")
        ) or stripped.startswith(
            "##"
        )

    @staticmethod
    def _read_lines(
        file_path: Path,
    ) -> list[str]:
        try:
            return file_path.read_text(
                encoding="utf-8"
            ).splitlines()

        except (
            UnicodeDecodeError,
            OSError,
        ):
            return []

    @staticmethod
    def _clear_existing_chunks(
        db: Session,
        repository_id: int,
    ) -> None:
        statement = (
            select(CodeChunk)
            .where(
                CodeChunk.repository_id
                == repository_id
            )
        )

        existing_chunks = db.scalars(
            statement
        ).all()

        for chunk in existing_chunks:
            db.delete(chunk)

        db.flush()