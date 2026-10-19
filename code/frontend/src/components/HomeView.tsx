import { useEffect, useState } from "react";
import MatrixRain from "./MatrixRain";
import type { ModelEntry, SystemInfo } from "../lib/api";
import { getJSON } from "../lib/api";
import { type Lang, t } from "../lib/i18n";

type Vue =
  | "accueil"
  | "chat"
  | "models"
  | "documents"
  | "collab"
  | "finetune"
  | "settings";

interface Props {
  onView: (v: Vue) => void;
  onNewChat: (prefill?: string) => void;
  systemInfo: SystemInfo | null;
  models: ModelEntry[];
  lang: Lang;
}

interface ReglesResp {
  regles: { regle: string; created_at: string }[];
}
interface FeedbackResp {
  total: number;
  moyenne: number;
  auto_evals?: { n: number; moyenne: number };
}
interface VoiceStatus {
  stt_available: boolean;
  tts_available: boolean;
}
interface AgentDef {
  id: string;
  name: string;
  description: string;
}

interface Dossier {
  vue: Vue | "nouveau-chat" | "image";
  glyphe: string;
  titre: string;
  desc: string;
  prefill?: string;
}

const DOSSIERS: Dossier[] = [
  {
    vue: "chat",
    glyphe: "❯_",
    titre: "Chat",
    desc: "Dialogue avec les 7 agents (outils, RAG, vision)",
  },
  {
    vue: "image",
    glyphe: "▣",
    titre: "Image 4K",
    desc: "FLUX + Kontext : photorealisme fidele au sujet",
    prefill:
      "Genere une image 4K photorealiste de ",
  },
  {
    vue: "documents",
    glyphe: "☰",
    titre: "Documents",
    desc: "RAG : importe, indexe, interroge tes fichiers",
  },
  {
    vue: "models",
    glyphe: "◈",
    titre: "Modeles",
    desc: "Catalogue Ollama : charge, decharge, installe",
  },
  {
    vue: "finetune",
    glyphe: "↻",
    titre: "Auto-amelioration",
    desc: "Cycles corpus > LoRA > bench > promote",
  },
  {
    vue: "collab",
    glyphe: "✦",
    titre: "Ateliers",
    desc: "Workflows multi-agents : brainstorm, revue, debat",
  },
  {
    vue: "nouveau-chat",
    glyphe: "◉",
    titre: "Vocal",
    desc: "Néo : client vocal LAN (wake-word + TTS)",
  },
  {
    vue: "settings",
    glyphe: "⚙",
    titre: "Reglages",
    desc: "Systeme, securite, memoires, ponts Python",
  },
];

function barre(pct: number, warn = 75, crit = 90): string {
  if (pct >= crit) return "crit";
  if (pct >= warn) return "warn";
  return "";
}

