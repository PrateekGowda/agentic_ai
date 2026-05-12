"use client";

import type { DeploymentSession } from "@agentcore-deployer/contracts";
import { FormEvent, useEffect, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/backend";

const defaultAnswers = {
  name: "my-aws-project",
  description: "Serverless API with encrypted storage and compliance reporting",
  owner: "platform@example.com",
  cost_center: "CC-1001",
  region: "us-east-1",
  environment: "dev",
  workload_type: "s3-lambda-api",
  compliance_profile: "baseline",
  github_visibility: "private",
};

export default function Home() {
  const [session, setSession] = useState<DeploymentSession | null>(null);
  const [projects, setProjects] = useState<DeploymentSession[]>([]);
  const [answers, setAnswers] = useState(defaultAnswers);
  const [chatMessage, setChatMessage] = useState(
    "Create a basic EC2 instance in us-east-1, install httpd, owner platform@example.com, cost center CC-1001",
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
    const quiet = quietFlag;
    if (!quiet) setBusy(true);
    try {
      const response = await fetch(`${apiBaseUrl}${path}`, {
        headers: { "Content-Type": "application/json", ...requestInit.headers },
        ...requestInit,
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      return (await response.json()) as T;
    } finally {
      if (!quiet) setBusy(false);
    }
  }

  async function startSession() {
    const created = await call<DeploymentSession>("/sessions", { method: "POST" });
    setSession(created);
    await refreshProjects();
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
    setNotice("GitHub token saved for this project session. Now click Send To Agent.");
    await refreshProjects();
  }

  async function submitRequirements(event: FormEvent) {
    event.preventDefault();
    const current = await ensureSession();
    const updated = await call<DeploymentSession>(`/sessions/${current.id}/requirements`, {
      method: "POST",
      body: JSON.stringify({ message: "Create infrastructure from UI answers.", answers }),
    });
    setSession(updated);
    await refreshProjects();
  }

  async function runStep(step: "provision" | "compliance" | "approve" | "deploy") {
    if (!session) return;
    setSession(await call<DeploymentSession>(`/sessions/${session.id}/${step}`, { method: "POST" }));
    await refreshProjects();
  }

  async function runAutomatically() {
    const current = await ensureSession();
    const chatAnswers = {
      ...answers,
      workload_type:
        chatMessage.toLowerCase().includes("ec2") || chatMessage.toLowerCase().includes("httpd")
          ? "ec2-httpd"
          : answers.workload_type,
    };
    const updated = await call<DeploymentSession>(`/sessions/${current.id}/run-background`, {
      method: "POST",
      body: JSON.stringify({
        message: chatMessage,
        answers: chatAnswers,
      }),
    });
    setSession(updated);
    setNotice("Agent workflow started. Watch Execution Logs for live progress.");
    await refreshProjects();
  }

  async function runEc2HttpdTest() {
    const current = await ensureSession();
    const updated = await call<DeploymentSession>(`/sessions/${current.id}/ec2-httpd-test`, {
      method: "POST",
    });
    setSession(updated);
    await refreshProjects();
  }

  async function destroyProject() {
    if (!session || !confirm("Destroy resources tracked by this project?")) return;
    const updated = await call<DeploymentSession>(`/sessions/${session.id}/destroy`, {
      method: "POST",
    });
    setSession(updated);
    await refreshProjects();
  }

  return (
    <main className="shell">
      <section className="stack" style={{ marginBottom: 24 }}>
        <div className="row">
          <h1 style={{ margin: 0 }}>AgentCore Multi-Agent Deployer</h1>
          <span className="status">{session?.status ?? "not_started"}</span>
          <a className="button secondary" href="/projects">
            Existing Projects
          </a>
        </div>
        <p className="muted" style={{ maxWidth: 780 }}>
          Enter a project name, chat with the agent, create a GitHub repository, store state in
          S3, view logs, and destroy tracked resources when finished.
        </p>
      </section>

      <div className="grid">
        <section className="panel stack">
          <form className="stack" onSubmit={submitRequirements}>
            <h2>1. Requirement Fields</h2>
            <label className="field">
              Project Name
              <input value={answers.name} onChange={(event) => setAnswers({ ...answers, name: event.target.value })} />
            </label>
            <label className="field">
              Description
              <textarea
                value={answers.description}
                onChange={(event) => setAnswers({ ...answers, description: event.target.value })}
              />
            </label>
            <div className="row">
              <label className="field" style={{ flex: 1 }}>
                Owner
                <input value={answers.owner} onChange={(event) => setAnswers({ ...answers, owner: event.target.value })} />
              </label>
              <label className="field" style={{ flex: 1 }}>
                Cost Center
                <input
                  value={answers.cost_center}
                  onChange={(event) => setAnswers({ ...answers, cost_center: event.target.value })}
                />
              </label>
            </div>
            <div className="row">
              <label className="field" style={{ flex: 1 }}>
                Region
                <input value={answers.region} onChange={(event) => setAnswers({ ...answers, region: event.target.value })} />
              </label>
              <label className="field" style={{ flex: 1 }}>
                Environment
                <select
                  value={answers.environment}
                  onChange={(event) => setAnswers({ ...answers, environment: event.target.value })}
                >
                  <option value="dev">dev</option>
                  <option value="test">test</option>
                  <option value="stage">stage</option>
                  <option value="prod">prod</option>
                </select>
              </label>
            </div>
            <button className="button" type="submit" disabled={busy}>
              Send to Requirement Agent
            </button>
          </form>
          {notice ? <p className="status">{notice}</p> : null}
          <section className="stack" style={{ borderTop: "1px solid var(--line)", paddingTop: 14 }}>
            <h2>2. GitHub Access</h2>
            <label className="field">
              GitHub API token
              <input
                type="password"
                value={githubToken}
                placeholder="Paste a fine-scoped token; it is stored only for this session"
                onChange={(event) => setGithubToken(event.target.value)}
              />
            </label>
            <button className="button secondary" onClick={saveGithubToken} disabled={!githubToken || busy}>
              Save Token For Session
            </button>
            <p className="muted">
              Save the token after selecting or creating a project session. The token is only held in
              backend memory for this session and is not displayed again.
            </p>
          </section>
          <section className="stack">
            <h2>3. Chatbot Request</h2>
            <label className="field">
              Tell the agents what to build
              <textarea
                value={chatMessage}
                onChange={(event) => setChatMessage(event.target.value)}
                rows={5}
              />
            </label>
            <div className="row">
              <button className="button secondary" onClick={startSession} disabled={busy}>
                New Project Session
              </button>
              <button className="button" onClick={runAutomatically} disabled={busy}>
                Send To Agent
              </button>
              <button className="button secondary" onClick={runEc2HttpdTest} disabled={!session || busy}>
                Run EC2 httpd Test
              </button>
              <button className="button secondary" onClick={destroyProject} disabled={!session || busy}>
                Destroy Project
              </button>
            </div>
            <p className="muted">
              Example: `Create a basic EC2 instance, install httpd, show me the URL, then allow destroy`.
            </p>
          </section>
        </section>

        <aside className="stack">
          <section className="panel stack">
            <div className="row">
              <h2 style={{ margin: 0 }}>Projects</h2>
              <button className="button secondary" onClick={refreshProjects} disabled={busy}>
                Refresh
              </button>
            </div>
            {projects.length ? (
              <div className="stack">
                {projects.map((project) => (
                  <button
                    key={project.id}
                    className="button secondary"
                    style={{ textAlign: "left" }}
                    onClick={() => setSession(project)}
                  >
                    {(project.spec?.name ?? project.id.slice(0, 8))} - {project.status}
                  </button>
                ))}
              </div>
            ) : (
              <p className="muted">No projects loaded. Start a session or refresh.</p>
            )}
          </section>

          <section className="panel stack">
            <h2>Repository and Artifacts</h2>
            <p>
              GitHub:{" "}
              {session?.repository_url ? (
                <a href={session.repository_url} target="_blank">
                  {session.repository_url}
                </a>
              ) : (
                <span className="muted">Not created yet</span>
              )}
            </p>
            <p>
              Architecture:{" "}
              {session?.architecture_doc_url ? <a href={session.architecture_doc_url}>ARCHITECTURE.md</a> : <span className="muted">Pending</span>}
            </p>
            <p>
              Compliance:{" "}
              {session?.compliance_report_url ? <a href={session.compliance_report_url}>COMPLIANCE.md</a> : <span className="muted">Pending</span>}
            </p>
            <p>
              EC2 httpd URL:{" "}
              {session?.resources?.ec2_httpd?.url ? (
                <a href={String(session.resources.ec2_httpd.url)} target="_blank">
                  {String(session.resources.ec2_httpd.url)}
                </a>
              ) : (
                <span className="muted">Not created</span>
              )}
            </p>
            <p>
              S3 State:{" "}
              {session?.resources?.project_state?.state_uri ? (
                <code>{String(session.resources.project_state.state_uri)}</code>
              ) : (
                <span className="muted">State is written after the first agent step.</span>
              )}
            </p>
            <p>
              S3 Logs:{" "}
              {session?.resources?.project_state?.logs_uri ? (
                <code>{String(session.resources.project_state.logs_uri)}</code>
              ) : (
                <span className="muted">Logs are written after the first agent step.</span>
              )}
            </p>
          </section>

          <section className="panel stack">
            <h2>Compliance Findings</h2>
            {session?.findings.length ? (
              session.findings.map((finding) => (
                <div key={finding.id} className="event warning">
                  <strong>{finding.title}</strong>
                  <p className="muted">{finding.remediation}</p>
                </div>
              ))
            ) : (
              <p className="muted">No findings yet.</p>
            )}
          </section>

          <section className="panel stack">
            <h2>Execution Logs</h2>
            {session?.events.length ? (
              session.events.map((event) => (
                <div key={event.id} className={`event ${event.severity}`}>
                  <strong>
                    {new Date(event.timestamp).toLocaleTimeString()} - {event.agent} - {event.status}
                  </strong>
                  <p>{event.message}</p>
                  {Object.keys(event.details ?? {}).length > 0 ? (
                    <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
                      {JSON.stringify(event.details, null, 2)}
                    </pre>
                  ) : null}
                  <small className="muted">{new Date(event.timestamp).toLocaleString()}</small>
                </div>
              ))
            ) : (
              <p className="muted">Start a session to see agent activity.</p>
            )}
          </section>
        </aside>
      </div>
    </main>
  );
}
