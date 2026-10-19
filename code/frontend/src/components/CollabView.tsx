import { useCallback, useEffect, useRef, useState } from "react";
import type { ModelEntry, SSEEvent } from "../lib/api";
import { getJSON, streamSSE } from "../lib/api";

interface WorkflowDef {
  id: string;
  name: string;
  description: string;
  stages: number;
}

interface StageState {
  agent: string;
  label: string;
  text: string;
  done: boolean;
}

export default function CollabView({
  models,
  toast,
}: {
  models: ModelEntry[];
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
}) {
  const [workflows, setWorkflows] = useState<WorkflowDef[]>([]);
  const [workflowId, setWorkflowId] = useState("brainstorm");
  const [model, setModel] = useState("auto");
  const [task, setTask] = useState("");
  const [running, setRunning] = useState(false);
  const [stages, setStages] = useState<StageState[]>([]);
  const [finalResult, setFinalResult] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getJSON<{ workflows: WorkflowDef[] }>("/api/collab/workflows")
      .then((d) => setWorkflows(d.workflows))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [stages, finalResult]);

  const run = useCallback(async () => {
    if (!task.trim() || running) return;
    setRunning(true);
    setStages([]);
    setFinalResult("");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamSSE(
        "/api/collab/run",
        { task: task.trim(), workflow: workflowId, model: model || "auto", temperature: 0.7 },
        (event: SSEEvent) => {
          if (event.type === "stage") {
            setStages((prev) => [
              ...prev,
              { agent: event.agent as string, label: event.label as string, text: "", done: false },
            ]);
          } else if (event.type === "stage_token") {
            setStages((prev) => {
              const copy = [...prev];
              const last = copy[copy.length - 1];
              if (last && last.agent === event.agent) last.text += event.content as string;
              return [...copy];
            });
          } else if (event.type === "stage_done") {
            setStages((prev) =>
              prev.map((s) => (s.agent === event.agent ? { ...s, done: true } : s))
            );
          } else if (event.type === "done") {
            setFinalResult(String(event.result ?? ""));
          } else if (event.type === "error") {
            throw new Error(String(event.error));
          }
        },
        controller.signal
      );
    } catch (err) {
      if ((err as Error).name !== "AbortError") toast(`Atelier : ${err}`, "error");
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }, [task, running, workflowId, model, toast]);

  return (
    <div className="collab-container">
      <div className="view-header">
        <h2>Ateliers multi-agents</h2>
        <select value={workflowId} onChange={(e) => setWorkflowId(e.target.value)}>
          {workflows.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name} ({w.stages} étapes)
            </option>
          ))}
        </select>
        <select value={model} onChange={(e) => setModel(e.target.value)} style={{ minWidth: 180 }}>
          <option value="auto">Auto</option>
          {models.filter((m) => m.installed).map((m) => (
            <option key={m.tag} value={m.tag}>
              {m.name}
            </option>
          ))}
        </select>
        {running && (
          <button onClick={() => abortRef.current?.abort()} className="danger">
            Arrêter
          </button>
        )}
      </div>

      <p className="collab-desc">
        {workflows.find((w) => w.id === workflowId)?.description}
      </p>

      <div className="chat-input-zone" style={{ borderTop: "none" }}>
        <textarea
          placeholder="Décrivez la tâche à traiter par l'équipe d'agents…"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void run();
            }
          }}
          rows={2}
        />
        <button className="primary" style={{ height: 46 }} disabled={running || !task.trim()} onClick={() => void run()}>
          Lancer
        </button>
      </div>

      <div className="messages collab-messages">
        {stages.map((s, i) => (
          <div key={`${s.agent}-${i}`} className={`stage-card ${s.done ? "done" : ""}`}>
            <div className="stage-title">
              <span className="stage-num">{i + 1}</span> {s.label}
              {!s.done && <span className="cursor-blink" />}
            </div>
            <pre className="stage-text">{s.text}</pre>
          </div>
        ))}
        {finalResult && (
          <div className="msg assistant final-result">
            <strong>Résultat final</strong>
            <pre>{finalResult}</pre>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
