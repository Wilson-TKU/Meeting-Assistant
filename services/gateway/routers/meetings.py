import uuid
from datetime import datetime, date as date_type
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, field_validator
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.models.meeting import Meeting
from core.models.task import Task, TaskType, TaskStatus
from core.models.transcript import Transcript
from core.models.summary import Summary
from services.gateway.dependencies import get_db, get_storage
from services.task_worker.celery_app import celery_app

router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────

class MeetingCreate(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None   # YYYY-MM-DD, defaults to today if omitted
    language: str = "zh"

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v):
        if not v:
            return None
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except (ValueError, TypeError):
            raise ValueError("date must be in YYYY-MM-DD format")


class MeetingUpdate(BaseModel):
    title: Optional[str] = None
    date: Optional[str] = None
    language: Optional[str] = None

    @field_validator("date", mode="before")
    @classmethod
    def validate_date(cls, v):
        if not v:
            return None
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except (ValueError, TypeError):
            raise ValueError("date must be in YYYY-MM-DD format")


class MeetingResponse(BaseModel):
    id: uuid.UUID
    title: str
    date: str
    language: str

    @classmethod
    def from_orm(cls, m: Meeting) -> "MeetingResponse":
        return cls(
            id=m.id,
            title=m.title,
            date=m.date.strftime("%Y-%m-%d"),
            language=m.language,
        )


class TaskQueuedResponse(BaseModel):
    task_id: uuid.UUID
    status: str = "pending"


class AudioUploadResponse(BaseModel):
    task_id: uuid.UUID
    transcript_id: uuid.UUID
    status: str = "pending"


class TranscriptResponse(BaseModel):
    id: uuid.UUID
    meeting_id: uuid.UUID
    language: str
    duration_seconds: Optional[float]
    raw: Optional[str]
    corrected: Optional[str]
    created_at: str


class TranscriptCreate(BaseModel):
    text: str
    language: str = "zh"


class TranscriptUpdate(BaseModel):
    corrected: str


class CorrectRequest(BaseModel):
    terms: dict[str, str] = {}
    dictionary_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None


class SummarizeRequest(BaseModel):
    scene: str = "general"
    use_corrected: bool = True
    participants: list[str] = []
    topics: list[str] = []
    prompt_id: Optional[uuid.UUID] = None    # user-defined prompt, overrides scene
    custom_system_prompt: Optional[str] = None  # inline override, highest priority
    extra_system_prompt: Optional[str] = None   # appended to base prompt (e.g. member roles)
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None


class SummaryResponse(BaseModel):
    id: uuid.UUID
    meeting_id: Optional[uuid.UUID]
    transcript_id: Optional[uuid.UUID]
    content: str
    is_aggregated: bool
    created_at: str

    @classmethod
    def from_orm(cls, s: Summary) -> "SummaryResponse":
        return cls(
            id=s.id,
            meeting_id=s.meeting_id,
            transcript_id=s.transcript_id,
            content=s.content,
            is_aggregated=s.is_aggregated,
            created_at=s.created_at.isoformat(),
        )


# ── Meetings ───────────────────────────────────────────────────────────────

@router.post("", response_model=MeetingResponse, status_code=201)
async def create_meeting(payload: MeetingCreate, db: AsyncSession = Depends(get_db)):
    if payload.date:
        meeting_date = datetime.strptime(payload.date, "%Y-%m-%d")
    else:
        meeting_date = datetime.combine(date_type.today(), datetime.min.time())

    title = payload.title or meeting_date.strftime("會議 %Y-%m-%d")
    meeting = Meeting(title=title, date=meeting_date, language=payload.language)
    db.add(meeting)
    await db.commit()
    await db.refresh(meeting)
    return MeetingResponse.from_orm(meeting)


@router.get("", response_model=list[MeetingResponse])
async def list_meetings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Meeting).order_by(Meeting.date.desc()))
    return [MeetingResponse.from_orm(m) for m in result.scalars().all()]


