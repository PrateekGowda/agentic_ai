"use client";

import type { DeploymentSession } from "@agentcore-deployer/contracts";
import { useEffect, useState } from "react";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/backend";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<DeploymentSession[]>([]);
  const [selected, setSelected] = useState<DeploymentSession | null>(null);
  const [busy, setBusy] = useState(false);

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

  async function refresh() {
    const sessions = await call<DeploymentSession[]>("/sessions");
    setProjects(sessions);
    if (selected) {
      setSelected(sessions.find((project) => project.id === selected.id) ?? selected);
    }
  }

  async function destroyProject(project: DeploymentSession) {
    if (!confirm(`Destroy tracked resources for ${project.spec?.name ?? project.id}?`)) return;
    const updated = await call<DeploymentSession>(`/sessions/${project.id}/destroy`, { method: "POST" });
    setSelected(updated);
    await refresh();
  }

  useEffect(() => {
    refresh().catch(() => undefined);
  }, []);

  return (
    <main className="shell">
      <section className="stack" style={{ marginBottom: 24 }}>
        <div className="row">
          <h1 style={{ margin: 0 }}>Existing Projects</h1>
          <a className="button secondary" href="/">
            New Request
          </a>
          <a className="button secondary" href="/admin">
            Admin
          </a>
          <button className="button secondary" onClick={refresh} disabled={busy}>
            Refresh
          </button>
        </div>
        <p className="muted">Select a project to inspect logs, S3 state, GitHub links, resources, and destroy options.</p>
      </section>

      <div className="grid">
        <section className="panel stack">
          <h2>Projects</h2>
          {projects.length ? (
            projects.map((project) => (
              <div key={project.id} className="event">
                <button
                  className="button secondary"
                  style={{ textAlign: "left" }}
                  onClick={() => setSelected(project)}
                >
                  {(project.spec?.name ?? project.id.slice(0, 8))} - {project.status}
                </button>
                <button className="button secondary" onClick={() => destroyProject(project)} disabled={busy}>
                  Destroy
                </button>
              </div>
            ))
          ) : (
            <p className="muted">No projects found in the current running service memory.</p>
          )}
        </section>

        <aside className="stack">
          <section className="panel stack">
            <h2>Selected Project</h2>
            {selected ? (
              <>
                <p><strong>Name:</strong> {selected.spec?.name ?? selected.id}</p>
                <p><strong>Status:</strong> {selected.status}</p>
                <p><strong>GitHub:</strong> {selected.repository_url ? <a href={selected.repository_url}>{selected.repository_url}</a> : "Not created"}</p>
                <p><strong>S3 State:</strong> <code>{String(selected.resources?.project_state?.state_uri ?? "Not written")}</code></p>
                <p><strong>S3 Logs:</strong> <code>{String(selected.resources?.project_state?.logs_uri ?? "Not written")}</code></p>
                <p><strong>EC2 URL:</strong> {selected.resources?.ec2_httpd?.url ? <a href={String(selected.resources.ec2_httpd.url)}>{String(selected.resources.ec2_httpd.url)}</a> : "Not created"}</p>
                <button className="button secondary" onClick={() => destroyProject(selected)} disabled={busy}>
                  Destroy Tracked Resources
                </button>
              </>
            ) : (
              <p className="muted">Select a project from the list.</p>
            )}
          </section>

          <section className="panel stack">
            <h2>Execution Logs</h2>
            {selected?.events?.length ? (
              <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
                {selected.events.map(formatTerminalEvent).join("\n")}
              </pre>
            ) : (
              <p className="muted">No logs for the selected project.</p>
            )}
          </section>
        </aside>
      </div>
    </main>
  );
}

function formatTerminalEvent(event: DeploymentSession["events"][number]) {
  const details = Object.keys(event.details ?? {}).length ? `\n${JSON.stringify(event.details, null, 2)}` : "";
  return `[${new Date(event.timestamp).toLocaleTimeString()}] ${event.agent} ${event.status} ${event.severity}\n$ ${event.message}${details}`;
}
