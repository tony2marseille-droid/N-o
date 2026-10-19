import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AgentDef,
  ChatStats,
  ModelEntry,
  ToolActivity,
} from "../lib/api";
import { getJSON, postVoiceSSE, streamSSE } from "../lib/api";
import PipelinePanel, { type PipelineStep } from "./PipelinePanel";
import MatrixRain from "./MatrixRain";

interface Props {
  models: ModelEntry[];
  selectedModel: string;
  onSelectModel: (tag: string) => void;
  conversationId: number | null;
  initialPrompt?: string | null;
  onConversationCreated: (id: number) => void;
  onAfterResponse?: () => void;
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
}

interface DisplayMessage {
  id?: number;
  role: "user" | "assistant";
  content: string;
  stats?: ChatStats;
  model?: string;
  tools?: ToolActivity[];
  sources?: { filename: string; url?: string }[];
  routedFrom?: string;
  learned?: boolean;
  score?: { value: number; avis: string };
}

function SpeakerIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
    </svg>
  );
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderContent(text: string) {
  const safe = escapeHtml(text);
  const withLinks = safe.replace(
    /\[([^\]]{1,120})\]\((file:\/\/[^\s)]+|https?:\/\/[^\s)]+)\)/g,
    (_m, label: string, url: string) => {
      if (/\.(png|jpe?g|webp)(\?.*)?$/i.test(url)) {
        const chemin = decodeURIComponent(url.replace(/^file:\/\/\/?/i, ""));
        return `<img class="msg-image" alt="${label}" src="/api/sd/file?path=${encodeURIComponent(chemin)}" />`;
      }
      return `<a href="${url}" target="_blank" rel="noreferrer">${label}</a>`;
    }
  );
  const styled = withLinks
    .replace(/\*\*([^*\n]{1,200})\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`\n]{1,200})`/g, "<code>$1</code>")
    // Chemins d'images locales cites par l'assistant -> apercu inline.
    .replace(
      /\b([A-Za-z]:\\[^\s<>"'`]+?\.(?:png|jpe?g|webp))\b/gi,
      (_m, p: string) =>
        `<img class="msg-image" alt="image" src="/api/sd/file?path=${encodeURIComponent(p)}" />`
    );
  const parts = styled.split(/(<a [^>]*>.*?<\/a>)/g);
  return parts.map((part, i) => {
    if (part.startsWith("<a ")) {
      const m = part.match(/href="([^"]+)"[^>]*>([\s\S]*)<\/a>/);
      if (m) {
        return (
          <a key={i} href={m[1]} target="_blank" rel="noreferrer">
            {m[2]}
          </a>
        );
      }
      return null;
    }
    return <span key={i} dangerouslySetInnerHTML={{ __html: part }} />;
  });
}

export default function ChatView({
  models,
  selectedModel,
  onSelectModel,
  conversationId,
  initialPrompt,
  onConversationCreated,
  onAfterResponse,
  toast,
}: Props) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState(initialPrompt ?? "");
  const [streaming, setStreaming] = useState(false);
  const [agents, setAgents] = useState<AgentDef[]>([]);
  const [agentId, setAgentId] = useState(() => localStorage.getItem("localai.agent") ?? "auto");
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [ragEnabled, setRagEnabled] = useState(false);
  const [webEnabled, setWebEnabled] = useState(false);
  const [pendingImages, setPendingImages] = useState<{ name: string; base64: string }[]>([]);
  const [prompts, setPrompts] = useState<{ id: number; name: string; content: string }[]>([]);
  const [autoTts, setAutoTts] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const [recording, setRecording] = useState(false);
  // Pipeline live : agent/modele/ETA + etapes d'outils (panneau Matrix).
  const [pipeline, setPipeline] = useState<{ agent: string; model: string; etaMs: number | null } | null>(null);
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const bufRef = useRef("");
  const flushRef = useRef<number | null>(null);

  const flushBuf = useCallback(() => {
    const s = bufRef.current;
    if (!s) return;
    bufRef.current = "";
    setMessages((prev) => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      if (!last || last.role !== "assistant") return copy;
      last.content += s;
      copy[copy.length - 1] = { ...last };
      return copy;
    });
  }, []);

  const attachFiles = useCallback((files: FileList | null) => {
    if (!files) return;
    for (const file of Array.from(files).slice(0, 4)) {
      if (!file.type.startsWith("image/")) continue;
      const reader = new FileReader();
      reader.onload = () => {
        const full = String(reader.result ?? "");
        const base64 = full.split(",")[1] ?? "";
        if (base64) setPendingImages((prev) => [...prev.slice(-3), { name: file.name, base64 }]);
      };
      reader.readAsDataURL(file);
    }
  }, []);

  useEffect(() => {
    getJSON<{ agents: AgentDef[] }>("/api/agents")
      .then((d) => setAgents(d.agents))
      .catch(() => undefined);
    getJSON<{ prompts: { id: number; name: string; content: string }[] }>("/api/prompts")
      .then((d) => setPrompts(d.prompts))
      .catch(() => undefined);
    getJSON<{ settings: Record<string, string> }>("/api/settings")
      .then((d) => setAutoTts(d.settings.tts_autoplay === "1"))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    localStorage.setItem("localai.agent", agentId);
    const agent = agents.find((a) => a.id === agentId);
    if (agent) setRagEnabled(agent.rag);
  }, [agentId, agents]);

  useEffect(() => {
    if (conversationId === null) {
      setMessages([]);
      return;
    }
    getJSON<{ messages: DisplayMessage[] }>(`/api/conversations/${conversationId}/messages`)
      .then((data) =>
        setMessages(
          data.messages
            .filter((m) => (m as unknown as { role: string }).role !== "system")
            .map((m) => ({
              id: m.id,
              role: (m as unknown as { role: "user" | "assistant" }).role,
              content: m.content,
              model: m.model,
            }))
        )
      )
      .catch(() => toast("Impossible de charger la conversation", "error"));
  }, [conversationId, toast]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const speak = useCallback(async (text: string, quiet = false) => {
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      audioRef.current?.pause();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch (err) {
      if (!quiet) toast(`Synthèse vocale impossible : ${err}`, "error");
    }
  }, [toast]);

  const copyMessage = useCallback((text: string) => {
    void navigator.clipboard.writeText(text).then(
      () => toast("Copié dans le presse-papiers", "success"),
      () => toast("Copie impossible", "error")
    );
  }, [toast]);

  const exportMd = useCallback(async () => {
    if (!conversationId) return;
    try {
      const resp = await fetch(`/api/conversations/${conversationId}/export`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `conversation-${conversationId}.md`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (err) {
      toast(`Export impossible : ${err}`, "error");
    }
  }, [conversationId, toast]);

  const send = useCallback(async (overrideText?: string, isRegenerate = false) => {
    const text = (overrideText ?? input).trim();
    const images = pendingImages.map((p) => p.base64);
    if ((!text && images.length === 0 && !isRegenerate) || streaming) return;
    if (!isRegenerate) {
      setInput("");
      setPendingImages([]);
    } else {
      setMessages((prev) => {
        const copy = [...prev];
        while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
        return copy;
      });
    }
    setStreaming(true);
    const assistantMsg: DisplayMessage = { role: "assistant", content: "", tools: [] };
    if (!isRegenerate) {
      setMessages((prev) => [...prev, { role: "user", content: text + (images.length ? ` [${images.length} image${images.length > 1 ? "s" : ""}]` : "") }, assistantMsg]);
    } else {
      setMessages((prev) => [...prev, assistantMsg]);
    }
    setPipeline(null);
    setSteps([]);
    // Lissage du streaming : les tokens arrivent par salles irregulieres ;
    // on les accumule et on affiche par petites volees regulieres (effet
    // machine a ecrire fluide, sans saccades).
    bufRef.current = "";
    if (flushRef.current !== null) {
      window.clearInterval(flushRef.current);
    }
    flushRef.current = window.setInterval(flushBuf, 30);

    const controller = new AbortController();
    abortRef.current = controller;
    let finalContent = "";

    try {
      let convId = conversationId;
      await streamSSE(
        "/api/chat",
        {
          conversation_id: convId,
          model: selectedModel || "auto",
          message: text || "Décris cette image.",
          temperature: 0.5,
          agent_id: agentId === "general" ? null : agentId,          tools_enabled: toolsEnabled,
          rag_enabled: ragEnabled,
          web_enabled: webEnabled,
          images,
          regenerate: isRegenerate,
        },
        (event) => {
          if (event.type === "meta") {
            const id = event.conversation_id as number;
            if (convId === null && id) {
              convId = id;
              onConversationCreated(id);
            }
            setPipeline({
              agent: String(event.agent ?? ""),
              model: String(event.model ?? ""),
              etaMs: typeof event.eta_ms === "number" ? (event.eta_ms as number) : null,
            });
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              last.model = event.model as string;
              last.learned = Boolean(event.learned);
              last.routedFrom =
                (event.route_reason as string) || undefined;
              last.sources = ((event.rag_sources as { filename: string; url?: string }[]) ?? [])
                .filter(Boolean)
                .map((s) => ({ filename: s.filename, url: s.url }));
              copy[copy.length - 1] = { ...last };
              return copy;
            });
          } else if (event.type === "token") {
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              if (!last || last.role !== "assistant") return copy;
              const contenu = last.content + (event.content as string);
              finalContent = contenu;
              copy[copy.length - 1] = { ...last, content: contenu };
              return copy;
            });
          } else if (event.type === "tool_start") {
            setSteps((prev) => [
              ...prev,
              { name: String(event.name), at: Date.now(), done: false },
            ]);
          } else if (event.type === "tool_exec") {
            setSteps((prev) => {
              const copy = [...prev];
              const idx = copy.findIndex((s) => s.name === event.name && !s.done);
              const step: PipelineStep = {
                name: String(event.name),
                at: idx >= 0 ? copy[idx].at : Date.now(),
                denied: Boolean(event.denied),
                preview: String(event.preview ?? ""),
                done: true,
              };
              if (idx >= 0) {
                copy[idx] = step;
              } else {
                copy.push(step);
              }
              return copy;
            });
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              const activity: ToolActivity = {
                name: event.name as string,
                denied: Boolean(event.denied),
                preview: String(event.preview ?? ""),
              };
              last.tools = [...(last.tools ?? []), activity];
              copy[copy.length - 1] = { ...last };
              return copy;
            });
          } else if (event.type === "done") {
            flushBuf();
            // Repli anti-ecran-vide : si aucun token n'a ete rendu, on
            // recharge la reponse stockee depuis l'API.
            if (!finalContent && convId) {
              const cidDone = convId;
              window.setTimeout(() => {
                getJSON<{ messages: DisplayMessage[] }>(
                  `/api/conversations/${cidDone}/messages`
                )
                  .then((data) =>
                    setMessages(
                      data.messages
                        .filter((m) => (m as unknown as { role: string }).role !== "system")
                        .map((m) => ({
                          id: m.id,
                          role: (m as unknown as { role: "user" | "assistant" }).role,
                          content: m.content,
                          model: m.model,
                        }))
                    )
                  )
                  .catch(() => undefined);
              }, 400);
            }
            const stats: ChatStats = {
              prompt_tokens: event.prompt_tokens as number,
              completion_tokens: event.completion_tokens as number,
              total_duration_ms: event.duration_ms as number,
              tokens_per_second: event.tokens_per_second as number,
            };
            const learned = Boolean(event.learned);
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              copy[copy.length - 1] = {
                ...last,
                id: event.assistant_message_id as number,
                stats,
                learned,
              };
              return copy;
            });
            if (autoTts && finalContent) void speak(finalContent.slice(0, 1200), true);
            onAfterResponse?.();
          } else if (event.type === "tts_followup") {
            const texte = String(event.text ?? "");
            if (texte) {
              window.setTimeout(() => void speak(texte, true), 900);
            }
          } else if (event.type === "score") {
            const grade = {
              value: Number(event.score ?? 0),
              avis: String(event.avis ?? ""),
            };
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              copy[copy.length - 1] = { ...last, score: grade };
              return copy;
            });
          } else if (event.type === "error") {
            throw new Error(String(event.error));
          }
        },
        controller.signal
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        flushBuf();
        toast(`Erreur de génération : ${err}`, "error");
        setMessages((prev) => {
          const copy = [...prev];
          const last = copy[copy.length - 1];
          if (last && !last.content && !(last.tools?.length)) {
            return [...copy.slice(0, -1)];
          }
          return copy;
        });
      }
    } finally {
      if (flushRef.current !== null) {
        window.clearInterval(flushRef.current);
        flushRef.current = null;
      }
      flushBuf();
      setStreaming(false);
      abortRef.current = null;
    }
  }, [input, pendingImages, streaming, selectedModel, agentId, toolsEnabled, ragEnabled, conversationId, onConversationCreated, onAfterResponse, autoTts, speak, toast]);

  const regenerate = useCallback(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    void send(lastUser.content.replace(/ \[\d+ images?\]$/, ""), true);
  }, [messages, send]);

  const sendVoice = useCallback(async (blob: Blob) => {
    if (streaming) return;
    setStreaming(true);
    const assistantMsg: DisplayMessage = { role: "assistant", content: "", tools: [] };
    setMessages((prev) => [...prev, { role: "user", content: "🎤 (vocal)" }, assistantMsg]);
    const controller = new AbortController();
    abortRef.current = controller;
    let finalContent = "";
    try {
      let convId = conversationId;
      await postVoiceSSE(blob, convId, {
        model: selectedModel || "auto",
        temperature: 0.3,
        tts: true,
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === "meta") {
            const id = event.conversation_id as number;
            if (convId === null && id) {
              convId = id;
              onConversationCreated(id);
            }
            setMessages((prev) => {
              const copy = [...prev];
              const user = copy[copy.length - 2];
              if (user) user.content = `🎤 ${String(event.transcript ?? "vocal")}`;
              copy[copy.length - 1].model = event.model as string;
              return [...copy];
            });
          } else if (event.type === "token") {
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              last.content += event.content as string;
              finalContent = last.content;
              return [...copy.slice(0, -1), { ...last }];
            });
          } else if (event.type === "done") {
            setMessages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              copy[copy.length - 1] = {
                ...last,
                id: event.assistant_message_id as number,
                stats: {
                  total_duration_ms: event.duration_ms as number,
                  prompt_tokens: event.prompt_tokens as number,
                  completion_tokens: event.completion_tokens as number,
                  tokens_per_second: event.tokens_per_second as number,
                },
              };
              return copy;
            });
            if (autoTts && finalContent) void speak(finalContent.slice(0, 1200), true);
          } else if (event.type === "tts") {
            const fileName = event.file as string;
            if (fileName) {
              const audio = new Audio(`/api/voice/wav/${fileName}`);
              audioRef.current = audio;
              void audio.play().catch(() => undefined);
            }
          } else if (event.type === "error") {
            throw new Error(String(event.error));
          }
        },
      });
    } catch (err) {
      toast(`Erreur vocale : ${err}`, "error");
      setMessages((prev) => prev.filter((m) => m.content || m.tools?.length));
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, selectedModel, conversationId, onConversationCreated, autoTts, speak, toast]);

  const toggleRecord = useCallback(async () => {
    if (recording) {
      const rec = mediaRef.current;
      if (rec && rec.state !== "inactive") rec.stop();
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      toast("Micro inaccessible", "error");
      return;
    }
    chunksRef.current = [];
    const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    mediaRef.current = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    mediaRef.current.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    mediaRef.current.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
      stream.getTracks().forEach((t) => t.stop());
      setRecording(false);
      if (blob.size > 0) void sendVoice(blob);
    };
    mediaRef.current.start();
    setRecording(true);
    toast("🎤 Enregistrement… parlez", "info");
  }, [recording, sendVoice, toast]);

  const insertPrompt = useCallback(
    (id: string) => {
      const p = prompts.find((x) => String(x.id) === id);
      if (!p) return;
      setInput((prev) => (prev ? `${prev}\n${p.content}` : p.content));
    },
    [prompts]
  );

  return (
    <div className="chat-container">
      {/* Fond dynamique Matrix (derriere les messages, discret). */}
      <MatrixRain background />
      <div className="view-header">
        <h2>Conversation</h2>
        <select
          value={selectedModel}
          onChange={(e) => onSelectModel(e.target.value)}
          style={{ minWidth: 210 }}
        >
          <option value="auto">Auto (routage intelligent)</option>
          {models.filter((m) => m.installed).map((m) => (
            <option key={m.tag} value={m.tag}>
              {m.name} ({m.parameter_size || m.size_label}){m.loaded ? " ●" : ""}
              {m.capabilities.includes("vision") ? " 👁" : ""}
            </option>
          ))}
        </select>
        <select value={agentId} onChange={(e) => setAgentId(e.target.value)} style={{ minWidth: 160 }}>
          <option value="auto">Agent : Auto (intelligent)</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              Agent : {a.name}
            </option>
          ))}
        </select>
        <label className="toggle">
          <input type="checkbox" checked={toolsEnabled} onChange={(e) => setToolsEnabled(e.target.checked)} />
          Outils
        </label>
        <label className="toggle">
          <input type="checkbox" checked={webEnabled} onChange={(e) => setWebEnabled(e.target.checked)} />
          Internet
        </label>
        <label className="toggle">
          <input type="checkbox" checked={ragEnabled} onChange={(e) => setRagEnabled(e.target.checked)} />
          Documents
        </label>
        {prompts.length > 0 && (
          <select
            title="Bibliothèque de prompts"
            value=""
            onChange={(e) => insertPrompt(e.target.value)}
            style={{ maxWidth: 170 }}
          >
            <option value="">📚 Prompts…</option>
            {prompts.map((p) => (
              <option key={p.id} value={String(p.id)}>
                {p.name}
              </option>
            ))}
          </select>
        )}
        <button title="Exporter en Markdown" disabled={!conversationId} onClick={() => void exportMd()}>
          ⬇ MD
        </button>
        {streaming && (
          <button onClick={() => abortRef.current?.abort()} className="danger">
            Arrêter
          </button>
        )}
      </div>

      {streaming && pipeline && (
        <PipelinePanel
          agent={pipeline.agent}
          model={pipeline.model}
          etaMs={pipeline.etaMs}
          steps={steps}
          streaming={streaming}
        />
      )}

      <div className="messages">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="big">Bienvenue sur LocalAI</div>
            <div>100% local — aucun envoi sur internet.</div>
            <div>Mode Auto : le modèle est choisi selon votre demande.</div>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={m.id ?? `live-${i}`} className={`msg ${m.role}`}>
            {renderContent(m.content)}
            {m.role === "assistant" && i === messages.length - 1 && streaming && !m.content && (
              <span className="cursor-blink" />
            )}
            {m.role === "assistant" && (m.content || (m.tools?.length ?? 0) > 0) && (
              <>
                {(m.tools?.length ?? 0) > 0 && (
                  <div className="tool-trace">
                    {m.tools!.map((t, ti) => (
                      <div key={ti} className={`tool-line ${t.denied ? "denied" : ""}`}>
                        ⚙ {t.name} → {t.preview.split("\n")[0].slice(0, 120)}
                        {t.denied ? " (refusé)" : ""}
                      </div>
                    ))}
                  </div>
                )}
                {m.learned && (
                  <div className="msg-meta">
                    <span className="chip-memo">⚡ Mémoire interne — réponse instantanée</span>
                  </div>
                )}
                {m.score && (
                  <div className="msg-meta">
                    <span
                      className={`chip-score ${
                        m.score.value >= 75 ? "good" : m.score.value >= 50 ? "mid" : "bad"
                      }`}
                      title={m.score.avis}
                    >
                      ◎ Score {m.score.value}/100 — {m.score.avis}
                    </span>
                  </div>
                )}
                {m.routedFrom && (
                  <div className="msg-meta">↳ routé : {m.routedFrom}</div>
                )}
                {m.sources && m.sources.length > 0 && (
                  <div className="msg-meta">
                    📄 sources :{" "}
                    {m.sources.map((s, si) => (
                      <span key={si}>
                        {s.url ? (
                          <a href={s.url} target="_blank" rel="noreferrer" title="Ouvrir le document">
                            {s.filename}
                          </a>
                        ) : (
                          s.filename
                        )}
                        {si < m.sources!.length - 1 ? ", " : ""}
                      </span>
                    ))}
                  </div>
                )}
                {m.role === "assistant" && m.content && !streaming && (
                  <div className="msg-meta">
                    <button className="tts-btn" title="Lire à voix haute" onClick={() => speak(m.content)}>
                      <SpeakerIcon />
                    </button>
                    <button
                      className="tts-btn"
                      title="Copier le message"
                      onClick={() => copyMessage(m.content)}
                    >
                      ⧉
                    </button>
                    {i === messages.length - 1 && messages.some((x) => x.role === "user") && (
                      <button className="tts-btn" title="Régénérer la réponse" onClick={regenerate}>
                        ↻
                      </button>
                    )}
                    {m.model && <span>{m.model}</span>}
                    {m.stats?.completion_tokens ? (
                      <span>
                        {m.stats.completion_tokens} tok
                        {m.stats.tokens_per_second ? ` · ${m.stats.tokens_per_second} tok/s` : ""}
                      </span>
                    ) : null}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-zone">
        {pendingImages.length > 0 && (
          <div className="image-previews">
            {pendingImages.map((p, i) => (
              <div key={i} className="image-thumb">
                <img src={`data:image/jpeg;base64,${p.base64}`} alt={p.name} />
                <button title="Retirer" onClick={() => setPendingImages((prev) => prev.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
          </div>
        )}
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            attachFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          className="icon-btn"
          title="Joindre une image (vision)"
          disabled={streaming}
          onClick={() => fileRef.current?.click()}
        >
          📎
        </button>
        <button
          className={`icon-btn ${recording ? "recording" : ""}`}
          title={recording ? "Arrêter l'enregistrement vocal" : "Agent vocal instantané (parlez)"}
          disabled={streaming && !recording}
          onClick={() => void toggleRecord()}
          style={recording ? { color: "#e5484d", transform: "scale(1.15)" } : undefined}
        >
          🎤
        </button>
        <textarea
          placeholder={
            streaming
              ? "Génération en cours…"
              : "Écrivez votre message… (Entrée pour envoyer)"
          }
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          onPaste={(e) => {
            const items = Array.from(e.clipboardData?.items ?? []);
            const imgs = items.filter((it) => it.type.startsWith("image/"));
            if (imgs.length > 0) {
              e.preventDefault();
              const dt = new DataTransfer();
              imgs.forEach((it) => dt.items.add(it.getAsFile() as File));
              attachFiles(dt.files);
            }
          }}
          rows={2}
        />
        <button className="primary" style={{ height: 46 }} disabled={streaming || (!input.trim() && pendingImages.length === 0)} onClick={() => void send()}>
          Envoyer
        </button>
      </div>
    </div>
  );
}
