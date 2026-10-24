export interface ModelRuntime {
  total_bytes: number;
  vram_bytes: number;
  cpu_bytes_estimated: number;
  gpu_memory_share_pct: number | null;
  context_length: number | null;
  runner: string | null;
}

export interface ModelEntry {
  id: string;
  name: string;
  tag: string;
  family: string;
  capabilities: string[];
  size_label: string;
  quantization: string;
  vram_gb: number;
  context: number;
  description: string;
  source: string;
  installed: boolean;
  loaded: boolean;
  runtime?: ModelRuntime | null;
  size_bytes: number;
  parameter_size: string;
}

export interface Conversation {
  id: number;
  title: string;
  model: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
}

export interface Message {
  id: number;
  conversation_id: number;
  role: "user" | "assistant" | "system";
  content: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  duration_ms: number;
  created_at: string;
}

export interface ChatStats {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_duration_ms?: number;
  tokens_per_second?: number;
}

export interface SystemInfo {
  app: { name: string; version: string };
  uptime_seconds: number;
  cpu: { percent: number; cores_physical: number; cores_logical: number };
  ram: { used_gb: number; total_gb: number; percent: number };
  disks: { mount: string; total_gb: number; free_gb: number; used_percent: number }[];
  gpus: {
    name: string;
    vram_used_mb: number;
    vram_total_mb: number;
    utilization_percent: number;
    temperature_c: number;
  }[];
  ollama: { available: boolean; version: string; host: string };
  models_dir: string;
}

export interface Voice {
  id: string;
  name: string;
  languages: string[];
  gender?: "F" | "M";
}

export interface MemoryItem {
  id: number;
  content: string;
  tag: string;
  created_at: string;
}

export interface DocumentItem {
  id: number;
  filename: string;
  size_bytes: number;
  chunk_count: number;
  created_at: string;
  category?: string | null;
  description?: string | null;
}

export interface SearchResult {
  document_id: number;
  filename: string;
  position: number;
  content: string;
  score: number;
}

export interface AgentDef {
  id: string;
  name: string;
  description: string;
  tools: string[];
  rag: boolean;
}

export interface BenchmarkSummary {
  tag: string;
  score_percent: number;
  avg_tokens_per_second: number;
  avg_latency_ms: number;
}

export interface ToolActivity {
  name: string;
  denied: boolean;
  preview: string;
}

export type SSEEvent = Record<string, unknown> & { type: string };

export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (event: SSEEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status} ${text.slice(0, 200)}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      for (const line of part.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {
            /* ignore malformed */
          }
        }
      }
    }
  }
}

export async function getJSON<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function sendJSON<T>(path: string, method: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

export async function postVoiceSSE(
  audioBlob: Blob,
  conversationId: number | null,
  opts: {
    model?: string;
    temperature?: number;
    tts?: boolean;
    onEvent: (event: SSEEvent) => void;
    signal?: AbortSignal;
  }
): Promise<void> {
  const form = new FormData();
  form.append("file", audioBlob, "voice.webm");
  if (conversationId != null) form.append("conversation_id", String(conversationId));
  form.append("model", opts.model ?? "auto");
  form.append("temperature", String(opts.temperature ?? 0.3));
  form.append("tts_output", opts.tts ? "true" : "false");
  const resp = await fetch("/api/voice/chat", {
    method: "POST",
    body: form,
    signal: opts.signal,
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status} ${text.slice(0, 200)}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      for (const line of part.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            opts.onEvent(JSON.parse(line.slice(6)));
          } catch {
            /* ignore malformed */
          }
        }
      }
    }
  }
}

export interface ProviderCredit {
  id: string;
  nom: string;
  configue: boolean;
  modele: string;
  tokens_in: number;
  tokens_out: number;
  requests: number;
  updated_at: string;
  credit: { total: number; utilise: number; restant: number } | null;
  quota_err: string | null;
}

export interface DiscoveredModel {
  name: string;
  tag: string;
}

export interface NeoFile {
  name: string;
  path: string;
  size_bytes: number;
  modified: number;
}

export async function transcribeAudio(audioBlob: Blob): Promise<{
  text: string;
  language: string;
  duration: number;
}> {
  const form = new FormData();
  form.append("file", audioBlob, "voice.webm");
  const resp = await fetch("/api/voice/transcribe", { method: "POST", body: form });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status} ${text.slice(0, 200)}`);
  }
  return resp.json();
}
