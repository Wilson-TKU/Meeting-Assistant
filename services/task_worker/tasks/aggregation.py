import uuid
from typing import Optional

from sqlalchemy import select

from core.config import settings
from core.database import get_session
from core.models.task import Task, TaskStatus
from core.models.summary import Summary
from core.aggregation.aggregator import MeetingAggregator
from core.models.prompt import SystemPrompt
from core.llm.litellm_client import LiteLLMClient
from core.storage import get_storage
from services.task_worker.celery_app import celery_app


@celery_app.task(name="services.task_worker.tasks.aggregation.run_aggregation", bind=True)
def run_aggregation(
    self,
    task_id: str,
    meeting_ids: list[str],
    labels: Optional[list[str]] = None,
    llm_base_url: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    prompt_id: Optional[str] = None,
    extra_system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
):
    _task_id = uuid.UUID(task_id)
    _meeting_ids = [uuid.UUID(mid) for mid in meeting_ids]

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

    try:
        with get_session() as session:
            summaries_text = []
            for mid in _meeting_ids:
                s = session.execute(
                    select(Summary)
                    .where(Summary.meeting_id == mid, Summary.is_aggregated == False)
                    .order_by(Summary.created_at.desc())
                ).scalars().first()
                if not s:
                    raise ValueError(f"No summary for meeting {mid}")
                summaries_text.append(s.content)

        resolved_system_prompt = None
        if prompt_id:
            with get_session() as session:
                db_prompt = session.get(SystemPrompt, uuid.UUID(prompt_id))
                if db_prompt:
                    resolved_system_prompt = db_prompt.template

        aggregator = MeetingAggregator(llm=llm)
        result = aggregator.aggregate(
            summaries_text,
            meeting_ids=meeting_ids,
            meeting_labels=labels,
            system_prompt=resolved_system_prompt,
            extra_system_prompt=extra_system_prompt,
        )

        agg_key = f"aggregations/meetings_{'_'.join(str(m) for m in meeting_ids)}.md"
        storage.upload(agg_key, result.content.encode("utf-8"), "text/markdown")

        with get_session() as session:
            agg_summary = Summary(
                content=result.content,
                content_ref=agg_key,
                is_aggregated=True,
                prompt_id=uuid.UUID(prompt_id) if prompt_id else None,
            )
            agg_summary.source_meeting_ids = meeting_ids
            session.add(agg_summary)
            session.flush()
            summary_id = str(agg_summary.id)

            task = session.get(Task, _task_id)
            task.status = TaskStatus.DONE
            task.output_ref = summary_id

    except Exception as e:
        with get_session() as session:
            task = session.get(Task, _task_id)
            task.status = TaskStatus.FAILED
            task.error = str(e)
        raise
