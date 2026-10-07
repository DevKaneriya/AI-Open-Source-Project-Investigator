from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class CodeImport(Base):
    __tablename__ = "code_imports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    file_id: Mapped[int] = mapped_column(
        ForeignKey("repository_files.id", ondelete="CASCADE"),
        nullable=False,
    )

    imported_module: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )