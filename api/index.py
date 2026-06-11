"""Minimal LangGraph-server-compatible API for the course Q&A chat.

Implements just enough of the LangGraph Platform REST protocol for
agent-chat-ui's `useStream` hook: thread creation/search/history and the
SSE run-stream endpoint. The LLM call itself is plain LangChain (v1).

Designed for serverless (Vercel): the client sends the full message
history on every run, so no persistent state is required. A best-effort
in-memory thread store keeps history endpoints working while the
function instance stays warm.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

ASSISTANT_ID = os.environ.get("ASSISTANT_ID", "agent")
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")

REPO_ROOT = Path(__file__).resolve().parent.parent
COURSE_INFO_PATH = REPO_ROOT / "course_info.md"

SYSTEM_PROMPT_TEMPLATE = """\
You are the course assistant for the class described below. Your sole \
purpose is to answer questions about this class for its students, who are \
often on their phones, so keep answers short, direct, and easy to read on \
a small screen.

Rules:
- Answer only from the course information below. If the answer is not \
covered there, say you don't know and point the student to the course \
staff or syllabus instead of guessing.
- Do not answer questions unrelated to the class; politely steer the \
conversation back to course topics.
- Today's date is {today}. Use it when questions involve deadlines or \
the schedule.

Course information:

{course_info}
"""


def load_system_prompt() -> str:
    course_info = COURSE_INFO_PATH.read_text(encoding="utf-8")
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    return SYSTEM_PROMPT_TEMPLATE.format(today=today, course_info=course_info)


def get_model() -> BaseChatModel:
    """Build the chat model. Set FAKE_MODEL=1 to run without an API key
    (e.g., local UI development); tests monkeypatch this instead."""
    if os.environ.get("FAKE_MODEL"):
        from itertools import cycle

        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langchain_core.messages import AIMessage

        return GenericFakeChatModel(
            messages=cycle([AIMessage(content="(fake model) I would answer from course_info.md here.")])
        )
    return init_chat_model(MODEL_NAME, model_provider="google_genai")


# --- In-memory thread store (best effort on serverless) -------------------

THREADS: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_thread(thread_id: str | None = None, metadata: dict | None = None) -> dict:
    tid = thread_id or str(uuid.uuid4())
    thread = {
        "thread_id": tid,
        "created_at": _now(),
        "updated_at": _now(),
        "metadata": {"graph_id": ASSISTANT_ID, **(metadata or {})},
        "status": "idle",
        "values": {},
    }
    THREADS[tid] = thread
    return thread


def _get_or_create_thread(thread_id: str) -> dict:
    return THREADS.get(thread_id) or _make_thread(thread_id)


def _thread_state(thread: dict) -> dict:
    """A single ThreadState snapshot in the shape useStream expects."""
    return {
        "values": thread["values"],
        "next": [],
        "tasks": [],
        "metadata": thread["metadata"],
        "created_at": thread["updated_at"],
        "checkpoint": {
            "thread_id": thread["thread_id"],
            "checkpoint_ns": "",
            "checkpoint_id": thread.get("checkpoint_id", str(uuid.uuid4())),
        },
        "parent_checkpoint": None,
    }


# --- Message (de)serialization ---------------------------------------------


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return str(content)


def to_langchain_messages(raw_messages: list[dict]) -> list[Any]:
    messages: list[Any] = []
    for raw in raw_messages:
        text = _text_from_content(raw.get("content"))
        if raw.get("type") == "human":
            messages.append(HumanMessage(content=text, id=raw.get("id")))
        elif raw.get("type") == "ai" and text:
            messages.append(AIMessage(content=text, id=raw.get("id")))
        # tool/system messages are not produced by this UI; ignore others
    return messages


def serialize_message(raw: dict) -> dict:
    return {
        "id": raw.get("id") or str(uuid.uuid4()),
        "type": raw["type"],
        "content": raw["content"],
        "tool_calls": [],
        "response_metadata": {},
    }


# --- SSE helpers ------------------------------------------------------------


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def run_stream(thread: dict, input_messages: list[dict], run_id: str) -> AsyncIterator[str]:
    yield sse("metadata", {"run_id": run_id, "attempt": 1})

    messages = [serialize_message(m) for m in input_messages]
    yield sse("values", {"messages": messages})

    try:
        lc_messages = [SystemMessage(content=load_system_prompt())]
        lc_messages.extend(to_langchain_messages(messages))

        model = get_model()
        ai_id = str(uuid.uuid4())
        answer = ""
        async for chunk in model.astream(lc_messages):
            piece = str(chunk.text)
            if not piece:
                continue
            answer += piece
            ai_message = {
                "id": ai_id,
                "type": "ai",
                "content": answer,
                "tool_calls": [],
                "response_metadata": {},
            }
            yield sse("values", {"messages": [*messages, ai_message]})

        final_messages = [
            *messages,
            {
                "id": ai_id,
                "type": "ai",
                "content": answer,
                "tool_calls": [],
                "response_metadata": {"model_name": MODEL_NAME},
            },
        ]
        thread["values"] = {"messages": final_messages}
        thread["checkpoint_id"] = str(uuid.uuid4())
        thread["updated_at"] = _now()
        yield sse("values", thread["values"])
    except Exception as exc:  # surface as a stream error event, not a 500
        yield sse("error", {"error": type(exc).__name__, "message": str(exc)})


# --- Routes -----------------------------------------------------------------

router = APIRouter()


@router.get("/info")
async def info() -> dict:
    return {"flags": {"assistants": False, "crons": False}}


@router.post("/threads")
async def create_thread(request: Request) -> dict:
    body = await request.json()
    return _make_thread(body.get("thread_id"), body.get("metadata"))


@router.post("/threads/search")
async def search_threads(request: Request) -> list[dict]:
    body = await request.json()
    limit = body.get("limit", 20)
    threads = sorted(THREADS.values(), key=lambda t: t["updated_at"], reverse=True)
    return threads[:limit]


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str) -> dict:
    return _get_or_create_thread(thread_id)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str) -> None:
    THREADS.pop(thread_id, None)


@router.get("/threads/{thread_id}/state")
async def get_thread_state(thread_id: str) -> dict:
    return _thread_state(_get_or_create_thread(thread_id))


@router.post("/threads/{thread_id}/history")
async def get_thread_history(thread_id: str) -> list[dict]:
    thread = _get_or_create_thread(thread_id)
    if thread["values"].get("messages"):
        return [_thread_state(thread)]
    return []


@router.post("/threads/{thread_id}/runs/stream")
async def stream_run(thread_id: str, request: Request) -> StreamingResponse:
    body = await request.json()
    thread = _get_or_create_thread(thread_id)
    run_id = str(uuid.uuid4())

    input_data = body.get("input")
    if input_data and input_data.get("messages"):
        input_messages = input_data["messages"]
        if not isinstance(input_messages, list):
            input_messages = [input_messages]
    else:
        # Regenerate: re-run from stored history, dropping trailing AI turns.
        input_messages = list(thread["values"].get("messages", []))
        while input_messages and input_messages[-1].get("type") == "ai":
            input_messages.pop()

    async def error_stream() -> AsyncIterator[str]:
        yield sse("metadata", {"run_id": run_id, "attempt": 1})
        yield sse(
            "error",
            {
                "error": "BadRequest",
                "message": "No input messages found. Please send a new message.",
            },
        )

    stream = run_stream(thread, input_messages, run_id) if input_messages else error_stream()
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Content-Location": f"/threads/{thread_id}/runs/{run_id}",
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


app = FastAPI(title="DS295R Course Assistant API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# On Vercel the function receives the original request path, which is always
# prefixed with /api; locally (uvicorn) the same prefix is used so the Next.js
# dev rewrite can proxy transparently. The bare routes help direct testing.
app.include_router(router, prefix="/api")
app.include_router(router)
