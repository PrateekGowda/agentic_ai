"use client";

import type { DeploymentEvent, DeploymentSession, DeploymentStatus } from "@agentcore-deployer/contracts";
import { useEffect, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/backend";

const processSteps: { label: string; statuses: DeploymentStatus[] }[] = [
  { label: "Requirement gathering", statuses: ["requirements", "customizing"] },
  { label: "Architecture and Terraform generation", statuses: ["customizing", "repo_created"] },
  { label: "GitHub repository create/read/update", statuses: ["repo_created"] },
  { label: "Compliance check", statuses: ["policy_check", "awaiting_approval", "blocked"] },
  { label: "Chat approval", statuses: ["awaiting_approval"] },
  { label: "Deployment", statuses: ["deploying", "succeeded"] },
  { label: "Project documentation", statuses: ["succeeded"] },
];

export default function Home() {
  const [session, setSession] = useState<DeploymentSession | null>(null);
  const [projects, setProjects] = useState<DeploymentSession[]>([]);
  const [chatMessage, setChatMessage] = useState(
    "Create an S3 bucket in us-east-1 dev. Project name is ui-s3-demo. Owner platform@example.com. Cost center CC-1001.",
  );
  const [githubToken, setGithubToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    refreshProjects().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!session?.id) return;
    const timer = window.setInterval(async () => {
      const updated = await call<DeploymentSession>(`/sessions/${session.id}`, { quiet: true } as RequestInit & { quiet?: boolean });
      setSession(updated);
      setProjects((current) => [updated, ...current.filter((project) => project.id !== updated.id)]);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [session?.id]);

  async function call<T>(path: string, init?: RequestInit & { quiet?: boolean }): Promise<T> {
    const { quiet: quietFlag, ...requestInit } = init ?? {};
    if (!quietFlag) setBusy(true);
    try {
      const response = await fetch(`${apiBaseUrl}${path}`, {
        headers: { "Content-Type": "application/json", ...requestInit.headers },
        ...requestInit,
      });
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()) as T;
    } finally {
      if (!quietFlag) setBusy(false);
    }
  }

  async function refreshProjects() {
    setProjects(await call<DeploymentSession[]>("/sessions"));
  }

  async function ensureSession() {
    const current = session ?? (await call<DeploymentSession>("/sessions", { method: "POST" }));
    setSession(current);
    await refreshProjects();
    return current;
  }

  async function saveGithubToken() {
    const current = await ensureSession();
    const updated = await call<DeploymentSession>(`/sessions/${current.id}/github-token`, {
      method: "POST",
      body: JSON.stringify({ token: githubToken }),
    });
    setGithubToken("");
    setSession(updated);
    setNotice("GitHub token saved. The provisioner can now create, update, and read the project repository.");
    await refreshProjects();
  }

  async function sendChat() {
    const current = await ensureSession();
    const updated = await call<DeploymentSession>(`/sessions/${current.id}/chat`, {
      method: "POST",
      body: JSON.stringify({ message: chatMessage, answers: {} }),
    });
    setSession(updated);
    setNotice("Chat sent. If approval is needed, type approve after reviewing the GitHub architecture and logs.");
    await refreshProjects();
  }

  async function destroyProject(project = session) {
    if (!project || !confirm("Destroy resources tracked by this project?")) return;
    const updated = await call<DeploymentSession>(`/sessions/${project.id}/destroy`, { method: "POST" });
    setSession(updated);
    await refreshProjects();
  }

  function stepState(step: { statuses: DeploymentStatus[] }, events: DeploymentEvent[]) {
    if (!session) return "pending";
    if (events.some((event) => event.severity === "error" && step.statuses.includes(event.status))) return "failed";
    if (events.some((event) => step.statuses.includes(event.status))) return "done";
    return "pending";
  }

  return (
    <main className="shell">
      <section className="stack" style={{ marginBottom: 24 }}>
        <div className="row">
          <h1 style={{ margin: 0 }}>AgentCore Multi-Agent Deployer</h1>
          <span className="status">{session?.status ?? "not_started"}</span>
          <a className="button secondary" href="/projects">
            Existing Projects Page
          </a>
        </div>
        <p className="muted" style={{ maxWidth: 860 }}>
          Chat with the agents to collect inputs, create architecture and Terraform, push code/docs to GitHub,
          approve deployment, create resources, and view project history.
        </p>
      </section>

      <section className="panel stack" style={{ marginBottom: 24 }}>
        <h2>Chatbot</h2>
        <label className="field">
          Tell the agents what to build
          <textarea value={chatMessage} onChange={(event) => setChatMessage(event.target.value)} rows={5} />
        </label>
        <div className="row">
          <button className="button secondary" onClick={ensureSession} disabled={busy}>
            New / Select Session
          </button>
          <button className="button" onClick={sendChat} disabled={busy || !chatMessage.trim()}>
            Send To Agent
          </button>
          <button className="button secondary" onClick={() => setChatMessage("approve")} disabled={!session || busy}>
            Type Approve
          </button>
          <button className="button secondary" onClick={() => destroyProject()} disabled={!session || busy}>
            Destroy Project
          </button>
        </div>
        <p className="muted">
          Test case: ask `Create an S3 bucket in us-east-1 dev. Project name is ui-s3-demo. Owner platform@example.com. Cost center CC-1001.`
        </p>
        {notice ? <p className="status">{notice}</p> : null}
      </section>

      <div className="grid">
        <section className="stack">
          <section className="panel stack">
            <h2>GitHub Access</h2>
            <label className="field">
              GitHub API token
              <input
                type="password"
                value={githubToken}
                placeholder="Paste a fine-scoped token for repository create/read/update"
                onChange={(event) => setGithubToken(event.target.value)}
              />
            </label>
            <button className="button secondary" onClick={saveGithubToken} disabled={!githubToken || busy}>
              Save Token For Session
            </button>
            <p className="muted">The token is held only for this backend session and is not returned by the API.</p>
          </section>

          <section className="panel stack">
            <h2>Step-By-Step Process</h2>
            {processSteps.map((step) => (
              <div key={step.label} className={`event ${stepState(step, session?.events ?? []) === "done" ? "success" : "info"}`}>
                <strong>{step.label}</strong>
                <p className="muted">{stepState(step, session?.events ?? [])}</p>
              </div>
            ))}
          </section>

          <section className="panel stack">
            <h2>Existing Projects</h2>
            <button className="button secondary" onClick={refreshProjects} disabled={busy}>
              Refresh Projects
            </button>
            {projects.length ? (
              projects.map((project) => (
                <div key={project.id} className="event">
                  <button className="button secondary" style={{ textAlign: "left" }} onClick={() => setSession(project)}>
                    {(project.spec?.name ?? project.id.slice(0, 8))} - {project.status}
                  </button>
                  <p className="muted">{project.repository_url ?? "GitHub repository pending"}</p>
                </div>
              ))
            ) : (
              <p className="muted">No projects loaded yet.</p>
            )}
          </section>
        </section>

        <aside className="stack">
          <section className="panel stack">
            <h2>Repository and Artifacts</h2>
            <p>GitHub: {session?.repository_url ? <a href={session.repository_url}>{session.repository_url}</a> : <span className="muted">Not created yet</span>}</p>
            <p>Architecture: {session?.architecture_doc_url ? <a href={session.architecture_doc_url}>ARCHITECTURE.md</a> : <span className="muted">Pending</span>}</p>
            <p>Compliance: {session?.compliance_report_url ? <a href={session.compliance_report_url}>COMPLIANCE.md</a> : <span className="muted">Pending</span>}</p>
            <p>S3 Bucket: {session?.resources?.s3_bucket?.bucket_uri ? <code>{String(session.resources.s3_bucket.bucket_uri)}</code> : <span className="muted">Not created</span>}</p>
            <p>S3 State: {session?.resources?.project_state?.state_uri ? <code>{String(session.resources.project_state.state_uri)}</code> : <span className="muted">Pending</span>}</p>
            <p>S3 Logs: {session?.resources?.project_state?.logs_uri ? <code>{String(session.resources.project_state.logs_uri)}</code> : <span className="muted">Pending</span>}</p>
            <p>AgentCore Memory: {session?.resources?.agentcore_memory_id ? <code>{String(session.resources.agentcore_memory_id)}</code> : <span className="muted">Not configured</span>}</p>
          </section>

          <section className="panel stack">
            <h2>Execution Logs</h2>
            {session?.events.length ? (
              session.events.map((event) => (
                <div key={event.id} className={`event ${event.severity}`}>
                  <strong>{new Date(event.timestamp).toLocaleTimeString()} - {event.agent} - {event.status}</strong>
                  <p>{event.message}</p>
                  {Object.keys(event.details ?? {}).length ? (
                    <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{JSON.stringify(event.details, null, 2)}</pre>
                  ) : null}
                </div>
              ))
            ) : (
              <p className="muted">Start chatting to see agent activity.</p>
            )}
          </section>
        </aside>
      </div>
    </main>
  );
}
