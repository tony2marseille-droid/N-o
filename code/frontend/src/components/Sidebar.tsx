import { useCallback } from "react";
import type { Conversation, SystemInfo } from "../lib/api";
import { type Lang, t } from "../lib/i18n";

interface Props {
  view: string;
  onView: (v: "accueil" | "chat" | "models" | "documents" | "collab" | "finetune" | "settings") => void;
  conversations: Conversation[];
  selectedConv: number | null;
  onSelectConv: (id: number) => void;
  onDeleteConv: (id: number) => void;
  onNewChat: () => void;
  systemInfo: SystemInfo | null;
  lang: Lang;
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
  lang,
}: Props) {
  const ollamaOk = systemInfo?.ollama.available ?? false;
  const texte = useCallback((key: string) => t(lang, key), [lang]);

  const fmtUptime = useCallback((s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${h}h ${String(m).padStart(2, "0")}m`;
  }, []);

  const gpu = systemInfo?.gpus?.[0];

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-dot" />
        <h1>NÉO</h1>
      </div>

      <div className="nav-row">
        <button
          className={`nav-btn ${view === "accueil" ? "active" : ""}`}
          onClick={() => onView("accueil")}
        >
          {texte("nav.home")}
        </button>
        <button
          className={`nav-btn ${view === "chat" ? "active" : ""}`}
          onClick={() => onView("chat")}
        >
          {texte("nav.chat")}
        </button>
      </div>
      <div className="nav-row">
        <button
          className={`nav-btn ${view === "models" ? "active" : ""}`}
          onClick={() => onView("models")}
        >
          {texte("nav.models")}
        </button>
        <button
          className={`nav-btn ${view === "documents" ? "active" : ""}`}
          onClick={() => onView("documents")}
        >
          {texte("nav.documents")}
        </button>
      </div>
      <div className="nav-row">
        <button
          className={`nav-btn ${view === "settings" ? "active" : ""}`}
          onClick={() => onView("settings")}
        >
          {texte("nav.settings")}
        </button>
        <button
          className={`nav-btn ${view === "collab" ? "active" : ""}`}
          onClick={() => onView("collab")}
        >
          {texte("nav.workshop")}
        </button>
      </div>
      <div className="nav-row">
        <button
          className={`nav-btn ${view === "finetune" ? "active" : ""}`}
          onClick={() => onView("finetune")}
        >
          {texte("nav.finetune")}
        </button>
      </div>

      <button className="primary" onClick={onNewChat}>
        {texte("nav.newChat")}
      </button>

      <div className="sidebar-section-title">{texte("nav.history")}</div>
      <div className="conv-list">
        {conversations.length === 0 && (
          <div className="conv-item" style={{ cursor: "default", opacity: 0.5 }}>
            {texte("nav.emptyHistory")}
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
          <span>{texte("status.ollama")}</span>
          <span className={ollamaOk ? "status-ok" : "status-bad"}>
            {ollamaOk
              ? `${texte("status.online")} v${systemInfo?.ollama.version ?? "?"}`
              : texte("status.offline")}
          </span>
        </div>
        {gpu && (
          <>
            <div className="status-line">
              <span>{texte("status.gpu")}</span>
              <span>{gpu.name.replace("NVIDIA GeForce ", "")}</span>
            </div>
            <div className="status-line">
              <span>{texte("status.vram")}</span>
              <span>
                {(gpu.vram_used_mb / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(0)} Go
              </span>
            </div>
          </>
        )}
        <div className="status-line">
          <span>{texte("status.ram")}</span>
          <span>
            {systemInfo ? `${systemInfo.ram.used_gb} / ${systemInfo.ram.total_gb} Go` : "—"}
          </span>
        </div>
        <div className="status-line">
          <span>{texte("status.uptime")}</span>
          <span>{systemInfo ? fmtUptime(systemInfo.uptime_seconds) : "—"}</span>
        </div>
      </div>
    </aside>
  );
}
