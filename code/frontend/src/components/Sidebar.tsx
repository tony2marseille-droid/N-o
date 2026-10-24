import { useCallback } from "react";
import type { SystemInfo } from "../lib/api";
import { type Lang, t } from "../lib/i18n";
import TokenPanel from "./TokenPanel";

interface Props {
  view: string;
  onView: (v: "accueil" | "chat" | "models" | "documents" | "collab" | "finetune" | "settings") => void;
  onNewChat: () => void;
  onToggleHistory: () => void;
  systemInfo: SystemInfo | null;
  lang: Lang;
}

export default function Sidebar({
  view,
  onView,
  onNewChat,
  onToggleHistory,
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

      {/* L'historique (conversations stockées) vit dans un panneau
          lateral masquable ; l'encart tokens prend sa place ici. */}
      <button className="history-toggle" onClick={onToggleHistory} title="Historique des conversations">
        {texte("nav.history")}
      </button>

      <TokenPanel />

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
