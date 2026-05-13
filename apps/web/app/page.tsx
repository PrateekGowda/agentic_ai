"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import type { DeploymentSession } from "@agentcore-deployer/contracts";

// ─── Types ───────────────────────────────────────────────────────────────────

interface ChatMessage {
  role: "user" | "assistant" | "thinking";
  content: string;
}

interface SessionMeta {
  id: string;
  label: string;
  status: string;
  projectName?: string;
}

const BASE = "/api/backend";

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function apiPost(path: string, body?: object) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API ${BASE}${path} → ${res.status}`);
  return res.json();
}

async function apiGet(path: string) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`API ${BASE}${path} → ${res.status}`);
  return res.json();
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    succeeded: "bg-green-600",
    failed: "bg-red-600",
    deploying: "bg-blue-500",
    awaiting_approval: "bg-yellow-500",
    requirements: "bg-gray-500",
    destroyed: "bg-purple-600",
  };
  return map[status] ?? "bg-gray-400";
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function Home() {
  // Session list (sidebar)
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  // Active session detail
  const [session, setSession] = useState<DeploymentSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  // Input state
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  // Refs for scroll
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // ── Helpers ──────────────────────────────────────────────────────────────

  const scrollToBottom = useCallback(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  const syncMessages = useCallback((data: DeploymentSession) => {
    const raw: ChatMessage[] = (data.resources?.chat_messages ?? []) as ChatMessage[];
    setMessages(raw);
    scrollToBottom();
  }, [scrollToBottom]);

  const loadSessionDetail = useCallback(async (id: string) => {
    try {
      const data: DeploymentSession = await apiGet(`/sessions/${id}`);
      setSession(data);
      syncMessages(data);
    } catch {
      /* ignore */
    }
  }, [syncMessages]);

  const refreshSessions = useCallback(async () => {
    try {
      const data: DeploymentSession[] = await apiGet(`/sessions`);
      const metas: SessionMeta[] = data
        .slice()
        .reverse()
        .map((s) => ({
          id: s.id,
          label: s.spec?.name ?? `Session ${s.id.slice(0, 8)}`,
          status: s.status,
          projectName: s.spec?.name,
        }));
      setSessions(metas);
    } catch {
      /* ignore */
    }
  }, []);

  // ── Initial load ──────────────────────────────────────────────────────────

  useEffect(() => {
    refreshSessions();
    const interval = setInterval(refreshSessions, 8000);
    return () => clearInterval(interval);
  }, [refreshSessions]);

  useEffect(() => {
    if (!activeId) return;
    loadSessionDetail(activeId);
    const interval = setInterval(() => loadSessionDetail(activeId), 3000);
    return () => clearInterval(interval);
  }, [activeId, loadSessionDetail]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // ── Actions ───────────────────────────────────────────────────────────────

  const createSession = async () => {
    try {
      const data: DeploymentSession = await apiPost(`/sessions`);
      const meta: SessionMeta = {
        id: data.id,
        label: `New session`,
        status: data.status,
      };
      setSessions((prev) => [meta, ...prev]);
      setActiveId(data.id);
      setSession(data);
      setMessages([
        {
          role: "assistant",
          content:
            "Hello! I am your AWS Infrastructure AI Agent.\n\n" +
            "I can help you:\n" +
            "• **Create infrastructure** — S3, Lambda, EC2, VPC, RDS and more\n" +
            "• **Update projects** — change configs, instance types, regions\n" +
            "• **Query your AWS account** — list buckets, instances, functions (read-only)\n" +
            "• **Manage deployments** — approve, destroy tracked resources\n" +
            "• **Answer questions** — architecture advice, best practices, cost estimates\n\n" +
            "What would you like to do?",
        },
      ]);
    } catch (err) {
      console.error(err);
    }
  };

  const selectSession = async (id: string) => {
    setActiveId(id);
    await loadSessionDetail(id);
  };

  const sendMessage = async () => {
    if (!input.trim() || busy || !activeId) return;
    const text = input.trim();
    setInput("");
    setBusy(true);

    // Optimistically add user message
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    // Show thinking bubble
    setMessages((prev) => [...prev, { role: "thinking", content: "Thinking..." }]);
    scrollToBottom();

    try {
      const data: DeploymentSession = await apiPost(`/sessions/${activeId}/chat`, {
        message: text,
        answers: {},
      });
      setSession(data);
      syncMessages(data);
      await refreshSessions();
    } catch (err) {
      setMessages((prev) => [
        ...prev.filter((m) => m.role !== "thinking"),
        { role: "assistant", content: "Sorry, I encountered an error. Please try again." },
      ]);
    } finally {
      setBusy(false);
      textareaRef.current?.focus();
    }
  };

  const approveDeployment = async () => {
    if (!activeId || busy) return;
    setBusy(true);
    setMessages((prev) => [...prev, { role: "thinking", content: "Running Terraform apply and verifying AWS resources..." }]);
    try {
      await apiPost(`/sessions/${activeId}/approve`);
      const deployed: DeploymentSession = await apiPost(`/sessions/${activeId}/deploy`);
      setSession(deployed);
      syncMessages(deployed);
      await refreshSessions();
    } catch {
      setMessages((prev) => [
        ...prev.filter((m) => m.role !== "thinking"),
        { role: "assistant", content: "Approval was sent but deployment encountered an error." },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const stopCurrentRun = async () => {
    if (!activeId || busy === false) return;
    try {
      const data: DeploymentSession = await apiPost(`/sessions/${activeId}/chat`, {
        message: "stop",
        answers: {},
      });
      setSession(data);
      syncMessages(data);
      setBusy(false);
    } catch {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Unable to stop right now. Please try again." },
      ]);
    }
  };

  const clearChat = async () => {
    if (!activeId) return;
    try {
      const data: DeploymentSession = await apiPost(`/sessions/${activeId}/clear`);
      setSession(data);
      setMessages([]);
    } catch {
      setMessages([]);
    }
  };

  const destroySession = async (id: string) => {
    if (!confirm("Destroy all tracked project resources? This cannot be undone.")) return;
    try {
      await apiPost(`/sessions/${id}/destroy`);
      await refreshSessions();
      if (activeId === id) loadSessionDetail(id);
    } catch (err) {
      console.error(err);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const awaitingApproval = session?.status === "awaiting_approval";

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="app-shell">
      {/* ──── SIDEBAR ──── */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <span className="sidebar-logo">⚡ IaC Agent</span>
        </div>

        <button className="new-chat-btn" onClick={createSession}>
          + New Chat
        </button>

        <div className="session-list">
          {sessions.length === 0 && (
            <p className="session-empty">No sessions yet. Start a new chat!</p>
          )}
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`session-item ${activeId === s.id ? "session-item-active" : ""}`}
              onClick={() => selectSession(s.id)}
            >
              <div className="session-item-name">{s.label}</div>
              <div className="session-item-footer">
                <span className={`status-dot ${statusBadge(s.status)}`} />
                <span className="session-item-status">{s.status}</span>
                {(s.status === "succeeded" || s.status === "deploying") && (
                  <button
                    className="destroy-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      destroySession(s.id);
                    }}
                    title="Destroy project"
                  >
                    🗑
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>

        <div className="sidebar-footer">
          <a href="/projects" className="sidebar-link">All Projects</a>
          <a href="/admin" className="sidebar-link">Admin</a>
        </div>
      </aside>

      {/* ──── MAIN AREA ──── */}
      <main className="chat-main">
        {!activeId ? (
          <div className="chat-empty">
            <div className="chat-empty-icon">⚡</div>
            <h2>AWS Infrastructure AI Agent</h2>
            <p>Create or update cloud infrastructure using natural language.</p>
            <button className="new-chat-btn-center" onClick={createSession}>
              Start a new chat
            </button>
          </div>
        ) : (
          <>
            {/* Header */}
            <div className="chat-header">
              <div className="chat-header-title">
                {session?.spec?.name ?? "New conversation"}
                {session?.status && (
                  <span className={`header-status-badge ${statusBadge(session.status)}`}>
                    {session.status}
                  </span>
                )}
              </div>
              <div className="chat-header-actions">
                {session?.repository_url && (
                  <a
                    href={session.repository_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="header-link"
                  >
                    GitHub ↗
                  </a>
                )}
                <button className="clear-btn" onClick={clearChat} title="Clear chat history">
                  Clear
                </button>
              </div>
            </div>

            {/* Messages */}
            <div className="messages-area">
              {messages.map((msg, i) => (
                <MessageBubble key={i} msg={msg} />
              ))}
              <div ref={chatEndRef} />
            </div>

            {/* Approval banner */}
            {awaitingApproval && (
              <div className="approval-banner">
                <span>Infrastructure is ready for review.</span>
                <button
                  className={`approve-btn ${busy ? "approve-btn-disabled" : ""}`}
                  onClick={approveDeployment}
                  disabled={busy}
                >
                  {busy ? "Deploying..." : "✓ Approve & Deploy"}
                </button>
              </div>
            )}

            {/* Composer */}
            <div className="composer">
              <textarea
                ref={textareaRef}
                className="composer-input"
                rows={1}
                placeholder={
                  busy
                    ? "Agent is working..."
                    : "Tell the agents what to build..."
                }
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={busy}
              />
              <button
                className={`send-btn ${busy || !input.trim() ? "send-btn-disabled" : ""}`}
                onClick={sendMessage}
                disabled={busy || !input.trim()}
                title="Send"
              >
                {busy ? (
                  <span className="spinner" />
                ) : (
                  <svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18">
                    <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
                  </svg>
                )}
              </button>
              {busy && (
                <button className="stop-btn" onClick={stopCurrentRun} title="Stop">
                  ■
                </button>
              )}
            </div>

            {/* Execution log */}
            {session && session.events.length > 0 && (
              <ExecutionLog session={session} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: ChatMessage }) {
  if (msg.role === "thinking") {
    return (
      <div className="bubble-row bubble-row-assistant">
        <div className="bubble bubble-thinking">
          <ThinkingDots />
          <span className="thinking-text">{msg.content}</span>
        </div>
      </div>
    );
  }

  if (msg.role === "user") {
    return (
      <div className="bubble-row bubble-row-user">
        <div className="bubble bubble-user">
          <MarkdownText text={msg.content} />
        </div>
      </div>
    );
  }

  return (
    <div className="bubble-row bubble-row-assistant">
      <div className="agent-avatar">⚡</div>
      <div className="bubble bubble-assistant">
        <MarkdownText text={msg.content} />
      </div>
    </div>
  );
}

function ThinkingDots() {
  return (
    <span className="thinking-dots">
      <span />
      <span />
      <span />
    </span>
  );
}

function MarkdownText({ text }: { text: string }) {
  const lines = text.split("\n");
  return (
    <span>
      {lines.map((line, i) => {
        // Bold **text**
        const parts = line.split(/(\*\*[^*]+\*\*)/g).map((part, j) => {
          if (part.startsWith("**") && part.endsWith("**")) {
            return <strong key={j}>{part.slice(2, -2)}</strong>;
          }
          // Inline code `text`
          return <span key={j}>{part}</span>;
        });
        return (
          <span key={i}>
            {parts}
            {i < lines.length - 1 && <br />}
          </span>
        );
      })}
    </span>
  );
}

function ExecutionLog({ session }: { session: DeploymentSession }) {
  const [open, setOpen] = useState(false);
  const severityColor: Record<string, string> = {
    success: "#22c55e",
    error: "#ef4444",
    warning: "#f59e0b",
    info: "#60a5fa",
  };

  return (
    <div className="exec-log">
      <button className="exec-log-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? "▼" : "▶"} Execution Log ({session.events.length} events)
      </button>
      {open && (
        <div className="exec-log-body">
          {session.events.map((ev, i) => {
            const repoUrl =
              ev.details && typeof ev.details.repository_url === "string"
                ? ev.details.repository_url
                : null;
            return (
              <div key={i} className="log-line">
                <span className="log-ts">{new Date(ev.timestamp).toLocaleTimeString()}</span>
                <span className="log-agent">[{ev.agent}]</span>
                <span style={{ color: severityColor[ev.severity] ?? "#fff" }}>
                  {ev.message}
                </span>
                {repoUrl ? (
                  <a
                    className="log-link"
                    href={repoUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    View Repo ↗
                  </a>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
