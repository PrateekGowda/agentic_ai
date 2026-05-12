"use client";

import type { DeploymentSession } from "@agentcore-deployer/contracts";
import { FormEvent, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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
  const [answers, setAnswers] = useState(defaultAnswers);
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
    setSession(await call<DeploymentSession>("/sessions", { method: "POST" }));
  }

  async function submitRequirements(event: FormEvent) {
    event.preventDefault();
    const current = session ?? (await call<DeploymentSession>("/sessions", { method: "POST" }));
    const updated = await call<DeploymentSession>(`/sessions/${current.id}/requirements`, {
      method: "POST",
      body: JSON.stringify({ message: "Create infrastructure from UI answers.", answers }),
    });
    setSession(updated);
  }

  async function runStep(step: "provision" | "compliance" | "approve" | "deploy") {
    if (!session) return;
    setSession(await call<DeploymentSession>(`/sessions/${session.id}/${step}`, { method: "POST" }));
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

          <form className="stack" onSubmit={submitRequirements}>
            <h2>Requirements</h2>
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
        </section>

        <aside className="stack">
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
            <h2>Deployment Timeline</h2>
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
