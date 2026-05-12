"use client";

import type { DeploymentSession } from "@agentcore-deployer/contracts";
import { useEffect, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/backend";

export default function AdminPage() {
  const [projects, setProjects] = useState<DeploymentSession[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function call<T>(path: string, init?: RequestInit): Promise<T> {
    setBusy(true);
    try {
      const response = await fetch(`${apiBaseUrl}${path}`, {
        headers: { "Content-Type": "application/json", ...init?.headers },
        ...init,
      });
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()) as T;
    } finally {
      setBusy(false);
    }
  }

  async function refreshProjects() {
    const sessions = await call<DeploymentSession[]>("/sessions");
    setProjects(sessions);
    if (!sessionId && sessions[0]) setSessionId(sessions[0].id);
  }

  async function createSession() {
    const session = await call<DeploymentSession>("/sessions", { method: "POST" });
    setSessionId(session.id);
    await refreshProjects();
  }

  async function saveGithubToken() {
    if (!sessionId || !githubToken) return;
    await call<DeploymentSession>(`/sessions/${sessionId}/github-token`, {
      method: "POST",
      body: JSON.stringify({ token: githubToken }),
    });
    setGithubToken("");
    setNotice("GitHub token saved for the selected backend session. It is not returned by the API.");
    await refreshProjects();
  }

  useEffect(() => {
    refreshProjects().catch(() => undefined);
  }, []);

  return (
    <main className="shell">
      <section className="stack" style={{ marginBottom: 24 }}>
        <div className="row">
          <h1 style={{ margin: 0 }}>Admin</h1>
          <a className="button secondary" href="/">Chat</a>
          <a className="button secondary" href="/projects">Projects</a>
        </div>
        <p className="muted">Manage session-scoped GitHub access for repository create/read/update operations.</p>
      </section>

      <section className="panel stack">
        <h2>GitHub Access Token</h2>
        <label className="field">
          Target session
          <select value={sessionId} onChange={(event) => setSessionId(event.target.value)}>
            <option value="">Select a session</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {(project.spec?.name ?? project.id.slice(0, 8))} - {project.status}
              </option>
            ))}
          </select>
        </label>
        <div className="row">
          <button className="button secondary" onClick={createSession} disabled={busy}>
            Create Admin Session
          </button>
          <button className="button secondary" onClick={refreshProjects} disabled={busy}>
            Refresh Sessions
          </button>
        </div>
        <label className="field">
          GitHub API token
          <input
            type="password"
            value={githubToken}
            placeholder="Fine-scoped token for repository create/read/update"
            onChange={(event) => setGithubToken(event.target.value)}
          />
        </label>
        <button className="button" onClick={saveGithubToken} disabled={!githubToken || !sessionId || busy}>
          Save Token
        </button>
        {notice ? <p className="status">{notice}</p> : null}
      </section>
    </main>
  );
}
