import asyncio
import json

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from orchestrator.models import DeploymentEvent, DeploymentStatus, GitHubTokenRequest, RequirementMessage
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


async def run_session_workflow(session_id: str, request: RequirementMessage) -> None:
    session = store.get(session_id)
    updated = await workflow().run_automatic(session, request)
    store.save(updated)


async def auto_deploy_after_delay(session_id: str, delay_seconds: int) -> None:
    await asyncio.sleep(delay_seconds)
    session = store.get(session_id)
    if session.status != DeploymentStatus.awaiting_approval or session.resources.get("approval_mode") == "manual":
        return
    session.approved = True
    session.resources["auto_deploy_started"] = True
    session.add_event(
        DeploymentEvent(
            session_id=session.id,
            agent="deployer",
            severity="info",
            status=DeploymentStatus.deploying,
            message="No approval response was received during the wait window. Auto-deployment is starting.",
        )
    )
    updated = await workflow().deploy(session)
    store.save(updated)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions")
def create_session() -> dict[str, object]:
    return store.create().model_dump(mode="json")


@app.get("/sessions")
def list_sessions() -> list[dict[str, object]]:
    return [session.model_dump(mode="json") for session in store.list()]


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


@app.post("/sessions/{session_id}/chat")
async def chat(session_id: str, request: RequirementMessage, background_tasks: BackgroundTasks) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().chat(session, request)
    saved = store.save(updated)
    delay = saved.resources.get("auto_deploy_after_seconds")
    if delay and not saved.resources.get("auto_deploy_scheduled"):
        saved.resources["auto_deploy_scheduled"] = True
        store.save(saved)
        background_tasks.add_task(auto_deploy_after_delay, session_id, int(delay))
    return saved.model_dump(mode="json")


@app.post("/sessions/{session_id}/github-token")
def set_github_token(session_id: str, request: GitHubTokenRequest) -> dict[str, object]:
    session = store.get(session_id)
    session.github_token = request.token.strip()
    session.github_token_configured = bool(session.github_token)
    session.add_event(
        DeploymentEvent(
            session_id=session.id,
            agent="provisioner",
            severity="success",
            status=session.status or DeploymentStatus.requirements,
            message="GitHub token saved for this project session. It will be used for repository creation and is not shown again.",
        )
    )
    return store.save(session).model_dump(mode="json")


@app.post("/sessions/{session_id}/run")
async def run_automatic(session_id: str, request: RequirementMessage) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().run_automatic(session, request)
    return store.save(updated).model_dump(mode="json")


@app.post("/sessions/{session_id}/run-background")
def run_automatic_background(
    session_id: str,
    request: RequirementMessage,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    session = store.get(session_id)
    session.add_event(
        DeploymentEvent(
            session_id=session.id,
            agent="requirements",
            severity="info",
            status=DeploymentStatus.deploying,
            message="Agent workflow started. Logs will update while the project runs.",
        )
    )
    store.save(session)
    background_tasks.add_task(run_session_workflow, session_id, request)
    return session.model_dump(mode="json")


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


@app.post("/sessions/{session_id}/ec2-httpd-test")
async def ec2_httpd_test(session_id: str) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().run_ec2_httpd_test(session)
    return store.save(updated).model_dump(mode="json")


@app.post("/sessions/{session_id}/destroy")
async def destroy(session_id: str) -> dict[str, object]:
    session = store.get(session_id)
    updated = await workflow().destroy(session)
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
