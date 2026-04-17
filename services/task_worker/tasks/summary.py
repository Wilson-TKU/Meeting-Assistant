import uuid
from typing import Optional

from core.config import settings
from core.database import get_session
from core.models.task import Task, TaskStatus
from core.models.meeting import Meeting
from core.models.summary import Summary
from core.models.prompt import SystemPrompt
from core.summary.templates import get_template, MeetingContext
from core.summary.summarizer import MeetingSummarizer
from core.llm.litellm_client import LiteLLMClient
from core.storage import get_storage
from services.task_worker.celery_app import celery_app


@celery_app.task(name="services.task_worker.tasks.summary.run_summary", bind=True)
def run_summary(
    self,
    task_id: str,
    meeting_id: str,
    transcript_id: str,
    transcript_ref: str,
    scene: str = "general",
    participants: list[str] | None = None,
    topics: list[str] | None = None,
    custom_system_prompt: str | None = None,
    prompt_id: str | None = None,
    llm_base_url: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    extra_system_prompt: str | None = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
):
    _task_id = uuid.UUID(task_id)
    _meeting_id = uuid.UUID(meeting_id)
    _transcript_id = uuid.UUID(transcript_id)

    storage = get_storage()
    llm = LiteLLMClient(
        model=llm_model or settings.llm_model,
        api_key=llm_api_key or settings.llm_api_key,
        api_base=llm_base_url or settings.llm_base_url,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
    )

    with get_session() as session:
        task = session.get(Task, _task_id)
        task.status = TaskStatus.RUNNING

        meeting = session.get(Meeting, _meeting_id)
        ctx = MeetingContext(
            date=meeting.date.strftime("%Y-%m-%d"),
            meeting_type=scene,
            participants=participants or [],
            topics=topics or [],
        )

    try:
        transcript_text = storage.download(transcript_ref).decode("utf-8")
        template = get_template(scene)
        summarizer = MeetingSummarizer(llm=llm)

        # Priority: custom_system_prompt (inline) > prompt_id (DB) > scene template
        resolved_system_prompt = custom_system_prompt
        if not resolved_system_prompt and prompt_id:
            with get_session() as session:
                db_prompt = session.get(SystemPrompt, uuid.UUID(prompt_id))
                if db_prompt:
                    resolved_system_prompt = db_prompt.template

        if extra_system_prompt:
            base = resolved_system_prompt or template.system_prompt
            resolved_system_prompt = f"{base}\n\n{extra_system_prompt}"

        content = summarizer.summarize(transcript_text, template, ctx, system_prompt=resolved_system_prompt)

        summary_key = f"meetings/{meeting_id}/summary.md"
        storage.upload(summary_key, content.encode("utf-8"), "text/markdown")

        with get_session() as session:
            summary = Summary(
                meeting_id=_meeting_id,
                transcript_id=_transcript_id,
                content=content,
                content_ref=summary_key,
                prompt_id=uuid.UUID(prompt_id) if prompt_id else None,
                scene=scene,
                model_name=llm.model,
            )
            session.add(summary)
            session.flush()

            task = session.get(Task, _task_id)
            task.status = TaskStatus.DONE
            task.output_ref = summary_key

    except Exception as e:
        with get_session() as session:
            task = session.get(Task, _task_id)
            task.status = TaskStatus.FAILED
            task.error = str(e)
        raise
