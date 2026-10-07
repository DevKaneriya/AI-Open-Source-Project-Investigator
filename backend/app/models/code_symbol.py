from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class CodeSymbol(Base):
    __tablename__ = "code_symbols"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    file_id: Mapped[int] = mapped_column(
        ForeignKey("repository_files.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    symbol_type: Mapped[str] = mapped_column(String(50), nullable=False)
    qualified_name: Mapped[str | None] = mapped_column(String(500), nullable=True)

    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)

    signature: Mapped[str | None] = mapped_column(Text, nullable=True)