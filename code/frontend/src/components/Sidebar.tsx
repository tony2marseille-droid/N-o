import { useCallback } from "react";
import type { Conversation, SystemInfo } from "../lib/api";

interface Props {
  view: string;
  onView: (v: "accueil" | "chat" | "models" | "documents" | "collab" | "finetune" | "settings") => void;
  conversations: Conversation[];
  selectedConv: number | null;
  onSelectConv: (id: number) => void;
  onDeleteConv: (id: number) => void;
  onNewChat: () => void;
  systemInfo: SystemInfo | null;
}

export default function Sidebar({
  view,
  onView,
  conversations,
  selectedConv,
  onSelectConv,
  onDeleteConv,
  onNewChat,
  systemInfo,
}: Props) {
  const ollamaOk = systemInfo?.ollama.available ?? false;

  const fmtUptime = useCallback((s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${String(m).padStart(2, "0")}m`;
  }, []);

  const gpu = systemInfo?.gpus[0];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-dot" />
        <h1>LocalAI</h1>
      </div>

      <div className="nav-row">
        <button
          className={`nav-btn ${view === "accueil" ? "active" : ""}`}
          onClick={() => onView("accueil")}
        >
          Accueil
        </button>
        <button
          className={`nav-btn ${view === "chat" ? "active" : ""}`}
          onClick={() => onView("chat")}
        >
          Chat
        </button>
      </div>
      <div className="nav-row">
        <button
          className={`nav-btn ${view === "models" ? "active" : ""}`}
          onClick={() => onView("models")}
        >
          Modèles
        </button>
        <button
          className={`nav-btn ${view === "documents" ? "active" : ""}`}
          onClick={() => onView("documents")}
        >
          Documents
        </button>
      </div>
      <div className="nav-row">
        <button
          className={`nav-btn ${view === "settings" ? "active" : ""}`}
          onClick={() => onView("settings")}
        >
          Réglages
        </button>
        <button
          className={`nav-btn ${view === "collab" ? "active" : ""}`}
          onClick={() => onView("collab")}
        >
          Ateliers
        </button>
      </div>
      <div className="nav-row">
        <button
          className={`nav-btn ${view === "finetune" ? "active" : ""}`}
          onClick={() => onView("finetune")}
        >
          Fine-tune
        </button>
      </div>

      <button className="primary" onClick={onNewChat}>
        + Nouvelle conversation
      </button>

      <div className="sidebar-section-title">Historique</div>
      <div className="conv-list">
        {conversations.length === 0 && (
          <div className="conv-item" style={{ cursor: "default", opacity: 0.5 }}>
            Aucune conversation
          </div>
        )}
        {conversations.map((c) => (
          <div
            key={c.id}
            className={`conv-item ${c.id === selectedConv ? "selected" : ""}`}
            onClick={() => onSelectConv(c.id)}
            title={c.title}
          >
            {c.title}
            <button
              className="conv-del"
              title="Supprimer"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteConv(c.id);
              }}
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      <div className="side-status">
        <div className="status-line">
          <span>Ollama</span>
          <span className={ollamaOk ? "status-ok" : "status-bad"}>
            {ollamaOk ? `en ligne v${systemInfo?.ollama.version ?? "?"}` : "hors ligne"}
          </span>
        </div>
        {gpu && (
          <>
            <div className="status-line">
              <span>GPU</span>
              <span>{gpu.name.replace("NVIDIA GeForce ", "")}</span>
            </div>
            <div className="status-line">
              <span>VRAM</span>
              <span>
                {(gpu.vram_used_mb / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(0)} Go
              </span>
            </div>
          </>
        )}
        <div className="status-line">
          <span>RAM</span>
          <span>
            {systemInfo ? `${systemInfo.ram.used_gb} / ${systemInfo.ram.total_gb} Go` : "—"}
          </span>
        </div>
        <div className="status-line">
          <span>Uptime PC</span>
          <span>{systemInfo ? fmtUptime(systemInfo.uptime_seconds) : "—"}</span>
        </div>
      </div>
    </aside>
  );
}
