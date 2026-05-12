from orchestrator.models import DeploymentEvent, DeploymentSession


class InMemorySessionStore:
    """Local MVP store. Replace with DynamoDB in deployed environments."""

    def __init__(self) -> None:
        self._sessions: dict[str, DeploymentSession] = {}

    def create(self) -> DeploymentSession:
        session = DeploymentSession()
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> DeploymentSession:
        return self._sessions[session_id]

    def save(self, session: DeploymentSession) -> DeploymentSession:
        self._sessions[session.id] = session
        return session

    def add_event(self, session_id: str, event: DeploymentEvent) -> DeploymentSession:
        session = self.get(session_id)
        session.add_event(event)
        return self.save(session)


store = InMemorySessionStore()
