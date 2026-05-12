"use client";

import type { DeploymentSession } from "@agentcore-deployer/contracts";
import { FormEvent, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/backend";

const defaultAnswers = {
  name: "customer-api",
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

  async function call<T>(path: string, init?: RequestInit): Promise<T> {
    setBusy(true);
    try {
      const response = await fetch(`${apiBaseUrl}${path}`, {
        headers: { "Content-Type": "application/json", ...init?.headers },
        ...init,
      });
      if (!response.ok) {
        throw new Error(await response.text());
      }
      return (await response.json()) as T;
    } finally {
      setBusy(false);
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
    const updated = await call<DeploymentSession>(`/sessions/${current.id}/run`, {
      method: "POST",
      body: JSON.stringify({
        message: chatMessage,
        answers: chatAnswers,
      }),
    });
    setSession(updated);
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
        </div>
        <p className="muted" style={{ maxWidth: 780 }}>
          Gather requirements, create a GitHub infrastructure repository, run policy checks, deploy
          Terraform, remediate safe failures, and publish documentation from one workflow.
        </p>
      </section>

      <div className="grid">
        <section className="panel stack">
          <div className="row">
            <button className="button secondary" onClick={startSession} disabled={busy}>
              Start Session
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
            <button className="button" onClick={() => runStep("provision")} disabled={!session?.spec || busy}>
              Create GitHub Repo
            </button>
            <button className="button secondary" onClick={() => runStep("compliance")} disabled={!session?.repositoryUrl || busy}>
              Run Compliance
            </button>
            <button className="button secondary" onClick={() => runStep("approve")} disabled={!session || busy}>
              Approve Apply
            </button>
            <button className="button" onClick={() => runStep("deploy")} disabled={!session || busy}>
              Deploy
            </button>
          </div>

          <section className="stack">
            <h2>Chatbot</h2>
            <label className="field">
              Tell the agents what to build
              <textarea
                value={chatMessage}
                onChange={(event) => setChatMessage(event.target.value)}
                rows={5}
              />
            </label>
            <p className="muted">
              Example: `Create a basic EC2 instance, install httpd, show me the URL, then allow destroy`.
            </p>
          </section>

          <form className="stack" onSubmit={submitRequirements}>
            <h2>Requirement Fields</h2>
            <label className="field">
              Name
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
          <p className="muted">
            Use `Send To Agent` to let the agents gather requirements, generate and commit
            Terraform, run compliance, approve a dev deployment, and publish documentation in one flow.
          </p>
          <section className="stack" style={{ borderTop: "1px solid var(--line)", paddingTop: 14 }}>
            <h2>GitHub Access</h2>
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
              Use a GitHub token or GitHub App token for repository creation. Do not paste SSH
              private keys here; revoke any key that has been shared in chat or logs.
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
              {session?.repositoryUrl ? (
                <a href={session.repositoryUrl} target="_blank">
                  {session.repositoryUrl}
                </a>
              ) : (
                <span className="muted">Not created yet</span>
              )}
            </p>
            <p>
              Architecture:{" "}
              {session?.architectureDocUrl ? <a href={session.architectureDocUrl}>ARCHITECTURE.md</a> : <span className="muted">Pending</span>}
            </p>
            <p>
              Compliance:{" "}
              {session?.complianceReportUrl ? <a href={session.complianceReportUrl}>COMPLIANCE.md</a> : <span className="muted">Pending</span>}
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
                  <strong>{event.agent}</strong>
                  <p>{event.message}</p>
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
