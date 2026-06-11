"""Tests for the LangGraph-protocol FastAPI backend, using a fake LLM."""

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.index as backend  # noqa: E402


class FailingModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "failing"

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        raise ValueError("boom: model unavailable")


@pytest.fixture
def client(monkeypatch) -> httpx.AsyncClient:
    backend.THREADS.clear()
    monkeypatch.setattr(
        backend,
        "get_model",
        lambda: GenericFakeChatModel(messages=iter([AIMessage(content="Office hours are Mondays at 3pm.")])),
    )
    transport = httpx.ASGITransport(app=backend.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def parse_sse(text: str) -> list[tuple[str, str]]:
    events = []
    for block in text.split("\n\n"):
        event, data = None, []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data.append(line[len("data: ") :])
        if event:
            events.append((event, "\n".join(data)))
    return events


async def test_info(client):
    for path in ("/info", "/api/info"):
        res = await client.get(path)
        assert res.status_code == 200


async def test_create_and_search_threads(client):
    res = await client.post("/api/threads", json={"metadata": {"graph_id": "agent"}})
    assert res.status_code == 200
    thread_id = res.json()["thread_id"]

    res = await client.post("/api/threads/search", json={"metadata": {}, "limit": 100})
    assert any(t["thread_id"] == thread_id for t in res.json())


async def test_run_stream_and_history(client):
    res = await client.post("/api/threads", json={})
    thread_id = res.json()["thread_id"]

    res = await client.post(
        f"/api/threads/{thread_id}/runs/stream",
        json={
            "assistant_id": "agent",
            "input": {"messages": [{"id": "h1", "type": "human", "content": "When are office hours?"}]},
            "stream_mode": ["values"],
        },
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert f"/threads/{thread_id}/runs/" in res.headers["content-location"]

    events = parse_sse(res.text)
    assert events[0][0] == "metadata"
    assert all(e in ("metadata", "values") for e, _ in events)

    import json

    final = json.loads(events[-1][1])
    messages = final["messages"]
    assert messages[0]["type"] == "human"
    assert messages[-1]["type"] == "ai"
    assert "Office hours are Mondays at 3pm." in messages[-1]["content"]

    # History now returns one state with the conversation.
    res = await client.post(f"/api/threads/{thread_id}/history", json={"limit": 10})
    states = res.json()
    assert len(states) == 1
    assert states[0]["values"]["messages"][-1]["content"].endswith("3pm.")
    assert states[0]["checkpoint"]["checkpoint_id"]


async def test_regenerate_uses_stored_history(client):
    res = await client.post("/api/threads", json={})
    thread_id = res.json()["thread_id"]

    await client.post(
        f"/api/threads/{thread_id}/runs/stream",
        json={"input": {"messages": [{"id": "h1", "type": "human", "content": "hi"}]}},
    )

    # Regenerate sends no input; backend should re-run from stored history.
    import api.index as backend_module

    backend_module.get_model = lambda: GenericFakeChatModel(
        messages=iter([AIMessage(content="Regenerated answer.")])
    )
    res = await client.post(f"/api/threads/{thread_id}/runs/stream", json={"input": None})
    assert "Regenerated answer." in res.text


async def test_model_error_streams_error_event(client, monkeypatch):
    monkeypatch.setattr(backend, "get_model", lambda: FailingModel())
    res = await client.post("/api/threads", json={})
    thread_id = res.json()["thread_id"]

    res = await client.post(
        f"/api/threads/{thread_id}/runs/stream",
        json={"input": {"messages": [{"id": "h1", "type": "human", "content": "hi"}]}},
    )
    assert res.status_code == 200
    events = parse_sse(res.text)
    assert events[-1][0] == "error"
    assert "boom" in events[-1][1]


async def test_empty_input_streams_error_event(client):
    res = await client.post("/api/threads", json={})
    thread_id = res.json()["thread_id"]
    res = await client.post(f"/api/threads/{thread_id}/runs/stream", json={"input": None})
    events = parse_sse(res.text)
    assert events[-1][0] == "error"
