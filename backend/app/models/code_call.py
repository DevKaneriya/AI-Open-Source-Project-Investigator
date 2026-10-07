from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base


class CodeCall(Base):
    __tablename__ = "code_calls"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    symbol_id: Mapped[int] = mapped_column(
        ForeignKey("code_symbols.id", ondelete="CASCADE"),
        nullable=False,
    )

    called_name: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )