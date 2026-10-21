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
}export default function FineTuneView({ toast }: Props) {
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
  const [conseilEnCours, setConseilEnCours] = useState(false);
  const [conseilIdees, setConseilIdees] = useState<{ id: number; titre: string; description: string; faisabilite: number; gain: number; statut: string }[]>([]);
  const [veilleEnCours, setVeilleEnCours] = useState(false);
  const [veilleItems, setVeilleItems] = useState<{ source: string; nom: string; url: string; etoiles: number; resume: string; gain: string }[]>([]);
  const convertPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const importPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  const startConvertPoll = useCallback(() => {
    if (convertPollRef.current) return;
    convertPollRef.current = setInterval(() => {
      getJSON<{ running: boolean; status: string; tag: string; output_path: string }>("/api/finetune/convert/status")
        .then((d) => {
          setConvertStatus({ running: d.running, status: d.status, output_path: d.output_path });
          if (!d.running && convertPollRef.current) {
            clearInterval(convertPollRef.current);
            convertPollRef.current = null;
            setConverting(false);
            if (d.status === "finished") toast("Conversion GGUF terminée", "success");
            else if (d.status === "error") toast("Échec de la conversion", "error");
            refreshStatus();
          }
        })
        .catch(() => undefined);
    }, 5000);
  }, [toast, refreshStatus]);

  const startImportPoll = useCallback(() => {
    if (importPollRef.current) return;
    importPollRef.current = setInterval(() => {
      getJSON<{ running: boolean; status: string; tag: string }>("/api/finetune/import-ollama/status")
        .then((d) => {
          setImportStatus({ running: d.running, status: d.status, tag: d.tag });
          if (!d.running && importPollRef.current) {
            clearInterval(importPollRef.current);
            importPollRef.current = null;
            setImporting(false);
            if (d.status === "finished") toast(`Modèle ${d.tag || "?"} importé dans Ollama`, "success");
            else if (d.status === "error") toast("Échec de l'import Ollama", "error");
            refreshStatus();
          }
        })
        .catch(() => undefined);
    }, 5000);
  }, [toast, refreshStatus]);

  useEffect(() => {
    refreshStatus();
    refreshExamples();
    refreshTrainStatus();
    getJSON<{ running: boolean; idees: number }>("/api/conseil/status")
      .then((d) => {
        setConseilEnCours(d.running);
        if (d.idees > 0 || d.running) {
          getJSON<{ idees: typeof conseilIdees }>("/api/conseil/idees")
            .then((r) => setConseilIdees(r.idees ?? []))
            .catch(() => undefined);
        }
      })
      .catch(() => undefined);
    getJSON<{ running: boolean; analyses: number }>("/api/veille/status")
      .then((d) => {
        setVeilleEnCours(d.running);
        if (d.analyses > 0 || d.running) {
          getJSON<{ veille: typeof veilleItems }>("/api/veille")
            .then((r) => setVeilleItems(r.veille ?? []))
            .catch(() => undefined);
        }
      })
      .catch(() => undefined);
    getJSON<{ running: boolean }>("/api/finetune/convert/status")
      .then((d) => {
        if (d.running) {
          setConverting(true);
          startConvertPoll();
        }
      })
      .catch(() => undefined);
    getJSON<{ running: boolean }>("/api/finetune/import-ollama/status")
      .then((d) => {
        if (d.running) {
          setImporting(true);
          startImportPoll();
        }
      })
      .catch(() => undefined);
    return () => {
      if (rlvrPollRef.current) clearInterval(rlvrPollRef.current);
      if (trainPollRef.current) clearInterval(trainPollRef.current);
      if (convertPollRef.current) clearInterval(convertPollRef.current);
      if (importPollRef.current) clearInterval(importPollRef.current);
    };
  }, [refreshStatus, refreshExamples, refreshTrainStatus, startConvertPoll, startImportPoll]);

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

  const lancerConseil = useCallback(async () => {
    setConseilEnCours(true);
    try {
      await sendJSON("/api/conseil/lancer", "POST", {});
      toast("Conseil lancé — 100 idées en génération + validation…", "info");
      const poll = setInterval(() => {
        getJSON<{ running: boolean; idees: number }>("/api/conseil/status")
          .then((d) => {
            if (d.idees > 0) {
              getJSON<{ idees: typeof conseilIdees }>("/api/conseil/idees")
                .then((r) => setConseilIdees(r.idees ?? []))
                .catch(() => undefined);
            }
            if (!d.running) {
              clearInterval(poll);
              setConseilEnCours(false);
              toast("Conseil terminé", "success");
            }
          })
          .catch(() => undefined);
      }, 15000);
    } catch {
      setConseilEnCours(false);
      toast("Échec du lancement du conseil", "error");
    }
  }, [toast]);

  const lancerIdee = useCallback(async (id: number) => {
    try {
      await sendJSON(`/api/conseil/idee/${id}/lancer`, "POST", {});
      toast(`Idée #${id} lancée — le dev agent travaille`, "info");
      getJSON<{ idees: typeof conseilIdees }>("/api/conseil/idees")
        .then((r) => setConseilIdees(r.idees ?? []))
        .catch(() => undefined);
    } catch {
      toast("Échec du lancement", "error");
    }
  }, [toast]);

  const lancerVeille = useCallback(async () => {
    setVeilleEnCours(true);
    try {
      await sendJSON("/api/veille/lancer", "POST", {});
      toast("Veille lancée — GitHub + Hugging Face…", "info");
      const poll = setInterval(() => {
        getJSON<{ running: boolean; analyses: number }>("/api/veille/status")
          .then((d) => {
            if (d.analyses > 0) {
              getJSON<{ veille: typeof veilleItems }>("/api/veille")
                .then((r) => setVeilleItems(r.veille ?? []))
                .catch(() => undefined);
            }
            if (!d.running) {
              clearInterval(poll);
              setVeilleEnCours(false);
              toast("Veille terminée", "success");
            }
          })
          .catch(() => undefined);
      }, 20000);
    } catch {
      setVeilleEnCours(false);
      toast("Échec du lancement de la veille", "error");
    }
  }, [toast]);

  const runTrain = useCallback(async () => {
    setTraining(true);
    setTrainStatus(null);
    try {
      await sendJSON("/api/finetune/train", "POST", {});
      toast("Entraînement lancé", "success");
      trainPollRef.current = setInterval(refreshTrainStatus, 10000);
    } catch {
      toast("Échec du lancement", "error");
      setTraining(false);
    }
  }, [toast, refreshTrainStatus]);

  const runConvert = useCallback(async () => {
    setConverting(true);
    setConvertStatus(null);
    try {
      await sendJSON("/api/finetune/convert", "POST", {});
      toast("Conversion GGUF lancée", "info");
      startConvertPoll();
    } catch {
      setConverting(false);
      setConvertStatus({ running: false, status: "error", output_path: "" });
      toast("Échec du lancement de la conversion", "error");
    }
  }, [toast, startConvertPoll]);

  const runImportOllama = useCallback(async () => {
    setImporting(true);
    setImportStatus(null);
    try {
      await sendJSON("/api/finetune/import-ollama", "POST", {});
      toast("Import Ollama lancé", "info");
      startImportPoll();
    } catch {
      setImporting(false);
      setImportStatus({ running: false, status: "error", tag: "" });
      toast("Échec du lancement de l'import", "error");
    }
  }, [toast, startImportPoll]);

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
                <span
                  className={`chip ${hardware.tier === "T3" ? "green" : ""}`}
                  style={{
                    borderColor: hardware.tier === "T3" ? "var(--green)" : hardware.tier === "T2" ? "var(--orange)" : "var(--red)",
                    color: hardware.tier === "T3" ? "var(--green)" : hardware.tier === "T2" ? "var(--orange)" : "var(--red)",
                  }}
                >
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
          {trainStatus && trainStatus.status !== "idle" && trainStatus.status !== "stopped" ? (
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
              <span
                style={{
                  fontFamily: "Consolas, monospace",
                  fontSize: 12,
                  color:
                    convertStatus.status === "error"
                      ? "var(--red)"
                      : convertStatus.running
                      ? "var(--orange)"
                      : "var(--green)",
                }}
              >
                {convertStatus.status === "error"
                  ? "échec"
                  : convertStatus.running
                  ? "conversion en cours…"
                  : convertStatus.output_path || convertStatus.status}
              </span>
            </div>
          )}
          {importStatus && (
            <div className="kv">
              <span className="k">Ollama</span>
              <span
                style={{
                  fontFamily: "Consolas, monospace",
                  fontSize: 12,
                  color:
                    importStatus.status === "error"
                      ? "var(--red)"
                      : importStatus.running
                      ? "var(--orange)"
                      : "var(--green)",
                }}
              >
                {importStatus.status === "error"
                  ? "échec"
                  : importStatus.running
                  ? "import en cours…"
                  : importStatus.tag || importStatus.status}
              </span>
            </div>
          )}
        </div>

        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Conseil — les 100 idées</h3>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
            <button
              className="primary"
              disabled={conseilEnCours}
              onClick={() => void lancerConseil()}
            >
              {conseilEnCours ? "Le conseil délibère…" : "Lancer le conseil"}
            </button>
            {conseilIdees.length > 0 && (
              <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                {conseilIdees.length} idées validées par le conseil
              </span>
            )}
          </div>
          {conseilIdees.length > 0 && (
            <div style={{ maxHeight: 400, overflowY: "auto" }}>
              {conseilIdees.map((idee) => (
                <div
                  key={idee.id}
                  className="model-card"
                  style={{ marginBottom: 8, borderColor:
                    idee.statut === "terminee" ? "var(--green)" :
                    idee.statut === "en_cours" ? "var(--orange)" : "var(--border)" }}
                >
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>
                    #{idee.id} · {idee.titre}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-dim)", marginBottom: 6 }}>
                    {idee.description}
                  </div>
                  <div className="model-specs" style={{ marginBottom: 6 }}>
                    <span className="chip">faisabilité {idee.faisabilite}/5</span>
                    <span className="chip">gain {idee.gain}/5</span>
                    <span className="chip">{idee.statut}</span>
                  </div>
                  {(idee.statut === "proposee" || idee.statut === "rejetee") && (
                    <button
                      className="primary"
                      style={{ padding: "4px 14px", fontSize: 12 }}
                      onClick={() => void lancerIdee(idee.id)}
                    >
                      Mettre en application
                    </button>
                  )}
                  {idee.statut === "en_cours" && (
                    <span style={{ fontSize: 12, color: "var(--orange)" }}>⏳ Implémentation en cours…</span>
                  )}
                  {idee.statut === "terminee" && (
                    <span style={{ fontSize: 12, color: "var(--green)" }}>✓ Terminée</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Veille — repos & modèles à la mode</h3>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
            <button
              className="primary"
              disabled={veilleEnCours}
              onClick={() => void lancerVeille()}
            >
              {veilleEnCours ? "Veille en cours…" : "Lancer la veille"}
            </button>
            {veilleItems.length > 0 && (
              <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                {veilleItems.length} analyses (GitHub + Hugging Face)
              </span>
            )}
          </div>
          {veilleItems.length > 0 && (
            <div style={{ maxHeight: 350, overflowY: "auto" }}>
              {veilleItems.map((v, i) => (
                <div key={i} className="model-card" style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    [{v.source === "github" ? "GitHub" : "Hugging Face"}]{" "}
                    <a href={v.url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)" }}>
                      {v.nom}
                    </a>
                    {v.etoiles > 0 && <span className="chip" style={{ marginLeft: 8 }}>⭐ {v.etoiles}</span>}
                  </div>
                  {v.resume && (
                    <div style={{ fontSize: 12, color: "var(--text)", marginTop: 4 }}>{v.resume}</div>
                  )}
                  {v.gain && (
                    <div style={{ fontSize: 12, color: "var(--accent)", marginTop: 4 }}>
                      Gain pour Néo : {v.gain}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

      </div>
    </>
  );
}

