import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, Text, Uuid, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey("meetings.id"), nullable=True)
    transcript_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    scene: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    content_ref: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    _asset_refs: Mapped[Optional[str]] = mapped_column("asset_refs", Text, nullable=True)
    is_aggregated: Mapped[bool] = mapped_column(default=False)
    _source_meeting_ids: Mapped[Optional[str]] = mapped_column("source_meeting_ids", Text, nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def asset_refs(self) -> list[str]:
        if self._asset_refs:
            return json.loads(self._asset_refs)
        return []

    @asset_refs.setter
    def asset_refs(self, value: list[str]) -> None:
        self._asset_refs = json.dumps(value)

    @property
    def source_meeting_ids(self) -> list[str]:
        if self._source_meeting_ids:
            return json.loads(self._source_meeting_ids)
        return []

    @source_meeting_ids.setter
    def source_meeting_ids(self, value: list[str]) -> None:
        self._source_meeting_ids = json.dumps(value)

    def __repr__(self) -> str:
        return f"<Summary id={self.id} meeting_id={self.meeting_id} aggregated={self.is_aggregated}>"