export default function HomeView({ onView, onNewChat, systemInfo, models, lang }: Props) {
  const texte = (key: string) => t(lang, key);
  const [regles, setRegles] = useState<ReglesResp | null>(null);
  const [feedback, setFeedback] = useState<FeedbackResp | null>(null);
  const [vocal, setVocal] = useState<VoiceStatus | null>(null);
  const [agents, setAgents] = useState<AgentDef[]>([]);

  useEffect(() => {
    getJSON<ReglesResp>("/api/selfimprove/rules").then(setRegles).catch(() => undefined);
    getJSON<FeedbackResp>("/api/feedback/stats").then(setFeedback).catch(() => undefined);
    getJSON<VoiceStatus>("/api/voice/status").then(setVocal).catch(() => undefined);
    getJSON<{ agents: AgentDef[] }>("/api/agents").then((a) => setAgents(a.agents)).catch(() => undefined);
  }, []);

  const gpu = systemInfo?.gpus[0];
  const installees = models.filter((m) => m.installed && !m.tag.startsWith("nomic")).length;
  const reglesActives = regles?.regles?.length ?? 0;
  const ok = (b: boolean | undefined) => (b ? "hud-dot ok" : "hud-dot bad");

  const ouvre = (d: Dossier) => {
    if (d.vue === "nouveau-chat") {
      // Vocal : ouvre le chat avec une demande prete (le client LAN fait le STT/TTS).
      onNewChat();
      return;
    }
    if (d.vue === "image" && d.prefill) {
      onNewChat(`${d.prefill}`);
      return;
    }
    onView(d.vue as Vue);
  };

  return (
    <section className="home">
      <MatrixRain />
      <div className="home-inner">
        {/* ---------- Hero ---------- */}
        <header className="home-hero">
          <div className="home-brand">
            <span className="home-core" />
            <h1 className="home-title">
              NÉO<span className="cursor-blink" />
            </h1>
          </div>
          <p className="home-tagline">
            Intelligence locale. Aucun nuage. {systemInfo?.app.version ? `v${systemInfo.app.version} — ` : ""}
            {agents.length > 0 ? `${agents.length} ${texte("home.agents")}.` : texte("home.agents")}
          </p>
          <div className="home-health">
            <span className={ok(systemInfo?.ollama.available ?? false)}>OLLAMA {systemInfo?.ollama.version ?? ""}</span>
            <span className={ok(true)}>SERVEUR :8320</span>
            <span className={ok(vocal?.stt_available)}>STT</span>
            <span className={ok(vocal?.tts_available)}>TTS</span>
            <span className="hud-mono">PONT OPENJARVIS ✓</span>
          </div>
        </header>

        {/* ---------- Dossiers tournants (anneau 3D) ---------- */}
        <div className="ring-scene">
          <div className="ring-center">
            <div className="reticle" />
            <div className="reticle-2" />
            <div className="core-label">
              <div className="core-dot" />
              <span>NOYAU</span>
            </div>
          </div>
          <div className="ring">
            {DOSSIERS.map((d, i) => (
              <button
                key={d.titre}
                type="button"
                className="folder-card"
                style={{ "--i": i } as React.CSSProperties}
                onClick={() => ouvre(d)}
                title={d.desc}
              >
                <span className="fc-icon">{d.glyphe}</span>
                <span className="fc-title">{d.titre}</span>
                <span className="fc-desc">{d.desc}</span>
                <span className="fc-open">▸ ouvrir</span>
              </button>
            ))}
          </div>
        </div>

        {/* ---------- HUD : etat du systeme ---------- */}
        <div className="hud-grid">
          <div className="hud-panel">
            <div className="hud-head">
              <span className="hud-title">{texte("home.system")}</span>
              <span className="hud-label hud-mono">
                uptime {systemInfo
                  ? `${Math.floor(systemInfo.uptime_seconds / 3600)}h${String(
                      Math.floor((systemInfo.uptime_seconds % 3600) / 60)
                    ).padStart(2, "0")}`
                  : "—"}
              </span>
            </div>
            <div className="hud-row">
              <span>CPU {systemInfo ? `${systemInfo.cpu.percent} %` : "—"}</span>
              <span className="hud-label">{systemInfo ? `${systemInfo.cpu.cores_logical} cœurs` : ""}</span>
            </div>
            <div className="hud-bar">
              <div className={`hud-bar-fill ${barre(systemInfo?.cpu.percent ?? 0)}`} style={{ width: `${systemInfo?.cpu.percent ?? 0}%` }} />
            </div>
            <div className="hud-row">
              <span>RAM {systemInfo ? `${systemInfo.ram.used_gb} / ${systemInfo.ram.total_gb} Go` : "—"}</span>
              <span className="hud-label">{systemInfo ? `${systemInfo.ram.percent} %` : ""}</span>
            </div>
            <div className="hud-bar">
              <div className={`hud-bar-fill ${barre(systemInfo?.ram.percent ?? 0)}`} style={{ width: `${systemInfo?.ram.percent ?? 0}%` }} />
            </div>
            {gpu && (
              <>
                <div className="hud-row">
                  <span>{gpu.name.replace("NVIDIA GeForce ", "")} {gpu.temperature_c ? `${gpu.temperature_c}°C` : ""}</span>
                  <span className="hud-label">{gpu.utilization_percent} % util.</span>
                </div>
                <div className="hud-row">
                  <span>VRAM {(gpu.vram_used_mb / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(0)} Go</span>
                  <span className="hud-label">{Math.round((gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100)} %</span>
                </div>
                <div className="hud-bar">
                  <div
                    className={`hud-bar-fill ${barre((gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100, 85, 95)}`}
                    style={{ width: `${(gpu.vram_used_mb / Math.max(1, gpu.vram_total_mb)) * 100}%` }}
                  />
                </div>
              </>
            )}
          </div>

          <div className="hud-panel">
            <div className="hud-head">
              <span className="hud-title">{texte("home.auto")}</span>
              <span className="hud-label">cycle hebdo</span>
            </div>
            <div className="hud-kv">
              <span>Regles actives</span>
              <span className="hud-strong">{reglesActives}</span>
            </div>
            <div className="hud-kv">
              <span>Retours utilisateur</span>
              <span className="hud-strong">{feedback?.total ?? 0}</span>
            </div>
            <div className="hud-kv">
              <span>Moyenne / 5</span>
              <span className="hud-strong">{feedback ? feedback.moyenne.toFixed(1) : "—"}</span>
            </div>
            <div className="hud-kv">
              <span>Auto-evaluations</span>
              <span className="hud-strong">{feedback?.auto_evals?.n ?? 0}</span>
            </div>
            {reglesActives > 0 && (
              <div className="hud-regle">« {regles?.regles[0]?.regle.slice(0, 110)}… »</div>
            )}
          </div>

          <div className="hud-panel">
            <div className="hud-head">
              <span className="hud-title">{texte("home.models")}</span>
              <span className="hud-label">fusion LocalAI × OpenJarvis</span>
            </div>
            <div className="hud-kv">
              <span>Modeles installes</span>
              <span className="hud-strong">{installees}</span>
            </div>
            <div className="hud-kv">
              <span>Agents</span>
              <span className="hud-strong">{agents.length || 7}</span>
            </div>
            <div className="hud-kv">
              <span>Dispatcher OpenJarvis</span>
              <span className="hud-strong hud-mono">qwen3:8b</span>
            </div>
            <div className="hud-kv">
              <span>Cerveau fine-tune</span>
              <span className="hud-strong hud-mono">localai-prog-latest</span>
            </div>
            <div className="hud-kv">
              <span>Pont /api/outil (jobs)</span>
              <span className="hud-strong">actif</span>
            </div>
          </div>
        </div>

        {/* ---------- Actions rapides ---------- */}
        <div className="home-actions">
          <button className="primary" onClick={() => onNewChat()}>
            ❯_ {texte("home.newChat")}
          </button>
          <button onClick={() => onView("finetune")}>{texte("home.cycle")}</button>
        </div>
      </div>
    </section>
  );
}
