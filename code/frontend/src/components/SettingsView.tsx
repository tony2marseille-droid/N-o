import { useCallback, useEffect, useState } from "react";
import type { MemoryItem, SystemInfo, Voice } from "../lib/api";
import { getJSON, sendJSON } from "../lib/api";
import { LANGS, type Lang, t } from "../lib/i18n";

interface Props {
  systemInfo: SystemInfo | null;
  refreshSystem: () => void;
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
  lang: Lang;
  onLangChange: (lang: Lang) => void;
}

interface FeedbackStats {
  total: number;
  moyenne: number;
  auto_evals?: { n: number; moyenne: number };
}
interface Regle {
  id: number;
  regle: string;
  source: string;
  created_at: string;
}

function Meter({ value }: { value: number }) {
  const cls = value >= 90 ? "crit" : value >= 70 ? "warn" : "";
  return (
    <div className="meter">
      <div className={`meter-fill ${cls}`} style={{ width: `${Math.min(100, value)}%` }} />
    </div>
  );
}

export default function SettingsView({
  systemInfo,
  refreshSystem,
  toast,
  lang,
  onLangChange,
}: Props) {
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [rate, setRate] = useState(185);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [newMemory, setNewMemory] = useState("");
  const [feedback, setFeedback] = useState<FeedbackStats | null>(null);
  const [regles, setRegles] = useState<Regle[]>([]);
  const [coaching, setCoaching] = useState(false);

  const texte = useCallback((key: string) => t(lang, key), [lang]);

  const refreshMemories = useCallback(() => {
    getJSON<{ memories: MemoryItem[] }>("/api/memory")
      .then((d) => setMemories(d.memories ?? []))
      .catch(() => undefined);
  }, []);

  const refreshFeedback = useCallback(() => {
    getJSON<FeedbackStats>("/api/feedback/stats")
      .then(setFeedback)
      .catch(() => undefined);
    getJSON<{ regles: Regle[] }>("/api/selfimprove/rules")
      .then((d) => setRegles(d.regles ?? []))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    getJSON<{ settings: Record<string, string> }>("/api/settings")
      .then((d) => {
        const s = d.settings ?? {};
        setSettings(s);
        setVoiceId(s.tts_voice ?? "");
        setRate(Number(s.tts_rate ?? 185));
      })
      .catch(() => undefined);
    getJSON<{ voices: Voice[] }>("/api/tts/voices")
      .then((d) => setVoices(d.voices ?? []))
      .catch(() => undefined);
    refreshMemories();
    refreshFeedback();
  }, [refreshMemories, refreshFeedback]);

  const save = useCallback(
    async (key: string, value: string) => {
      try {
        await sendJSON("/api/settings", "PUT", { key, value });
        setSettings((s) => ({ ...s, [key]: value }));
        toast(texte("settings.saved"), "success");
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [toast, texte]
  );

  const changerLangue = useCallback(
    async (code: Lang) => {
      await save("language", code);
      onLangChange(code);
    },
    [save, onLangChange]
  );

  const testerVoix = useCallback(async () => {
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: "Bonjour, je suis Néo, votre assistant local.",
          voice: voiceId || undefined,
          rate,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const audio = new Audio(URL.createObjectURL(blob));
      void audio.play();
    } catch (err) {
      toast(`TTS indisponible : ${err}`, "error");
    }
  }, [voiceId, rate, toast]);

  const ajouterMemoire = useCallback(async () => {
    const contenu = newMemory.trim();
    if (!contenu) return;
    try {
      await sendJSON("/api/memory", "POST", { content: contenu });
      setNewMemory("");
      refreshMemories();
      toast("Mémoire ajoutée", "success");
    } catch (err) {
      toast(`Erreur : ${err}`, "error");
    }
  }, [newMemory, refreshMemories, toast]);

  const supprimerMemoire = useCallback(
    async (id: number) => {
      try {
        await sendJSON(`/api/memory/${id}`, "DELETE");
        setMemories((m) => m.filter((x) => x.id !== id));
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [toast]
  );

  const lancerCoach = useCallback(async () => {
    setCoaching(true);
    try {
      const r = await sendJSON<{ new_rules: number; analysed_signals: number }>(
        "/api/selfimprove/coach",
        "POST"
      );
      toast(
        `Coach : ${r.new_rules} nouvelle(s) règle(s), ${r.analysed_signals} signaux analysés`,
        "success"
      );
      refreshFeedback();
    } catch (err) {
      toast(`Coach indisponible : ${err}`, "error");
    } finally {
      setCoaching(false);
    }
  }, [refreshFeedback, toast]);

  const gpu = systemInfo?.gpus?.[0];

  return (
    <>
      <div className="view-header">
        <h2>{texte("settings.title")}</h2>
        <span style={{ flex: 1 }} />
        <button onClick={refreshSystem}>{texte("settings.refresh")}</button>
      </div>
      <div className="content-scroll">
        <div className="settings-grid">
          {/* Langue */}
          <div className="panel">
            <h3>{texte("settings.language")}</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              {texte("settings.languageHint")}
            </p>
            <select
              value={lang}
              onChange={(e) => void changerLangue(e.target.value as Lang)}
              style={{ width: "100%" }}
            >
              {LANGS.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.flag} {l.label}
                </option>
              ))}
            </select>
          </div>

          {/* Système */}
          <div className="panel">
            <h3>{texte("settings.system")}</h3>
            {systemInfo ? (
              <>
                <div className="kv">
                  <span className="k">CPU ({systemInfo.cpu.cores_logical} threads)</span>
                  <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    {systemInfo.cpu.percent}% <Meter value={systemInfo.cpu.percent} />
                  </span>
                </div>
                <div className="kv">
                  <span className="k">RAM</span>
                  <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    {systemInfo.ram.used_gb} / {systemInfo.ram.total_gb} Go{" "}
                    <Meter value={systemInfo.ram.percent} />
                  </span>
                </div>
                {(systemInfo.gpus ?? []).map((g) => (
                  <div className="kv" key={g.name}>
                    <span className="k">{g.name}</span>
                    <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      {(g.vram_used_mb / 1024).toFixed(1)} / {(g.vram_total_mb / 1024).toFixed(0)} Go
                      · {g.temperature_c}°C
                      <Meter
                        value={g.vram_total_mb > 0 ? (g.vram_used_mb / g.vram_total_mb) * 100 : 0}
                      />
                    </span>
                  </div>
                ))}
                {gpu && (
                  <div className="kv">
                    <span className="k">GPU</span>
                    <span>{gpu.utilization_percent}%</span>
                  </div>
                )}
                {(systemInfo.disks ?? []).map((d) => (
                  <div className="kv" key={d.mount}>
                    <span className="k">Disque {d.mount}</span>
                    <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      {d.free_gb} Go libres <Meter value={d.used_percent} />
                    </span>
                  </div>
                ))}
              </>
            ) : (
              <div style={{ color: "var(--text-dim)" }}>{texte("common.loading")}</div>
            )}
          </div>

          {/* Assistant : outils + apprentissage */}
          <div className="panel">
            <h3>Assistant</h3>
            <div className="settings-row">
              <label>{texte("settings.tools")}</label>
              <select
                value={settings.tool_level ?? "USER"}
                onChange={(e) => void save("tool_level", e.target.value)}
              >
                <option value="SAFE">SAFE — calculs/date</option>
                <option value="USER">USER — lecture + web</option>
                <option value="POWER">POWER — écriture + terminal</option>
              </select>
            </div>
            <label className="toggle" style={{ marginTop: 8 }}>
              <input
                type="checkbox"
                checked={(settings.auto_learn ?? "1") === "1"}
                onChange={(e) => void save("auto_learn", e.target.checked ? "1" : "0")}
              />
              {texte("settings.autoLearn")}
            </label>
          </div>

          {/* Voix */}
          <div className="panel">
            <h3>{texte("settings.voice")}</h3>
            <div className="settings-row">
              <label>Voix</label>
              <select
                value={voiceId}
                onChange={(e) => {
                  setVoiceId(e.target.value);
                  void save("tts_voice", e.target.value);
                }}
              >
                <option value="">Auto (français)</option>
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="settings-row">
              <label>Débit</label>
              <input
                type="range"
                min={120}
                max={260}
                value={rate}
                onChange={(e) => setRate(Number(e.target.value))}
                onMouseUp={() => void save("tts_rate", String(rate))}
              />
              <span className="chip">{rate}</span>
            </div>
            <button onClick={() => void testerVoix()}>{texte("settings.voiceTest")}</button>
          </div>

          {/* Mémoire */}
          <div className="panel">
            <h3>{texte("settings.memoryTitle")}</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              {texte("settings.memoryHint")}
            </p>
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <input
                placeholder={texte("settings.memoryPlaceholder")}
                value={newMemory}
                onChange={(e) => setNewMemory(e.target.value)}
                style={{ flex: 1 }}
                onKeyDown={(e) => e.key === "Enter" && void ajouterMemoire()}
              />
              <button className="primary" onClick={() => void ajouterMemoire()}>
                {texte("settings.remember")}
              </button>
            </div>
            <div className="memory-list">
              {memories.length === 0 && (
                <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>—</div>
              )}
              {memories.map((m) => (
                <div className="memory-item" key={m.id}>
                  <span>{m.content}</span>
                  <button onClick={() => void supprimerMemoire(m.id)}>✕</button>
                </div>
              ))}
            </div>
          </div>

          {/* Auto-amélioration */}
          <div className="panel">
            <h3>{texte("settings.coach")}</h3>
            <div className="kv">
              <span className="k">{texte("settings.feedback")}</span>
              <span>{feedback?.total ?? 0}</span>
            </div>
            <div className="kv">
              <span className="k">{texte("settings.average")}</span>
              <span>{feedback ? feedback.moyenne.toFixed(1) : "—"}</span>
            </div>
            <div className="kv">
              <span className="k">{texte("settings.autoEvals")}</span>
              <span>{feedback?.auto_evals?.n ?? 0}</span>
            </div>
            <div className="kv">
              <span className="k">{texte("settings.rules")}</span>
              <span>{regles.length}</span>
            </div>
            {regles.length > 0 && (
              <div className="tool-trace">
                {regles.slice(0, 3).map((r) => (
                  <div className="tool-line" key={r.id}>
                    • {r.regle.slice(0, 120)}
                  </div>
                ))}
              </div>
            )}
            <button
              className="primary"
              style={{ marginTop: 10 }}
              disabled={coaching}
              onClick={() => void lancerCoach()}
            >
              {coaching ? texte("common.loading") : texte("settings.coachRun")}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
