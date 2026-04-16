import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.summary import Summary
from core.models.meeting import Meeting
from core.models.task import Task, TaskType, TaskStatus
from core.document.generator import DocumentGenerator, Asset
from services.gateway.dependencies import get_db, get_storage
from services.task_worker.celery_app import celery_app

router = APIRouter()


class AggregateRequest(BaseModel):
    meeting_ids: list[uuid.UUID]
    labels: list[str] | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    prompt_id: uuid.UUID | None = None
    extra_system_prompt: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


class TaskQueuedResponse(BaseModel):
    task_id: uuid.UUID
    status: str = "pending"


class AggregationResponse(BaseModel):
    id: uuid.UUID
    source_meeting_ids: list[str]
    content: str
    created_at: str
    prompt_id: Optional[uuid.UUID]

    @classmethod
    def from_orm(cls, s: Summary) -> "AggregationResponse":
        return cls(
            id=s.id,
            source_meeting_ids=s.source_meeting_ids,
            content=s.content,
            created_at=s.created_at.isoformat(),
            prompt_id=s.prompt_id,
        )


@router.post("/aggregate", response_model=TaskQueuedResponse, status_code=202)
async def start_aggregation(payload: AggregateRequest, db: AsyncSession = Depends(get_db)):
    if len(payload.meeting_ids) < 2:
        raise HTTPException(status_code=422, detail="At least 2 meeting IDs required.")

    for mid in payload.meeting_ids:
        result = await db.execute(
            select(Summary).where(Summary.meeting_id == mid, Summary.is_aggregated == False)
        )
        if not result.scalars().first():
            raise HTTPException(status_code=404, detail=f"No summary for meeting {mid}.")

    task = Task(task_type=TaskType.AGGREGATION, status=TaskStatus.PENDING)
    db.add(task)
    await db.flush()

    celery_result = celery_app.send_task(
        "services.task_worker.tasks.aggregation.run_aggregation",
        args=[str(task.id), [str(mid) for mid in payload.meeting_ids], payload.labels,
              payload.llm_base_url, payload.llm_model, payload.llm_api_key,
              str(payload.prompt_id) if payload.prompt_id else None,
              payload.extra_system_prompt, payload.temperature, payload.top_p, payload.max_tokens],
    )
    task.celery_task_id = celery_result.id
    await db.commit()

    return TaskQueuedResponse(task_id=task.id)


@router.get("/aggregations", response_model=list[AggregationResponse])
async def list_aggregations(db: AsyncSession = Depends(get_db)):
    """List all cross-meeting aggregation results."""
    result = await db.execute(
        select(Summary).where(Summary.is_aggregated == True).order_by(Summary.created_at.desc())
    )
    return [AggregationResponse.from_orm(s) for s in result.scalars().all()]


@router.get("/aggregations/{summary_id}", response_model=AggregationResponse)
async def get_aggregation(summary_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get a specific aggregation result."""
    summary = await db.get(Summary, summary_id)
    if not summary or not summary.is_aggregated:
        raise HTTPException(status_code=404, detail="Aggregation not found")
    return AggregationResponse.from_orm(summary)


@router.post("/generate/{summary_id}", response_class=PlainTextResponse)
async def generate_document(
    summary_id: uuid.UUID,
    title: Optional[str] = None,
    assets: list[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    storage=Depends(get_storage),
):
    summary = await db.get(Summary, summary_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")

    doc_title = title
    if not doc_title and summary.meeting_id:
        meeting = await db.get(Meeting, summary.meeting_id)
        doc_title = meeting.title if meeting else f"Summary {summary_id}"
    elif not doc_title:
        doc_title = f"Summary {summary_id}"

    asset_objects = []
    for upload in assets:
        data = await upload.read()
        asset_key = f"assets/{summary_id}/{upload.filename}"
        storage.upload(asset_key, data, upload.content_type or "application/octet-stream")
        url = storage.get_url(asset_key)
        asset_obj = Asset.from_path(upload.filename or "")
        asset_obj.url = url
        asset_objects.append(asset_obj)

    generator = DocumentGenerator()
    return generator.generate(title=doc_title, content=summary.content, assets=asset_objects)
