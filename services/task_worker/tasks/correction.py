import uuid
import tempfile
import os
from typing import Optional

from core.config import settings
from core.database import get_session
from core.models.task import Task, TaskStatus
from core.models.transcript import Transcript
from core.correction.corrector import TranscriptCorrector
from core.correction.dictionary import CorrectionDictionary
from core.llm.litellm_client import LiteLLMClient
from core.storage import get_storage
from services.task_worker.celery_app import celery_app


@celery_app.task(name="services.task_worker.tasks.correction.run_correction", bind=True)
def run_correction(
    self,
    task_id: str,
    transcript_id: str,
    raw_ref: str,
    dictionary_key: Optional[str] = None,
    terms: dict[str, str] | None = None,
    llm_base_url: Optional[str] = None,
    llm_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    max_tokens: Optional[int] = None,
):
    """Correct a transcript using dictionary + LLM."""
    _task_id = uuid.UUID(task_id)
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

    try:
        raw_text = storage.download(raw_ref).decode("utf-8")

        dictionary = CorrectionDictionary()
        if dictionary_key and storage.exists(dictionary_key):
            dict_data = storage.download(dictionary_key).decode("utf-8")
            suffix = ".json" if dictionary_key.endswith(".json") else ".csv"
            with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as tmp:
                tmp.write(dict_data)
                tmp_path = tmp.name
            try:
                if suffix == ".json":
                    dictionary.load_json(tmp_path)
                else:
                    dictionary.load_csv(tmp_path)
            finally:
                os.unlink(tmp_path)

        if terms:
            for wrong, correct in terms.items():
                dictionary.add(wrong, correct)

        corrector = TranscriptCorrector(llm=llm, dictionary=dictionary)
        corrected = corrector.correct(raw_text)

        corrected_ref = raw_ref.replace("/raw.txt", "/corrected.txt")
        storage.upload(corrected_ref, corrected.encode("utf-8"), "text/plain")

        with get_session() as session:
            transcript = session.get(Transcript, _transcript_id)
            transcript.corrected_ref = corrected_ref

            task = session.get(Task, _task_id)
            task.status = TaskStatus.DONE
            task.output_ref = corrected_ref

    except Exception as e:
        with get_session() as session:
            task = session.get(Task, _task_id)
            task.status = TaskStatus.FAILED
            task.error = str(e)
        raise
