import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, sendJSON } from "../lib/api";

interface Props {
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
}

const CATEGORIES = ["programmation", "ia", "impression3d", "bricolage", "autre"];

interface HardwareInfo {
  gpu_name: string;
  vram_gb: number;
  tier: string;
}

interface DatasetStats {
  sft_count: number;
  rlvr_count: number;
  total: number;
}

interface ModelStatus {
  gguf_exists: boolean;
  gguf_path: string;
  gguf_size_gb: number;
  ollama_imported: boolean;
  ollama_tag: string;
}

interface DatasetExample {
  index: number;
  category: string;
  user_message: string;
  assistant_message: string;
}

interface TrainStatus {
  running: boolean;
  status: string;
  progress: number;
  epoch: number;
  total_epochs: number;
  loss: number;
  elapsed_s: number;
}

interface ConvertStatus {
  running: boolean;
  status: string;
  output_path: string;
}

interface ImportStatus {
  running: boolean;
  status: string;
  tag: string;
}

export default function FineTuneView({ toast }: Props) {
  const [hardware, setHardware] = useState<HardwareInfo | null>(null);
  const [datasetStats, setDatasetStats] = useState<DatasetStats | null>(null);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [examples, setExamples] = useState<DatasetExample[]>([]);

  const [newCategory, setNewCategory] = useState("programmation");
  const [newUserMsg, setNewUserMsg] = useState("");
  const [newAssistantMsg, setNewAssistantMsg] = useState("");
  const [addingExample, setAddingExample] = useState(false);

  const [rlvrRunning, setRlvrRunning] = useState(false);
  const [rlvrCount, setRlvrCount] = useState<number | null>(null);

  const [trainStatus, setTrainStatus] = useState<TrainStatus | null>(null);
  const [training, setTraining] = useState(false);

  const [converting, setConverting] = useState(false);
  const [convertStatus, setConvertStatus] = useState<ConvertStatus | null>(null);
  const [importing, setImporting] = useState(false);
  const [importStatus, setImportStatus] = useState<ImportStatus | null>(null);

  const rlvrPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const trainPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshStatus = useCallback(() => {
    getJSON<{ hardware: HardwareInfo; dataset: DatasetStats; model: ModelStatus }>("/api/finetune/status")
      .then((d) => {
        setHardware(d.hardware);
        setDatasetStats(d.dataset);
        setModelStatus(d.model);
      })
      .catch(() => undefined);
  }, []);

  const refreshExamples = useCallback(() => {
    getJSON<{ examples: DatasetExample[] }>("/api/finetune/examples")
      .then((d) => setExamples(d.examples))
      .catch(() => undefined);
  }, []);

  const refreshTrainStatus = useCallback(() => {
    getJSON<TrainStatus>("/api/finetune/train/status")
      .then((d) => {
        setTrainStatus(d);
        if (!d.running && trainPollRef.current) {
          clearInterval(trainPollRef.current);
          trainPollRef.current = null;
          setTraining(false);
          if (d.status === "finished") toast("Entraînement terminé", "success");
          else if (d.status === "error") toast("Entraînement en erreur", "error");
        }
      })
      .catch(() => undefined);
  }, [toast]);

  useEffect(() => {
    refreshStatus();
    refreshExamples();
    refreshTrainStatus();
    return () => {
      if (rlvrPollRef.current) clearInterval(rlvrPollRef.current);
      if (trainPollRef.current) clearInterval(trainPollRef.current);
    };
  }, [refreshStatus, refreshExamples, refreshTrainStatus]);

  const addExample = useCallback(async () => {
    if (!newUserMsg.trim() || !newAssistantMsg.trim()) return;
    setAddingExample(true);
    try {
      await sendJSON("/api/finetune/examples", "POST", {
        category: newCategory,
        user_message: newUserMsg.trim(),
        assistant_message: newAssistantMsg.trim(),
      });
      setNewUserMsg("");
      setNewAssistantMsg("");
      toast("Exemple ajouté", "success");
      refreshExamples();
      refreshStatus();
    } catch {
      toast("Échec de l'ajout", "error");
    } finally {
      setAddingExample(false);
    }
  }, [newCategory, newUserMsg, newAssistantMsg, toast, refreshExamples, refreshStatus]);

  const deleteExample = useCallback(
    async (index: number) => {
      try {
        await sendJSON(`/api/finetune/examples/${index}`, "DELETE");
        refreshExamples();
        refreshStatus();
      } catch {
        toast("Suppression impossible", "error");
      }
    },
    [toast, refreshExamples, refreshStatus]
  );

  const mergeDatasets = useCallback(async () => {
    try {
      await sendJSON("/api/finetune/merge", "POST");
      toast("Datasets fusionnés", "success");
      refreshStatus();
    } catch {
      toast("Échec de la fusion", "error");
    }
  }, [toast, refreshStatus]);

  const runRlvr = useCallback(async () => {
    setRlvrRunning(true);
    try {
      await sendJSON("/api/finetune/rlvr/run", "POST");
      toast("RLVR lancé", "success");
      rlvrPollRef.current = setInterval(() => {
        getJSON<{ validated: number; running: boolean }>("/api/finetune/rlvr/status")
          .then((d) => {
            setRlvrCount(d.validated);
            if (!d.running && rlvrPollRef.current) {
              clearInterval(rlvrPollRef.current);
              rlvrPollRef.current = null;
              setRlvrRunning(false);
              refreshExamples();
              refreshStatus();
              toast("RLVR terminé", "success");
            }
          })
          .catch(() => undefined);
      }, 5000);
    } catch {
      toast("Échec du lancement RLVR", "error");
      setRlvrRunning(false);
    }
  }, [toast, refreshExamples, refreshStatus]);

  const runTrain = useCallback(async () => {
    setTraining(true);
    setTrainStatus(null);
    try {
      await sendJSON("/api/finetune/train", "POST");
      toast("Entraînement lancé", "success");
      trainPollRef.current = setInterval(refreshTrainStatus, 10000);
    } catch {
      toast("Échec du lancement", "error");
      setTraining(false);
    }
  }, [toast, refreshTrainStatus]);

  const runConvert = useCallback(async () => {
    setConverting(true);
    try {
      const r = await sendJSON<{ status: string; output_path: string }>("/api/finetune/convert", "POST");
      setConvertStatus({ running: false, status: r.status, output_path: r.output_path });
      toast("Conversion GGUF terminée", "success");
      refreshStatus();
    } catch {
      setConvertStatus({ running: false, status: "error", output_path: "" });
      toast("Échec de la conversion", "error");
    } finally {
      setConverting(false);
    }
  }, [toast, refreshStatus]);

  const runImportOllama = useCallback(async () => {
    setImporting(true);
    try {
      const r = await sendJSON<{ status: string; tag: string }>("/api/finetune/import-ollama", "POST");
      setImportStatus({ running: false, status: r.status, tag: r.tag });
      toast(`Modèle ${r.tag} importé dans Ollama`, "success");
      refreshStatus();
    } catch {
      setImportStatus({ running: false, status: "error", tag: "" });
      toast("Échec de l'import Ollama", "error");
    } finally {
      setImporting(false);
    }
  }, [toast, refreshStatus]);

  const badge = (ok: boolean, labelOk: string, labelBad: string) => (
    <span className={`chip ${ok ? "green" : ""}`} style={{ borderColor: ok ? "var(--green)" : "var(--red)", color: ok ? "var(--green)" : "var(--red)" }}>
      {ok ? labelOk : labelBad}
    </span>
  );

  return (
    <>
      <div className="view-header">
        <h2>Fine-Tuning</h2>
        <span style={{ flex: 1 }} />
        <button onClick={() => { refreshStatus(); refreshExamples(); refreshTrainStatus(); }}>Rafraîchir</button>
      </div>
      <div className="content-scroll">

        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>État du système</h3>
          {hardware ? (
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
              <div className="kv" style={{ border: "none", flex: "1 1 160px" }}>
                <span className="k">GPU</span>
                <span style={{ fontFamily: "Consolas, monospace", fontSize: 12 }}>{hardware.gpu_name}</span>
              </div>
              <div className="kv" style={{ border: "none", flex: "0 0 auto" }}>
                <span className="k">VRAM</span>
                <span>{hardware.vram_gb} Go</span>
              </div>
              <div className="kv" style={{ border: "none", flex: "0 0 auto" }}>
                <span className="k">Tier</span>
                <span className={`chip ${hardware.tier === "high" ? "green" : ""}`} style={{ borderColor: hardware.tier === "high" ? "var(--green)" : hardware.tier === "medium" ? "var(--orange)" : "var(--red)", color: hardware.tier === "high" ? "var(--green)" : hardware.tier === "medium" ? "var(--orange)" : "var(--red)" }}>
                  {hardware.tier}
                </span>
              </div>
            </div>
          ) : (
            <div style={{ color: "var(--text-dim)" }}>Chargement…</div>
          )}
          {datasetStats && (
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
              <div className="kv" style={{ border: "none", flex: "0 0 auto" }}>
                <span className="k">SFT</span>
                <span>{datasetStats.sft_count}</span>
              </div>
              <div className="kv" style={{ border: "none", flex: "0 0 auto" }}>
                <span className="k">RLVR</span>
                <span>{datasetStats.rlvr_count}</span>
              </div>
              <div className="kv" style={{ border: "none", flex: "0 0 auto" }}>
                <span className="k">Total</span>
                <span>{datasetStats.total}</span>
              </div>
            </div>
          )}
          {modelStatus && (
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              {badge(modelStatus.gguf_exists, "GGUF prêt", "GGUF manquant")}
              {modelStatus.gguf_exists && (
                <span className="chip">{modelStatus.gguf_size_gb.toFixed(1)} Go</span>
              )}
              {badge(modelStatus.ollama_imported, `Ollama: ${modelStatus.ollama_tag}`, "Non importé")}
            </div>
          )}
        </div>

        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Dataset Manager</h3>
          <div style={{ overflowX: "auto", marginBottom: 14 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead>
                <tr style={{ color: "var(--text-faint)", borderBottom: "1px solid var(--border)" }}>
                  <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>#</th>
                  <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Catégorie</th>
                  <th style={{ textAlign: "left", padding: "6px 8px", fontWeight: 500 }}>Aperçu</th>
                  <th style={{ padding: "6px 8px", fontWeight: 500 }}></th>
                </tr>
              </thead>
              <tbody>
                {examples.length === 0 && (
                  <tr>
                    <td colSpan={4} style={{ color: "var(--text-faint)", padding: 12, textAlign: "center" }}>
                      Aucun exemple
                    </td>
                  </tr>
                )}
                {examples.map((ex) => (
                  <tr key={ex.index} style={{ borderBottom: "1px solid rgba(35,47,64,0.4)" }}>
                    <td style={{ padding: "6px 8px", fontFamily: "Consolas, monospace", color: "var(--text-faint)" }}>{ex.index}</td>
                    <td style={{ padding: "6px 8px" }}>
                      <span className="chip">{ex.category}</span>
                    </td>
                    <td style={{ padding: "6px 8px", color: "var(--text-dim)", maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {ex.user_message.slice(0, 80)}
                      {ex.user_message.length > 80 ? "…" : ""}
                    </td>
                    <td style={{ padding: "6px 8px", textAlign: "center" }}>
                      <button className="danger" style={{ padding: "3px 8px", fontSize: 11 }} onClick={() => void deleteExample(ex.index)}>
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10, color: "var(--accent)" }}>Ajouter un exemple</div>
            <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
              <select value={newCategory} onChange={(e) => setNewCategory(e.target.value)} style={{ width: 150 }}>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <button className="primary" disabled={addingExample || !newUserMsg.trim() || !newAssistantMsg.trim()} onClick={() => void addExample()}>
                {addingExample ? "…" : "Ajouter"}
              </button>
              <button onClick={() => void mergeDatasets()}>Fusionner SFT + RLVR</button>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <textarea
                placeholder="Message utilisateur…"
                value={newUserMsg}
                onChange={(e) => setNewUserMsg(e.target.value)}
                rows={3}
                style={{ flex: 1, resize: "vertical" }}
              />
              <textarea
                placeholder="Réponse assistant…"
                value={newAssistantMsg}
                onChange={(e) => setNewAssistantMsg(e.target.value)}
                rows={3}
                style={{ flex: 1, resize: "vertical" }}
              />
            </div>
          </div>
        </div>

        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>RLVR — Reinforcement Learning from Verifiable Rewards</h3>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
            <button className="primary" disabled={rlvrRunning} onClick={() => void runRlvr()}>
              {rlvrRunning ? "RLVR en cours…" : "Lancer RLVR"}
            </button>
            {rlvrCount !== null && (
              <span style={{ fontSize: 12.5, color: "var(--text-dim)" }}>
                {rlvrCount} exemples validés
              </span>
            )}
          </div>
          {rlvrRunning && (
            <div style={{ fontSize: 12, color: "var(--orange)" }}>
              <span className="cursor-blink" /> Vérification automatique toutes les 5 s
            </div>
          )}
        </div>

        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Entraînement LoRA</h3>
          {trainStatus ? (
            <>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 10 }}>
                <div className="kv" style={{ border: "none", flex: "0 0 auto" }}>
                  <span className="k">Base</span>
                  <span style={{ fontFamily: "Consolas, monospace", fontSize: 12 }}>{trainStatus.running ? "…" : (modelStatus?.ollama_tag || "—")}</span>
                </div>
                <div className="kv" style={{ border: "none", flex: "0 0 auto" }}>
                  <span className="k">Epochs</span>
                  <span>{trainStatus.total_epochs}</span>
                </div>
                <div className="kv" style={{ border: "none", flex: "0 0 auto" }}>
                  <span className="k">LoRA rank</span>
                  <span>16</span>
                </div>
              </div>
              {trainStatus.running && (
                <div style={{ marginBottom: 10 }}>
                  <div className="progress-track" style={{ marginBottom: 4 }}>
                    <div className="progress-fill" style={{ width: `${(trainStatus.progress * 100).toFixed(0)}%` }} />
                  </div>
                  <div className="progress-label">
                    <span>
                      Epoch {trainStatus.epoch}/{trainStatus.total_epochs}
                      {trainStatus.loss > 0 && ` · loss ${trainStatus.loss.toFixed(4)}`}
                    </span>
                    <span>
                      {(trainStatus.progress * 100).toFixed(0)}%
                      {trainStatus.elapsed_s > 0 && ` · ${Math.round(trainStatus.elapsed_s)}s`}
                    </span>
                  </div>
                </div>
              )}
              {trainStatus.status === "finished" && (
                <div style={{ fontSize: 12.5, color: "var(--green)" }}>Entraînement terminé</div>
              )}
              {trainStatus.status === "error" && (
                <div style={{ fontSize: 12.5, color: "var(--red)" }}>Erreur lors de l'entraînement</div>
              )}
            </>
          ) : (
            <div style={{ color: "var(--text-faint)", fontSize: 12.5, marginBottom: 10 }}>Aucun entraînement en cours</div>
          )}
          <button className="primary" disabled={training || (trainStatus?.running ?? false)} onClick={() => void runTrain()}>
            {training || trainStatus?.running ? "Entraînement en cours…" : "Lancer l'entraînement"}
          </button>
        </div>

        <div className="panel">
          <h3>Conversion & Import Ollama</h3>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
            <button disabled={converting} onClick={() => void runConvert()}>
              {converting ? "Conversion…" : "Convertir en GGUF"}
            </button>
            <button disabled={importing} onClick={() => void runImportOllama()}>
              {importing ? "Import…" : "Importer dans Ollama"}
            </button>
          </div>
          {convertStatus && (
            <div className="kv">
              <span className="k">GGUF</span>
              <span style={{ fontFamily: "Consolas, monospace", fontSize: 12, color: convertStatus.status === "error" ? "var(--red)" : "var(--green)" }}>
                {convertStatus.status === "error" ? "échec" : convertStatus.output_path || convertStatus.status}
              </span>
            </div>
          )}
          {importStatus && (
            <div className="kv">
              <span className="k">Ollama</span>
              <span style={{ fontFamily: "Consolas, monospace", fontSize: 12, color: importStatus.status === "error" ? "var(--red)" : "var(--green)" }}>
                {importStatus.status === "error" ? "échec" : importStatus.tag || importStatus.status}
              </span>
            </div>
          )}
        </div>

      </div>
    </>
  );
}
