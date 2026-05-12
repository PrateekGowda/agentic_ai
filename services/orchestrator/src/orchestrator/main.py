import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from orchestrator.models import RequirementMessage
from orchestrator.settings import get_settings
from orchestrator.store import store
from orchestrator.workflow import DeploymentWorkflow

app = FastAPI(title="AgentCore Multi-Agent Deployer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def workflow() -> DeploymentWorkflow:
    return DeploymentWorkflow(get_settings())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions")
def create_session() -> dict[str, object]:
    return store.create().model_dump(mode="json")


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, object]:
    try:
        return store.get(session_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@app.post("/sessions/{session_id}/requirements")
async def gather_requirements(session_id: str, request: RequirementMessage) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().gather_requirements(session, request)
    return store.save(updated).model_dump(mode="json")


@app.post("/sessions/{session_id}/run")
async def run_automatic(session_id: str, request: RequirementMessage) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().run_automatic(session, request)
    return store.save(updated).model_dump(mode="json")


@app.post("/sessions/{session_id}/provision")
async def provision(session_id: str) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().provision(session)
    return store.save(updated).model_dump(mode="json")


@app.post("/sessions/{session_id}/compliance")
async def compliance(session_id: str) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().run_compliance(session)
    return store.save(updated).model_dump(mode="json")


@app.post("/sessions/{session_id}/approve")
def approve(session_id: str) -> dict[str, object]:
    session = store.get(session_id)
    session.approved = True
    return store.save(session).model_dump(mode="json")


@app.post("/sessions/{session_id}/deploy")
async def deploy(session_id: str) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().deploy(session)
    return store.save(updated).model_dump(mode="json")


@app.get("/sessions/{session_id}/events")
async def stream_events(session_id: str) -> StreamingResponse:
    async def event_stream():
        last_seen = 0
        while True:
            session = store.get(session_id)
            for event in session.events[last_seen:]:
                yield f"data: {json.dumps(event.model_dump(mode='json'))}\n\n"
            last_seen = len(session.events)
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
