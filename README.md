# Meeting Assistant

AI-powered meeting assistant — transcribes audio, corrects transcripts with LLM + custom dictionary, and generates structured meeting summaries. Multiple summaries can be aggregated into cross-meeting reports.

## Features

- **Speech-to-Text** via [faster-whisper](https://github.com/SYSTRAN/faster-whisper), running locally
- **LLM Transcript Correction** — contextual repair of STT errors with any OpenAI-compatible server, Ollama, or Anthropic
- **Custom Correction Dictionary** — deterministic substitution for proper nouns, names, and technical terms (CSV / JSON)
- **Structured Summaries** — built-in scene templates (weekly standup, project review, client interview, general) plus user-defined prompts
- **Cross-Meeting Aggregation** — synthesize multiple meeting summaries into a single cross-meeting report
- **Document Export** — Markdown output with optional attached images and documents
- **Web UI + REST API** — browser frontend served from the gateway, fully async FastAPI backend
- **CLI** — standalone file-based tool; works without Docker, points at any STT/LLM server

## Architecture

```
Audio File
    │
    ▼
STT Service (faster-whisper)
    │
    ▼
Correction Pipeline
  ① Dictionary substitution  ← deterministic, proper nouns first
  ② LLM contextual repair    ← handles long-tail STT errors
    │
    ▼
LLM Summarizer + Prompt Template
    │
    ├──► Single Meeting Summary ──► Markdown Export
    │
    └──► Multiple Summaries ──────► Aggregator ──► Cross-meeting Report
```

Long-running operations (STT, correction, summarization) run asynchronously via Celery. Use `GET /tasks/{id}` to poll or `GET /tasks/{id}/stream` (SSE) for real-time updates.

### Services

| Container | Port | Role |
|-----------|------|------|
| `gateway` | 8000 | FastAPI REST API + Web UI (`GET /`) |
| `stt_service` | 8080 | faster-whisper HTTP server (GPU) |
| `task_worker_stt` | — | Celery worker — STT queue |
| `task_worker_llm` | — | Celery worker — LLM queue (correction / summary / aggregation) |
| `redis` | 6379 | Celery broker |

## Quickstart

**Requirements:** Docker + Docker Compose, and an LLM (local or cloud).

```bash
git clone <repo-url>
cd meeting_assistant

cp .env.example .env
# Edit .env — set LLM_MODEL, LLM_BASE_URL, LLM_API_KEY
# See .env.example for examples (vLLM, Ollama, OpenAI, Anthropic)

docker compose up -d
```

- Web UI: **http://localhost:8000**
- Swagger docs: **http://localhost:8000/docs**

> After editing `.env`, always run `docker compose up -d` (not `restart`) to re-read env vars.

## CLI

The CLI is file-based and requires no database. It sends HTTP requests to your STT and LLM servers.

```bash
pip install -e ".[cli]"

# Transcribe audio → stdout or file
meeting-assistant transcribe audio.mp3 \
  --stt-url http://localhost:8080

# Correct a transcript with LLM + optional dictionary
meeting-assistant correct transcript.txt \
  --llm-url http://localhost:8002/v1 \
  --model openai/Qwen/Qwen3-4B \
  --terms "阿里巴巴=Alibaba,吉他=GitHub" \
  --output corrected.txt

# Generate a summary
meeting-assistant summarize corrected.txt \
  --llm-url http://localhost:8002/v1 \
  --model openai/Qwen/Qwen3-4B \
  --scene weekly_standup \
  --output summary.md

# Aggregate multiple summaries into a report
meeting-assistant aggregate week1.md week2.md week3.md \
  --llm-url http://localhost:8002/v1 \
  --model openai/Qwen/Qwen3-4B \
  --output report.md
```

## LLM Configuration

Edit `.env` — no code changes needed to switch providers.

```env
# Local vLLM / any OpenAI-compatible server
LLM_MODEL=openai/Qwen/Qwen3-4B
LLM_BASE_URL=http://host.docker.internal:8002/v1
LLM_API_KEY=no-key       # local servers: use "no-key" (cannot be empty)

# OpenAI
LLM_MODEL=openai/gpt-4o
LLM_API_KEY=sk-...

# Ollama
LLM_MODEL=ollama/llama3.2
LLM_BASE_URL=http://host.docker.internal:11434

# Anthropic
LLM_MODEL=anthropic/claude-sonnet-4-6
LLM_API_KEY=sk-ant-...
```

## Testing

All tests run without any external services (in-memory SQLite, mocked storage and Celery):

```bash
pip install -e ".[dev]"
pytest                           # 163 tests
pytest --cov=core                # with coverage report
```

## Documentation

| | |
|--|--|
| [Setup Guide](docs/setup.md) | **完整部署手冊（繁中）** — 從全新 Ubuntu 機器開始：硬體 / OS / Docker / GPU 安裝、第一場會議步驟、故障排除、備份 |
| [Usage Guide](docs/usage.md) | CLI commands, Web UI walkthrough, REST API examples with curl |
| [Development Guide](docs/development.md) | Dev setup, project structure, adding features |
| [Architecture](docs/architecture.md) | Design decisions, data flow, abstraction layers, upgrade paths |

## License

MIT