@router.get("/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(meeting_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return MeetingResponse.from_orm(meeting)


@router.put("/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(meeting_id: uuid.UUID, payload: MeetingUpdate, db: AsyncSession = Depends(get_db)):
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if payload.title is not None:
        meeting.title = payload.title
    if payload.date is not None:
        meeting.date = datetime.strptime(payload.date, "%Y-%m-%d")
    if payload.language is not None:
        meeting.language = payload.language
    await db.commit()
    await db.refresh(meeting)
    return MeetingResponse.from_orm(meeting)


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(meeting_id: uuid.UUID, db: AsyncSession = Depends(get_db), storage=Depends(get_storage)):
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    transcripts = (await db.execute(select(Transcript).where(Transcript.meeting_id == meeting_id))).scalars().all()
    for t in transcripts:
        for ref in [t.audio_ref, t.raw_ref, t.corrected_ref]:
            if ref:
                try:
                    storage.delete(ref)
                except Exception:
                    pass

    summaries = (await db.execute(select(Summary).where(Summary.meeting_id == meeting_id))).scalars().all()
    for s in summaries:
        if s.content_ref:
            try:
                storage.delete(s.content_ref)
            except Exception:
                pass

    await db.execute(delete(Summary).where(Summary.meeting_id == meeting_id))
    await db.execute(delete(Task).where(Task.meeting_id == meeting_id))
    await db.execute(delete(Transcript).where(Transcript.meeting_id == meeting_id))
    await db.delete(meeting)
    await db.commit()


# ── Transcripts ────────────────────────────────────────────────────────────

@router.post("/{meeting_id}/audio", response_model=AudioUploadResponse, status_code=202)
async def upload_audio(
    meeting_id: uuid.UUID,
    audio: UploadFile = File(...),
    language: str = "zh",
    stt_url: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    storage=Depends(get_storage),
):
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    audio_key = f"meetings/{meeting_id}/audio/{audio.filename}"
    data = await audio.read()
    storage.upload(audio_key, data, audio.content_type or "audio/mpeg")

    transcript = Transcript(meeting_id=meeting_id, audio_ref=audio_key, language=language)
    db.add(transcript)
    await db.flush()

    task = Task(meeting_id=meeting_id, task_type=TaskType.STT, input_ref=audio_key, status=TaskStatus.PENDING)
    db.add(task)
    await db.flush()

    celery_result = celery_app.send_task(
        "services.task_worker.tasks.stt.run_stt",
        args=[str(task.id), str(transcript.id), audio_key, language, stt_url],
    )
    task.celery_task_id = celery_result.id
    await db.commit()

    return AudioUploadResponse(task_id=task.id, transcript_id=transcript.id)


@router.post("/{meeting_id}/transcript", response_model=TranscriptResponse, status_code=201)
async def create_transcript_from_text(
    meeting_id: uuid.UUID,
    payload: TranscriptCreate,
    db: AsyncSession = Depends(get_db),
    storage=Depends(get_storage),
):
    """Create a transcript directly from text, skipping STT."""
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    raw_key = f"meetings/{meeting_id}/transcripts/raw.txt"
    corrected_key = f"meetings/{meeting_id}/transcripts/corrected.txt"
    storage.upload(raw_key, payload.text.encode("utf-8"), "text/plain")
    storage.upload(corrected_key, payload.text.encode("utf-8"), "text/plain")

    transcript = Transcript(
        meeting_id=meeting_id,
        language=payload.language,
        raw_ref=raw_key,
        corrected_ref=corrected_key,
    )
    db.add(transcript)
    await db.commit()
    await db.refresh(transcript)

    return TranscriptResponse(
        id=transcript.id,
        meeting_id=transcript.meeting_id,
        language=transcript.language,
        duration_seconds=None,
        raw=payload.text,
        corrected=payload.text,
        created_at=transcript.created_at.isoformat(),
    )


@router.get("/{meeting_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(meeting_id: uuid.UUID, db: AsyncSession = Depends(get_db), storage=Depends(get_storage)):
    result = await db.execute(
        select(Transcript).where(Transcript.meeting_id == meeting_id).order_by(Transcript.id.desc())
    )
    transcript = result.scalars().first()
    if not transcript:
        raise HTTPException(status_code=404, detail="No transcript found.")

    def _safe_download(ref):
        if not ref:
            return None
        try:
            return storage.download(ref).decode("utf-8")
        except (KeyError, FileNotFoundError):
            return None

    raw = _safe_download(transcript.raw_ref)
    corrected = _safe_download(transcript.corrected_ref)

    return TranscriptResponse(
        id=transcript.id,
        meeting_id=transcript.meeting_id,
        language=transcript.language,
        duration_seconds=transcript.duration_seconds,
        raw=raw,
        corrected=corrected,
        created_at=transcript.created_at.isoformat(),
    )


@router.put("/{meeting_id}/transcript", response_model=TranscriptResponse)
async def update_transcript(meeting_id: uuid.UUID, payload: TranscriptUpdate, db: AsyncSession = Depends(get_db), storage=Depends(get_storage)):
    result = await db.execute(
        select(Transcript).where(Transcript.meeting_id == meeting_id).order_by(Transcript.id.desc())
    )
    transcript = result.scalars().first()
    if not transcript:
        raise HTTPException(status_code=404, detail="No transcript found.")

    corrected_ref = f"meetings/{meeting_id}/transcripts/corrected.txt"
    storage.upload(corrected_ref, payload.corrected.encode("utf-8"), "text/plain")
    transcript.corrected_ref = corrected_ref
    await db.commit()

    raw = storage.download(transcript.raw_ref).decode("utf-8") if transcript.raw_ref else None
    return TranscriptResponse(
        id=transcript.id,
        meeting_id=transcript.meeting_id,
        language=transcript.language,
        duration_seconds=transcript.duration_seconds,
        raw=raw,
        corrected=payload.corrected,
        created_at=transcript.created_at.isoformat(),
    )


# ── Correction ─────────────────────────────────────────────────────────────

@router.post("/{meeting_id}/correct", response_model=TaskQueuedResponse, status_code=202)
async def start_correction(
    meeting_id: uuid.UUID,
    payload: CorrectRequest = CorrectRequest(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Transcript).where(Transcript.meeting_id == meeting_id).order_by(Transcript.id.desc())
    )
    transcript = result.scalars().first()
    if not transcript or not transcript.raw_ref:
        raise HTTPException(status_code=404, detail="No transcript found. Upload audio first.")

    task = Task(meeting_id=meeting_id, task_type=TaskType.CORRECTION, input_ref=transcript.raw_ref, status=TaskStatus.PENDING)
    db.add(task)
    await db.flush()

    celery_result = celery_app.send_task(
        "services.task_worker.tasks.correction.run_correction",
        args=[str(task.id), str(transcript.id), transcript.raw_ref, payload.dictionary_key, payload.terms,
              payload.llm_base_url, payload.llm_model, payload.llm_api_key,
              payload.temperature, payload.top_p, payload.max_tokens],
    )
    task.celery_task_id = celery_result.id
    await db.commit()

    return TaskQueuedResponse(task_id=task.id)


# ── Summary ────────────────────────────────────────────────────────────────

@router.post("/{meeting_id}/summarize", response_model=TaskQueuedResponse, status_code=202)
async def start_summary(
    meeting_id: uuid.UUID,
    payload: SummarizeRequest = SummarizeRequest(),
    db: AsyncSession = Depends(get_db),
):
    meeting = await db.get(Meeting, meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    result = await db.execute(
        select(Transcript).where(Transcript.meeting_id == meeting_id).order_by(Transcript.id.desc())
    )
    transcript = result.scalars().first()
    if not transcript:
        raise HTTPException(status_code=404, detail="No transcript found.")

    ref = transcript.corrected_ref if payload.use_corrected and transcript.corrected_ref else transcript.raw_ref
    if not ref:
        raise HTTPException(status_code=404, detail="No transcript text available.")

    task = Task(meeting_id=meeting_id, task_type=TaskType.SUMMARY, input_ref=ref, status=TaskStatus.PENDING)
    db.add(task)
    await db.flush()

    celery_result = celery_app.send_task(
        "services.task_worker.tasks.summary.run_summary",
        args=[str(task.id), str(meeting_id), str(transcript.id), ref, payload.scene,
              payload.participants, payload.topics, payload.custom_system_prompt,
              str(payload.prompt_id) if payload.prompt_id else None,
              payload.llm_base_url, payload.llm_model, payload.llm_api_key,
              payload.extra_system_prompt, payload.temperature, payload.top_p, payload.max_tokens],
    )
    task.celery_task_id = celery_result.id
    await db.commit()

    return TaskQueuedResponse(task_id=task.id)


@router.get("/{meeting_id}/summary", response_model=SummaryResponse)
async def get_summary(meeting_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Summary)
        .where(Summary.meeting_id == meeting_id, Summary.is_aggregated == False)
        .order_by(Summary.created_at.desc())
    )
    summary = result.scalars().first()
    if not summary:
        raise HTTPException(status_code=404, detail="No summary found for this meeting.")
    return SummaryResponse.from_orm(summary)


@router.get("/{meeting_id}/summaries", response_model=list[SummaryResponse])
async def list_summaries(meeting_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Summary)
        .where(Summary.meeting_id == meeting_id, Summary.is_aggregated == False)
        .order_by(Summary.created_at.desc())
    )
    return [SummaryResponse.from_orm(s) for s in result.scalars().all()]


@router.delete("/{meeting_id}/summaries/{summary_id}", status_code=204)
async def delete_summary(meeting_id: uuid.UUID, summary_id: uuid.UUID, db: AsyncSession = Depends(get_db), storage=Depends(get_storage)):
    result = await db.execute(
        select(Summary).where(Summary.id == summary_id, Summary.meeting_id == meeting_id)
    )
    summary = result.scalars().first()
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found.")
    if summary.content_ref:
        try:
            storage.delete(summary.content_ref)
        except Exception:
            pass
    await db.delete(summary)
    await db.commit()
