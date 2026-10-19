import { useCallback, useEffect, useState } from "react";
import type { MemoryItem, SystemInfo, Voice } from "../lib/api";
import { getJSON, sendJSON, streamSSE } from "../lib/api";

interface Props {
  systemInfo: SystemInfo | null;
  refreshSystem: () => void;
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
}

const TOOL_LEVELS = [
  { id: "SAFE", desc: "Calculatrice et date uniquement" },
  { id: "USER", desc: "+ lecture fichiers et dossiers" },
  { id: "POWER", desc: "+ écriture fichiers et terminal" },
];

interface LlamaCppStatus {
  binary_installed: boolean;
  bin_path: string;
  server_running: boolean;
  gguf_dir: string;
  models: string[];
  port: number;
  active_backend: string;
}

interface TailscaleStatus {
  installed: boolean;
  state: string;
  ip4: string;
  serve_published: boolean;
}

function Meter({ value }: { value: number }) {
  const cls = value >= 90 ? "crit" : value >= 70 ? "warn" : "";
  return (
    <div className="meter">
      <div className={`meter-fill ${cls}`} style={{ width: `${Math.min(100, value)}%` }} />
    </div>
  );
}

export default function SettingsView({ systemInfo, refreshSystem, toast }: Props) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [rate, setRate] = useState(185);
  const [updateInfo, setUpdateInfo] = useState<{
    update_available: boolean;
    latest_version: string;
    url: string;
    configured: boolean;
  } | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [newMemory, setNewMemory] = useState("");
  const [toolLevel, setToolLevel] = useState("USER");
  const [llamaStatus, setLlamaStatus] = useState<LlamaCppStatus | null>(null);
  const [selectedGguf, setSelectedGguf] = useState("");
  const [installingLlama, setInstallingLlama] = useState(false);
  const [tsStatus, setTsStatus] = useState<TailscaleStatus | null>(null);
  const [kStats, setKStats] = useState<{ count: number; reuse_total: number } | null>(null);
  const [distillStats, setDistillStats] = useState<{
    scored_answers: number;
    weak_answers: number;
    good_answers: number;
    learned_entries: number;
    weak_threshold: number;
  } | null>(null);
  const [distilling, setDistilling] = useState(false);
  const [distillResult, setDistillResult] = useState<{ upgraded: number; skipped: number; model_used: string } | null>(null);
  const [loraPreparing, setLoraPreparing] = useState(false);
  const [loraResult, setLoraResult] = useState<{ total_exemples: number; tier: string } | null>(null);
  const [autoLearn, setAutoLearn] = useState(true);
  const [autoTtsPlay, setAutoTtsPlay] = useState(false);
  const [followUp, setFollowUp] = useState(false);
  const [theme, setTheme] = useState("dark");
  const [promptList, setPromptList] = useState<{ id: number; name: string; content: string }[]>([]);
  const [promptName, setPromptName] = useState("");
  const [promptContent, setPromptContent] = useState("");
  const [usage, setUsage] = useState<{
    assistant_messages: number;
    completion_tokens: number;
    conversations: number;
    documents: number;
    by_model: { model: string; n: number; tokens: number }[];
    per_day: { day: string; n: number }[];
  } | null>(null);
  const [interests, setInterests] = useState<{ key: string; label: string }[]>([]);
  const [selectedInterests, setSelectedInterests] = useState<string[]>([]);
  const [securityInfo, setSecurityInfo] = useState<{
    home: string;
    dossiers_autorises: string[];
    regles_version: string;
    stockage: { used_gb: number; quota_gb: number; percent: number };
  } | null>(null);
  const [newFolder, setNewFolder] = useState("");
  const [sdStatus, setSdStatus] = useState<{ binaire: boolean; modele: string; pret: boolean } | null>(null);
  const [sdInstalling, setSdInstalling] = useState(false);
  const [sdPrompt, setSdPrompt] = useState("");
  const [sdGenerating, setSdGenerating] = useState(false);
  const [sdImageUrl, setSdImageUrl] = useState("");
  const [i3dPath, setI3dPath] = useState("");
  const [i3dBusy, setI3dBusy] = useState(false);
  const [i3dResult, setI3dResult] = useState("");
  const [tgStatus, setTgStatus] = useState<{
    configured: boolean;
    enabled: boolean;
    model: string;
    poller_running: boolean;
    last_error: string;
    sessions: number;
  } | null>(null);
  const [tgToken, setTgToken] = useState("");
  const [tgEnabled, setTgEnabled] = useState(true);
  const [tgModel, setTgModel] = useState("gemma3:4b");
  const [lkStatus, setLkStatus] = useState<{
    configured: boolean;
    url: string;
    has_api_key: boolean;
    has_api_secret: boolean;
  } | null>(null);
  const [lkUrl, setLkUrl] = useState("");
  const [lkKey, setLkKey] = useState("");
  const [lkSecret, setLkSecret] = useState("");
  const [styleEnabled, setStyleEnabled] = useState(true);
  const [styleProfile, setStyleProfile] = useState<string>("");

  const refreshLlama = useCallback(() => {
    getJSON<LlamaCppStatus>("/api/llamacpp/status")
      .then((s) => {
        setLlamaStatus(s);
        if (s.models.length > 0 && !s.models.includes(selectedGguf)) setSelectedGguf(s.models[0]);
      })
      .catch(() => undefined);
  }, [selectedGguf]);

  const refreshTailscale = useCallback(() => {
    getJSON<TailscaleStatus>("/api/tailscale")
      .then(setTsStatus)
      .catch(() => undefined);
  }, []);

  const refreshKnowledge = useCallback(() => {
    getJSON<{ count: number; reuse_total: number }>("/api/knowledge/stats")
      .then(setKStats)
      .catch(() => undefined);
  }, []);

  const refreshDistill = useCallback(() => {
    getJSON<{
      scored_answers: number;
      weak_answers: number;
      good_answers: number;
      learned_entries: number;
      weak_threshold: number;
    }>("/api/distill/stats")
      .then(setDistillStats)
      .catch(() => undefined);
  }, []);

  const runDistillUpgrade = useCallback(async () => {
    setDistilling(true);
    setDistillResult(null);
    try {
      const r = await sendJSON<{ upgraded: number; skipped: number; model_used: string }>(
        "/api/distill/upgrade",
        "POST",
        {}
      );
      setDistillResult(r);
      toast(`Distillation : ${r.upgraded} réponse(s) améliorée(s)`);
    } catch {
      toast("Échec de la distillation");
    } finally {
      setDistilling(false);
      refreshDistill();
    }
  }, [toast, refreshDistill]);

  const prepareLora = useCallback(async () => {
    setLoraPreparing(true);
    setLoraResult(null);
    try {
      const r = await sendJSON<{ total_exemples: number; tier: string }>(
        "/api/distill/lora/prepare",
        "POST",
        {}
      );
      setLoraResult(r);
      toast(`Atelier LoRA préparé (${r.total_exemples} exemples)`, "success");
    } catch {
      toast("Échec de préparation de l'atelier LoRA", "error");
    } finally {
      setLoraPreparing(false);
    }
  }, [toast]);

  const refreshPrompts = useCallback(() => {
    getJSON<{ prompts: { id: number; name: string; content: string }[] }>("/api/prompts")
      .then((d) => setPromptList(d.prompts))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshLlama();
    refreshTailscale();
    refreshKnowledge();
    refreshDistill();
    refreshPrompts();
    getJSON<{
      assistant_messages: number;
      completion_tokens: number;
      conversations: number;
      documents: number;
      by_model: { model: string; n: number; tokens: number }[];
      per_day: { day: string; n: number }[];
    }>("/api/stats/usage")
      .then(setUsage)
      .catch(() => undefined);
    getJSON<{ settings: Record<string, string> }>("/api/settings")
      .then((d) => {
        setAutoLearn(d.settings.auto_learn !== "0");
        setAutoTtsPlay(d.settings.tts_autoplay === "1");
        setFollowUp(d.settings.tts_followup === "1");
        if (d.settings.theme) applyTheme(d.settings.theme);
      })
      .catch(() => undefined);
  }, [refreshLlama, refreshTailscale, refreshKnowledge, refreshPrompts]);

  const refreshSecurity = useCallback(() => {
    getJSON<{
      home: string;
      dossiers_autorises: string[];
      regles_version: string;
      stockage: { used_gb: number; quota_gb: number; percent: number };
    }>("/api/security")
      .then(setSecurityInfo)
      .catch(() => undefined);
  }, []);

  const toggleInterest = useCallback(
    async (key: string) => {
      const next = selectedInterests.includes(key)
        ? selectedInterests.filter((k) => k !== key)
        : [...selectedInterests, key];
      setSelectedInterests(next);
      try {
        await sendJSON("/api/settings/interests", "PUT", { themes: next });
        toast("Domaines d'intérêt mis à jour", "success");
      } catch {
        toast("Échec de l'enregistrement des intérêts", "error");
      }
    },
    [selectedInterests, toast]
  );

  const grantFolder = useCallback(async () => {
    if (!newFolder.trim()) return;
    try {
      const r = await sendJSON<{ dossiers_autorises: string[] }>("/api/security/folders", "POST", {
        path: newFolder.trim(),
      });
      setNewFolder("");
      setSecurityInfo((s) => (s ? { ...s, dossiers_autorises: r.dossiers_autorises } : s));
      toast("Dossier accordé à l'assistant", "success");
    } catch (err) {
      toast(`Refus : ${err}`, "error");
    }
  }, [newFolder, toast]);

  const revokeFolder = useCallback(
    async (path: string) => {
      try {
        const r = await sendJSON<{ dossiers_autorises: string[] }>("/api/security/folders", "DELETE", {
          path,
        });
        setSecurityInfo((s) => (s ? { ...s, dossiers_autorises: r.dossiers_autorises } : s));
        toast("Dossier révoqué", "success");
      } catch {
        toast("Échec de la révocation", "error");
      }
    },
    [toast]
  );

  const refreshSd = useCallback(() => {
    getJSON<{ binaire: boolean; modele: string; pret: boolean }>("/api/sd/status")
      .then(setSdStatus)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    getJSON<{ catalogue: { key: string; label: string }[]; selection: string[] }>(
      "/api/settings/interests"
    )
      .then((d) => {
        setInterests(d.catalogue);
        setSelectedInterests(d.selection);
      })
      .catch(() => undefined);
    refreshSecurity();
    refreshSd();
  }, [refreshSecurity, refreshSd]);

  const refreshTelegram = useCallback(() => {
    getJSON<{
      configured: boolean;
      enabled: boolean;
      model: string;
      poller_running: boolean;
      last_error: string;
      sessions: number;
    }>("/api/telegram/status")
      .then((s) => {
        setTgStatus(s);
        if (s.configured) setTgEnabled(s.enabled);
        if (s.model) setTgModel(s.model);
      })
      .catch(() => undefined);
  }, []);

  const refreshLivekit = useCallback(() => {
    getJSON<{
      configured: boolean;
      url: string;
      has_api_key: boolean;
      has_api_secret: boolean;
    }>("/api/livekit/status")
      .then((s) => {
        setLkStatus(s);
        if (s.url) setLkUrl(s.url);
      })
      .catch(() => undefined);
  }, []);

  const refreshStyle = useCallback(() => {
    getJSON<{ enabled: boolean; profile: Record<string, string> }>("/api/style")
      .then((s) => {
        setStyleEnabled(s.enabled);
        setStyleProfile(JSON.stringify(s.profile, null, 2));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshTelegram();
    refreshLivekit();
    refreshStyle();
  }, [refreshTelegram, refreshLivekit, refreshStyle]);

  const installSd = useCallback(async () => {
    setSdInstalling(true);
    try {
      const r = await sendJSON<{ ok: boolean; error?: string; modele?: string }>(
        "/api/sd/install",
        "POST",
        {}
      );
      if (r.ok) {
        toast(r.modele ? "Générateur d'images installé" : `Binaire installé — déposez un modèle dans D:\\tools-sd\\models`, "success");
      } else {
        toast(`Échec : ${r.error}`, "error");
      }
    } catch (err) {
      toast(`Installation échouée : ${err}`, "error");
    } finally {
      setSdInstalling(false);
      refreshSd();
    }
  }, [toast, refreshSd]);

  const generateImage = useCallback(async () => {
    if (!sdPrompt.trim()) return;
    setSdGenerating(true);
    setSdImageUrl("");
    try {
      const r = await sendJSON<{ ok: boolean; path: string; message: string }>("/api/sd/generate", "POST", {
        prompt: sdPrompt.trim(),
      });
      if (r.ok) {
        setSdImageUrl(`/api/sd/file?path=${encodeURIComponent(r.path)}&t=${Date.now()}`);
        toast("Image générée", "success");
      } else {
        toast(`Échec : ${r.message.slice(0, 120)}`, "error");
      }
    } catch (err) {
      toast(`Erreur : ${err}`, "error");
    } finally {
      setSdGenerating(false);
    }
  }, [sdPrompt, toast]);

  const imageTo3d = useCallback(async () => {
    if (!i3dPath.trim()) return;
    setI3dBusy(true);
    setI3dResult("");
    try {
      const r = await sendJSON<{ ok: boolean; message: string }>("/api/sd/image-to-3d", "POST", {
        image_path: i3dPath.trim(),
      });
      setI3dResult(r.message);
      toast(r.ok ? "Mesh généré" : "Conversion échouée", r.ok ? "success" : "error");
    } catch (err) {
      setI3dResult(String(err));
      toast("Erreur conversion 3D", "error");
    } finally {
      setI3dBusy(false);
    }
  }, [i3dPath, toast]);

  const installLlamaServer = useCallback(async () => {
    setInstallingLlama(true);
    try {
      await streamSSE("/api/llamacpp/install?variant=cuda", {}, (ev) => {
        if (ev.type === "progress" && ev.step === "download") {
          toast(`llama.cpp : ${ev.percent}%`, "info");
        } else if (ev.type === "done") {
          toast("llama-server installé", "success");
          refreshLlama();
        } else if (ev.type === "error") {
          throw new Error(String(ev.error));
        }
      });
    } catch (err) {
      toast(`Installation llama.cpp échouée : ${err}`, "error");
    } finally {
      setInstallingLlama(false);
    }
  }, [toast, refreshLlama]);

  const loadGguf = useCallback(
    async (unload: boolean) => {
      if (!selectedGguf && !unload) return;
      try {
        await sendJSON("/api/models/load", "POST", { tag: `gguf:${selectedGguf}` });
        toast(unload ? "Modèle déchargé" : `Modèle ${selectedGguf} chargé`, "success");
        refreshLlama();
      } catch (err) {
        toast(`Erreur : ${err}`, "error");
      }
    },
    [selectedGguf, toast, refreshLlama]
  );

  const publishTailscale = useCallback(async () => {
    try {
      const r = await sendJSON<{ url: string }>("/api/tailscale/publish", "POST");
      toast(`LocalAI publié sur Tailscale : ${r.url}`, "success");
      refreshTailscale();
    } catch (err) {
      toast(`Publication impossible : ${err}`, "error");
    }
  }, [toast, refreshTailscale]);

  function applyTheme(t: string) {
    setTheme(t);
    document.documentElement.dataset.theme = t === "light" ? "light" : "";
  }

  const clearKnowledge = useCallback(async () => {
    try {
      const r = await sendJSON<{ deleted: number }>("/api/knowledge", "DELETE");
      toast(`${r.deleted} connaissances effacées`, "success");
      refreshKnowledge();
    } catch (err) {
      toast(`Erreur : ${err}`, "error");
    }
  }, [toast, refreshKnowledge]);

  const addPrompt = useCallback(async () => {
    if (!promptName.trim() || !promptContent.trim()) return;
    try {
      await sendJSON("/api/prompts", "POST", { name: promptName.trim(), content: promptContent.trim() });
      setPromptName("");
      setPromptContent("");
      refreshPrompts();
      toast("Prompt enregistré", "success");
    } catch (err) {
      toast(`Erreur : ${err}`, "error");
    }
  }, [promptName, promptContent, refreshPrompts, toast]);

  const deletePrompt = useCallback(
    async (id: number) => {
      await sendJSON(`/api/prompts/${id}`, "DELETE");
      refreshPrompts();
    },
    [refreshPrompts]
  );

  useEffect(() => {
    getJSON<{ voices: Voice[] }>("/api/tts/voices")
      .then((d) => setVoices(d.voices))
      .catch(() => undefined);
    getJSON<{ settings: Record<string, string> }>("/api/settings")
      .then((d) => {
        if (d.settings.tts_voice) setVoiceId(d.settings.tts_voice);
        if (d.settings.tts_rate) setRate(Number(d.settings.tts_rate));
        if (d.settings.tool_level) setToolLevel(d.settings.tool_level);
        if (!d.settings.tts_voice && !voiceId) {
          /* garde défaut */
        }
      })
      .catch(() => undefined);
    refreshMemories();
  }, []);

  const refreshMemories = useCallback(() => {
    getJSON<{ memories: MemoryItem[] }>("/api/memory")
      .then((d) => setMemories(d.memories))
      .catch(() => undefined);
  }, []);

  const persistSetting = useCallback(
    async (key: string, value: string) => {
      try {
        await sendJSON("/api/settings", "PUT", { key, value });
      } catch {
        /* silencieux */
      }
    },
    []
  );

  const addMemory = useCallback(async () => {
    if (!newMemory.trim()) return;
    try {
      await sendJSON("/api/memory", "POST", { content: newMemory.trim() });
      setNewMemory("");
      refreshMemories();
      toast("Information mémorisée", "success");
    } catch (err) {
      toast(`Échec : ${err}`, "error");
    }
  }, [newMemory, refreshMemories, toast]);

  const deleteMemory = useCallback(
    async (id: number) => {
      await sendJSON(`/api/memory/${id}`, "DELETE");
      refreshMemories();
    },
    [refreshMemories]
  );

  const testVoice = useCallback(async () => {
    try {
      await persistSetting("tts_voice", voiceId);
      await persistSetting("tts_rate", String(rate));
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: "Bonjour, je suis votre assistant local. La synthèse vocale fonctionne.",
          voice: voiceId,
          rate,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const audio = new Audio(URL.createObjectURL(blob));
      await audio.play();
    } catch (err) {
      toast(`Test vocal échoué : ${err}`, "error");
    }
  }, [voiceId, rate, toast, persistSetting]);

  const checkUpdate = useCallback(async () => {
    try {
      const info = await getJSON<{
        update_available: boolean;
        latest_version: string;
        url: string;
        configured: boolean;
      }>("/api/app/check-update");
      setUpdateInfo(info);
      if (!info.configured) {
        toast("Canal de mise à jour non configuré (update_manifest_url)");
      } else if (info.update_available) {
        toast(`Nouvelle version disponible : v${info.latest_version}`, "success");
      } else {
        toast("Application à jour", "success");
      }
    } catch (err) {
      toast(`Vérification impossible : ${err}`, "error");
    }
  }, [toast]);

  return (
    <>
      <div className="view-header">
        <h2>Réglages</h2>
        <span style={{ flex: 1 }} />
        <button onClick={refreshSystem}>Rafraîchir les mesures</button>
      </div>
      <div className="content-scroll">
        <div className="settings-grid">
          <div className="panel">
            <h3>Système</h3>
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
                {systemInfo.gpus.map((g) => (
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
                {systemInfo.disks.map((d) => (
                  <div className="kv" key={d.mount}>
                    <span className="k">Disque {d.mount}</span>
                    <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
                      {d.free_gb} Go libres <Meter value={d.used_percent} />
                    </span>
                  </div>
                ))}
              </>
            ) : (
              <div style={{ color: "var(--text-dim)" }}>Chargement…</div>
            )}
          </div>

          <div className="panel">
            <h3>Mémoire de l'assistant</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              Ces informations sont injectées dans chaque conversation.
            </p>
            <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
              <input
                placeholder="Ex : Je m'appelle Tony, je code en Python"
                value={newMemory}
                onChange={(e) => setNewMemory(e.target.value)}
                style={{ flex: 1 }}
                onKeyDown={(e) => e.key === "Enter" && void addMemory()}
              />
              <button className="primary" onClick={() => void addMemory()}>
                Mémoriser
              </button>
            </div>
            <div className="memory-list">
              {memories.length === 0 && (
                <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>Aucune information.</div>
              )}
              {memories.map((m) => (
                <div key={m.id} className="memory-item">
                  <span>{m.content}</span>
                  <button onClick={() => void deleteMemory(m.id)}>?</button>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <h3>Permissions des outils</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              Niveau maximal que l'IA peut utiliser sans confirmation.
            </p>
            <select
              value={toolLevel}
              onChange={(e) => {
                setToolLevel(e.target.value);
                void persistSetting("tool_level", e.target.value);
              }}
              style={{ width: "100%" }}
            >
              {TOOL_LEVELS.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.id} — {l.desc}
                </option>
              ))}
            </select>
          </div>

          <div className="panel">
            <h3>Domaines d'intérêt</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              Priorise la distillation : tous les sujets restent appris, ceux-ci en premier.
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {interests.map((t) => {
                const on = selectedInterests.includes(t.key);
                return (
                  <button
                    key={t.key}
                    onClick={() => void toggleInterest(t.key)}
                    className={on ? "primary" : ""}
                    style={{ fontSize: 12, padding: "4px 10px", borderRadius: 14 }}
                  >
                    {on ? "✓ " : ""}
                    {t.label}
                  </button>
                );
              })}
              {interests.length === 0 && (
                <span style={{ color: "var(--text-faint)", fontSize: 12.5 }}>Chargement…</span>
              )}
            </div>
          </div>

          <div className="panel">
            <h3>Sécurité fichiers</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              L'assistant est confiné à son dossier d'installation. Accordez-lui
              explicitement les dossiers dont il a besoin.
            </p>
            {securityInfo && (
              <>
                <div className="kv">
                  <span className="k">Règles (type antivirus)</span>
                  <span>v{securityInfo.regles_version}</span>
                </div>
                <div className="kv">
                  <span className="k">Stockage LocalAI</span>
                  <span>
                    {securityInfo.stockage.used_gb} / {securityInfo.stockage.quota_gb} Go
                  </span>
                </div>
                <div style={{ margin: "8px 0" }}>
                  {securityInfo.dossiers_autorises.length === 0 && (
                    <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>
                      Aucun dossier accordé.
                    </div>
                  )}
                  {securityInfo.dossiers_autorises.map((d) => (
                    <div key={d} className="memory-item">
                      <span title={d} style={{ fontFamily: "Consolas, monospace", fontSize: 12 }}>
                        {d}
                      </span>
                      <button onClick={() => void revokeFolder(d)}>✕</button>
                    </div>
                  ))}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <input
                    placeholder="D:\MonDossier"
                    value={newFolder}
                    onChange={(e) => setNewFolder(e.target.value)}
                    style={{ flex: 1 }}
                    onKeyDown={(e) => e.key === "Enter" && void grantFolder()}
                  />
                  <button className="primary" onClick={() => void grantFolder()}>
                    Accorder
                  </button>
                </div>
              </>
            )}
          </div>

          <div className="panel">
            <h3>Génération d'images (local)</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              Stable Diffusion auto-hébergé via stable-diffusion.cpp — aucun service externe.
            </p>
            {sdStatus && (
              <>
                <div className="kv">
                  <span className="k">État</span>
                  <span className={sdStatus.pret ? "status-ok" : "status-bad"}>
                    {sdStatus.pret
                      ? `prêt (${sdStatus.modele.split("\\").pop()})`
                      : sdStatus.binaire
                        ? "binaire OK, modèle manquant"
                        : "non installé"}
                  </span>
                </div>
                {!sdStatus.pret && (
                  <button disabled={sdInstalling} onClick={() => void installSd()}>
                    {sdInstalling ? "Téléchargement (~1,5 Go)…" : "Installer le générateur"}
                  </button>
                )}
              </>
            )}
            {sdStatus?.pret && (
              <>
                <div style={{ display: "flex", gap: 8, margin: "10px 0" }}>
                  <input
                    placeholder="Décris l'image à générer…"
                    value={sdPrompt}
                    onChange={(e) => setSdPrompt(e.target.value)}
                    style={{ flex: 1 }}
                    onKeyDown={(e) => e.key === "Enter" && void generateImage()}
                  />
                  <button className="primary" disabled={sdGenerating} onClick={() => void generateImage()}>
                    {sdGenerating ? "…" : "Générer"}
                  </button>
                </div>
                {sdImageUrl && (
                  <img
                    src={sdImageUrl}
                    alt="générée"
                    style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border)" }}
                  />
                )}
              </>
            )}
          </div>

          <div className="panel">
            <h3>Image vers 3D</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              Photo → mesh imprimable via TripoSR local (équivalent Meshy auto-hébergé).
              L'agent 3D utilise aussi cet outil en conversation.
            </p>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                placeholder="D:\chemin\vers\image.jpg"
                value={i3dPath}
                onChange={(e) => setI3dPath(e.target.value)}
                style={{ flex: 1 }}
                onKeyDown={(e) => e.key === "Enter" && void imageTo3d()}
              />
              <button className="primary" disabled={i3dBusy || !i3dPath.trim()} onClick={() => void imageTo3d()}>
                {i3dBusy ? "Conversion (~30 s)…" : "Convertir en STL"}
              </button>
            </div>
            {i3dResult && (
              <p style={{ fontSize: 12.5, color: "var(--text-dim)", wordBreak: "break-all" }}>
                {i3dResult.slice(0, 300)}
              </p>
            )}
          </div>

          <div className="panel">
            <h3>Stockage des modèles</h3>
            <div className="kv">
              <span className="k">Dossier actuel (OLLAMA_MODELS)</span>
              <span style={{ fontFamily: "Consolas, monospace", fontSize: 12 }}>
                {systemInfo?.models_dir || "par défaut (~/.ollama/models)"}
              </span>
            </div>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, lineHeight: 1.6 }}>
              Pour déplacer le stockage, définissez la variable système OLLAMA_MODELS vers
              le dossier voulu puis redémarrez Ollama. L'installeur configure cela automatiquement.
            </p>
          </div>

          <div className="panel">
            <h3>Synthèse vocale (TTS)</h3>
            <div className="settings-row">
              <label>Voix</label>
              <select value={voiceId} onChange={(e) => setVoiceId(e.target.value)}>
                {voices.length === 0 && <option value="">Aucune voix détectée</option>}
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} ({v.languages.join(", ")})
                  </option>
                ))}
              </select>
            </div>
            <div className="settings-row">
              <label>Vitesse : {rate}</label>
              <input
                type="range"
                min={100}
                max={300}
                step={5}
                value={rate}
                onChange={(e) => setRate(Number(e.target.value))}
              />
            </div>
            <label className="toggle" style={{ marginBottom: 10 }}>
              <input
                type="checkbox"
                checked={autoTtsPlay}
                onChange={(e) => {
                  setAutoTtsPlay(e.target.checked);
                  void persistSetting("tts_autoplay", e.target.checked ? "1" : "0");
                }}
              />
              Lire automatiquement chaque réponse
            </label>
            <label className="toggle" style={{ marginBottom: 10 }}>
              <input
                type="checkbox"
                checked={followUp}
                onChange={async (e) => {
                  const on = e.target.checked;
                  setFollowUp(on);
                  await persistSetting("tts_followup", on ? "1" : "0");
                  if (on && !voiceId) {
                    try {
                      const d = await getJSON<{ voices: Voice[] }>("/api/tts/voices");
                      const femFr =
                        d.voices.find(
                          (v) =>
                            v.gender === "F" &&
                            v.languages.some((l) => l.toLowerCase().includes("fr"))
                        ) || d.voices.find((v) => v.gender === "F");
                      if (femFr) {
                        setVoiceId(femFr.id);
                        await persistSetting("tts_voice", femFr.id);
                        toast(`Voix sélectionnée : ${femFr.name}`, "success");
                      }
                    } catch {
                      /* silencieux */
                    }
                  }
                }}
              />
              Demander après chaque réponse si elle convient (voix)
            </label>
            <button className="primary" disabled={voices.length === 0} onClick={() => void testVoice()}>
              Tester et enregistrer
            </button>
          </div>

          <div className="panel">
            <h3>Moteur d'inférence</h3>
            <div className="settings-row">
              <label>Backend actif</label>
              <select
                value={llamaStatus?.active_backend || "ollama"}
                onChange={(e) => {
                  void persistSetting("inference_backend", e.target.value);
                  setTimeout(refreshLlama, 300);
                }}
              >
                <option value="ollama">Ollama (recommandé)</option>
                <option value="llamacpp">llama.cpp (avancé)</option>
              </select>
            </div>
            {llamaStatus && (
              <>
                <div className="kv">
                  <span className="k">llama-server</span>
                  <span className={llamaStatus.binary_installed ? "status-ok" : "status-bad"}>
                    {llamaStatus.binary_installed ? "installé" : "non installé"}
                    {llamaStatus.server_running ? " · serveur actif" : ""}
                  </span>
                </div>
                {!llamaStatus.binary_installed && (
                  <button disabled={installingLlama} onClick={() => void installLlamaServer()}>
                    {installingLlama ? "Téléchargement CUDA (~1 Go)…" : "Télécharger llama-server"}
                  </button>
                )}
                {llamaStatus.binary_installed && (
                  <>
                    <div className="settings-row">
                      <label>Modèle GGUF ({llamaStatus.gguf_dir})</label>
                      <select
                        value={selectedGguf}
                        onChange={(e) => setSelectedGguf(e.target.value)}
                        disabled={llamaStatus.models.length === 0}
                      >
                        {llamaStatus.models.length === 0 && <option value="">Aucun .gguf</option>}
                        {llamaStatus.models.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button className="primary" onClick={() => void loadGguf(false)}>
                        Charger
                      </button>
                      <button onClick={() => void loadGguf(true)}>Décharger</button>
                    </div>
                  </>
                )}
              </>
            )}
            <p style={{ color: "var(--text-dim)", fontSize: 12, lineHeight: 1.6 }}>
              Les modèles GGUF se déposent dans le dossier indiqué. RAG et outils
              restent assurés par Ollama.
            </p>
          </div>

          <div className="panel">
            <h3>Accès distant — Tailscale</h3>
            {tsStatus === null && <div style={{ color: "var(--text-dim)" }}>…</div>}
            {tsStatus && !tsStatus.installed && (
              <p style={{ color: "var(--text-dim)", fontSize: 12.5, lineHeight: 1.6 }}>
                Tailscale n'est pas installé. Relancez LocalAI-Setup.exe et cochez
                « Accès distant (Tailscale) », ou installez-le depuis tailscale.com.
              </p>
            )}
            {tsStatus && tsStatus.installed && (
              <>
                <div className="kv">
                  <span className="k">État</span>
                  <span className={tsStatus.state === "Running" ? "status-ok" : "status-bad"}>
                    {tsStatus.state || "inconnu"}
                  </span>
                </div>
                {tsStatus.ip4 && (
                  <div className="kv">
                    <span className="k">IP tailnet</span>
                    <span>{tsStatus.ip4}</span>
                  </div>
                )}
                <div className="kv">
                  <span className="k">LocalAI publié</span>
                  <span className={tsStatus.serve_published ? "status-ok" : "status-bad"}>
                    {tsStatus.serve_published ? "oui" : "non"}
                  </span>
                </div>
                {!tsStatus.serve_published && (
                  <button className="primary" onClick={() => void publishTailscale()}>
                    Publier sur le réseau Tailscale
                  </button>
                )}
              </>
            )}
          </div>

          <div className="panel">
            <h3>Apprentissage continu</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              L'assistant mémorise chaque connaissance nouvelle et répond
              instantanément aux questions déjà rencontrées.
            </p>
            <label className="toggle" style={{ marginBottom: 10 }}>
              <input
                type="checkbox"
                checked={autoLearn}
                onChange={(e) => {
                  setAutoLearn(e.target.checked);
                  void persistSetting("auto_learn", e.target.checked ? "1" : "0");
                }}
              />
              Apprendre automatiquement des conversations
            </label>
            {kStats && (
              <>
                <div className="kv">
                  <span className="k">Connaissances apprises</span>
                  <span>{kStats.count}</span>
                </div>
                <div className="kv">
                  <span className="k">Réutilisations</span>
                  <span>{kStats.reuse_total}</span>
                </div>
              </>
            )}
            <button onClick={() => void clearKnowledge()}>Vider la mémoire apprise</button>
          </div>

          <div className="panel">
            <h3>Distillation des modèles</h3>
            <p style={{ color: "var(--text-dim)", fontSize: 12.5, marginTop: 0 }}>
              Les réponses faibles sont régénérées par le plus gros modèle puis
              reversées dans la mémoire : le petit modèle devient plus pertinent
              à chaque cycle. Le dataset exporté sert au fine-tuning LoRA.
            </p>
            {distillStats && (
              <>
                <div className="kv">
                  <span className="k">Réponses notées</span>
                  <span>{distillStats.scored_answers}</span>
                </div>
                <div className="kv">
                  <span className="k">Réponses faibles (&lt; {distillStats.weak_threshold})</span>
                  <span>{distillStats.weak_answers}</span>
                </div>
                <div className="kv">
                  <span className="k">Bonnes réponses (≥ 70)</span>
                  <span>{distillStats.good_answers}</span>
                </div>
              </>
            )}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button onClick={() => void runDistillUpgrade()} disabled={distilling}>
                {distilling ? "Distillation en cours…" : "Améliorer les réponses faibles"}
              </button>
              <button
                onClick={() => {
                  window.open("/api/distill/export", "_blank");
                  toast("Dataset SFT exporté");
                }}
              >
                Exporter dataset (JSONL)
              </button>
              <button onClick={() => void prepareLora()} disabled={loraPreparing}>
                {loraPreparing ? "Préparation…" : "Préparer atelier LoRA"}
              </button>
            </div>
            {loraResult && (
              <p style={{ fontSize: 12.5, color: "var(--green)" }}>
                Atelier prêt : {loraResult.total_exemples} exemples dans data/lora_atelier/
                (SFT + traces + DPO + script d'entraînement {loraResult.tier})
              </p>
            )}
            {distillResult && (
              <p style={{ fontSize: 12.5, color: distillResult.upgraded > 0 ? "var(--green)" : "var(--orange)" }}>
                {distillResult.upgraded} réponse(s) améliorée(s) avec {distillResult.model_used}
                {distillResult.skipped > 0 && ` — ${distillResult.skipped} ignorée(s)`}
              </p>
            )}
          </div>

          <div className="panel">
            <h3>Bibliothèque de prompts</h3>
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              <input
                placeholder="Nom"
                value={promptName}
                onChange={(e) => setPromptName(e.target.value)}
                style={{ width: 110 }}
              />
              <input
                placeholder="Contenu du prompt…"
                value={promptContent}
                onChange={(e) => setPromptContent(e.target.value)}
                style={{ flex: 1 }}
                onKeyDown={(e) => e.key === "Enter" && void addPrompt()}
              />
              <button className="primary" onClick={() => void addPrompt()}>
                +
              </button>
            </div>
            <div className="memory-list">
              {promptList.length === 0 && (
                <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>Aucun prompt enregistré.</div>
              )}
              {promptList.map((p) => (
                <div key={p.id} className="memory-item">
                  <span title={p.content}>{p.name}</span>
                  <button onClick={() => void deletePrompt(p.id)}>✕</button>
                </div>
              ))}
            </div>
          </div>

          <div className="panel">
            <h3>Apparence</h3>
            <div className="settings-row">
              <label>Thème</label>
              <select
                value={theme}
                onChange={(e) => {
                  applyTheme(e.target.value);
                  void persistSetting("theme", e.target.value);
                }}
              >
                <option value="dark">Sombre</option>
                <option value="light">Clair</option>
              </select>
            </div>
          </div>

          <div className="panel">
            <h3>Statistiques d'usage</h3>
            {usage ? (
              <>
                <div className="kv">
                  <span className="k">Conversations</span>
                  <span>{usage.conversations}</span>
                </div>
                <div className="kv">
                  <span className="k">Réponses générées</span>
                  <span>{usage.assistant_messages}</span>
                </div>
                <div className="kv">
                  <span className="k">Tokens produits</span>
                  <span>{(usage.completion_tokens / 1000).toFixed(1)} k</span>
                </div>
                {usage.by_model.slice(0, 4).map((m) => (
                  <div className="kv" key={m.model}>
                    <span className="k">{m.model}</span>
                    <span>
                      {m.n} rép. · {(m.tokens / 1000).toFixed(1)} k tok
                    </span>
                  </div>
                ))}
              </>
            ) : (
              <div style={{ color: "var(--text-dim)" }}>Chargement…</div>
            )}
          </div>

          <div className="panel">
            <h3>Agent vocal instantané (mode auto)</h3>
            <div className="kv">
              <span className="k">Statut STT (whisper)</span>
              <span>{tgStatus?.poller_running !== undefined ? "voir ci-dessous" : "chargement…"}</span>
            </div>
            <div style={{ marginTop: 8, color: "var(--text-dim)", fontSize: 13 }}>
              Le micro 🎤 dans la conversation active : transcription → réponse → synthèse vocale automatique.
            </div>
          </div>

          <div className="panel">
            <h3>Télégramme</h3>
            {tgStatus && (
              <div className="kv">
                <span className="k">État</span>
                <span>
                  {tgStatus.configured
                    ? tgStatus.poller_running
                      ? "Poller actif ●"
                      : "Poller inactif"
                    : "Non configuré"}
                </span>
              </div>
            )}
            <div className="field-row">
              <input
                placeholder="Token du bot (…123456:AA-BB)"
                value={tgToken}
                onChange={(e) => setTgToken(e.target.value)}
                style={{ flex: 1 }}
              />
              <button
                onClick={async () => {
                  await sendJSON("/api/telegram/config", "PUT", {
                    token: tgToken,
                    enabled: tgEnabled,
                    model: tgModel,
                  });
                  toast("Configuration Télégramme enregistrée", "success");
                  refreshTelegram();
                }}
              >
                Enregistrer
              </button>
            </div>
            <label className="toggle">
              <input type="checkbox" checked={tgEnabled} onChange={(e) => setTgEnabled(e.target.checked)} />
              Bot actif
            </label>
            <button
              onClick={async () => {
                const r = await sendJSON<{ ok: boolean; bot?: string; error?: string }>(
                  "/api/telegram/check",
                  "POST"
                );
                toast(
                  r.ok ? `Bot @${r.bot} valide` : `Télégramme : ${r.error ?? "inconnu"}`,
                  r.ok ? "success" : "error"
                );
              }}
            >
              Tester le token
            </button>
            {tgStatus?.last_error ? (
              <div style={{ marginTop: 6, color: "var(--red)", fontSize: 12 }}>{tgStatus.last_error}</div>
            ) : null}
          </div>

          <div className="panel">
            <h3>LiveKit (audio temps réel)</h3>
            {lkStatus && (
              <div className="kv">
                <span className="k">Serveur</span>
                <span>{lkStatus.url || "non configuré"}</span>
              </div>
            )}
            <div className="field-row" style={{ flexDirection: "column", gap: 8 }}>
              <input placeholder="URL du serveur LiveKit (ws://…)" value={lkUrl} onChange={(e) => setLkUrl(e.target.value)} />
              <input placeholder="Clé API" value={lkKey} onChange={(e) => setLkKey(e.target.value)} />
              <input placeholder="Secret API" type="password" value={lkSecret} onChange={(e) => setLkSecret(e.target.value)} />
            </div>
            <div style={{ marginTop: 8 }}>
              <button
                onClick={async () => {
                  await sendJSON("/api/livekit/config", "PUT", {
                    url: lkUrl,
                    api_key: lkKey,
                    api_secret: lkSecret,
                  });
                  toast("Configuration LiveKit enregistrée", "success");
                  refreshLivekit();
                }}
              >
                Enregistrer
              </button>
            </div>
          </div>

          <div className="panel">
            <h3>Style de communication adaptatif</h3>
            <label className="toggle">
              <input
                type="checkbox"
                checked={styleEnabled}
                onChange={async (e) => {
                  const on = e.target.checked;
                  setStyleEnabled(on);
                  await persistSetting("style_auto", on ? "1" : "0");
                }}
              />
              Style automatique (calé sur vos échanges)
            </label>
            <div style={{ marginTop: 10 }}>
              <button
                onClick={async () => {
                  try {
                    await sendJSON("/api/style/refresh", "POST", {});
                    toast("Analyse du style lancée (30 à 90 s)", "success");
                  } catch {
                    toast("Analyse impossible", "error");
                  }
                }}
              >
                Ré-évaluer mon style
              </button>
            </div>
            {styleProfile && (
              <pre
                style={{
                  marginTop: 10,
                  background: "var(--bg-2)",
                  padding: 8,
                  borderRadius: 6,
                  fontSize: 12,
                  maxHeight: 160,
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                }}
              >
                {styleProfile}
              </pre>
            )}
          </div>

          <div className="panel">
            <h3>Application</h3>
            <div className="kv">
              <span className="k">Version</span>
              <span>v{systemInfo?.app.version ?? "?"}</span>
            </div>
            <div style={{ marginTop: 12 }}>
              <button onClick={() => void checkUpdate()}>Rechercher une mise à jour</button>
              {updateInfo && updateInfo.update_available && (
                <div style={{ marginTop: 10, color: "var(--green)", fontSize: 13 }}>
                  Version {updateInfo.latest_version} disponible —{" "}
                  <a href={updateInfo.url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
                    télécharger
                  </a>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
