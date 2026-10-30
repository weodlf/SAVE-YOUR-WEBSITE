from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ScanLog(Base):
    __tablename__ = 'scan_logs'
    __table_args__ = {'sqlite_autoincrement': True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_type: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    result_summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True
    )
