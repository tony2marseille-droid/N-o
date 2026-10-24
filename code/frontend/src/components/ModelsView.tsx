import { useCallback, useEffect, useState } from "react";
import type { BenchmarkSummary, DiscoveredModel, ModelEntry } from "../lib/api";
import { getJSON, sendJSON, streamSSE } from "../lib/api";

interface Props {
  models: ModelEntry[];
  refreshModels: () => Promise<void>;
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
}

type ProgressState = { percent: number; status: string };

/* --- Tier estime depuis la taille du modele (consigne : filtre T1..T5) --- */
function paramB(m: ModelEntry): number {
  const s = (m.parameter_size || "").toUpperCase();
  const v = parseFloat(s) || 0;
  if (s.includes("M") && !s.includes("B")) return v / 1000;
  return v;
}

function tierDe(m: ModelEntry): string {
  const t = m.tag.toLowerCase();
  if (t.includes("nomic") || t.includes("embed")) return "Embed";
  const b = paramB(m);
  if (b >= 30) return "T5";
  if (b >= 15) return "T4";
  if (b >= 10) return "T3";
  if (b >= 5) return "T2";
  return "T1";
}

function formatSize(bytes: number): string {
  if (!bytes) return "—";
  return `${(bytes / 1e9).toFixed(1)} Go`;
}

export default function ModelsView({ models, refreshModels, toast }: Props) {
  const [progress, setProgress] = useState<Record<string, ProgressState>>({});
  const [busyTag, setBusyTag] = useState("");
  const [customTag, setCustomTag] = useState("");
  const [benchmarking, setBenchmarking] = useState(false);
  const [benchResults, setBenchResults] = useState<BenchmarkSummary[]>([]);
  const [benchCurrent, setBenchCurrent] = useState("");
  const [optionnels, setOptionnels] = useState<string[]>([]);
  const [tierFiltre, setTierFiltre] = useState("");
  const [decouverte, setDecouverte] = useState<DiscoveredModel[] | null>(null);
  const [decouvreEnCours, setDecouvreEnCours] = useState(false);

  useEffect(() => {
    getJSON<{ optionnels: string[] }>("/api/system/hardware/pulls")
      .then((d) => setOptionnels(d.optionnels || []))
      .catch(() => undefined);
  }, []);

  const estInstalle = (tag: string) =>
    models.some((m) => m.tag.toLowerCase() === tag.toLowerCase());

  // Consigne : recherche de nouveaux modeles disponibles (bibliotheque
  // publique Ollama) - bouton en haut de la fenetre Modeles.
  const decouvrir = useCallback(async () => {
    setDecouvreEnCours(true);
    try {
      const d = await sendJSON<{ nouveaux: DiscoveredModel[]; total: number }>(
        "/api/models/discover",
        "POST"
      );
      setDecouverte(d.nouveaux ?? []);
      toast(
        `${d.nouveaux?.length ?? 0} nouveaux modeles disponibles (sur ${d.total} scannes)`,
        "success"
      );
    } catch (err) {
      toast(`Recherche impossible : ${err}`, "error");
    } finally {
      setDecouvreEnCours(false);
    }
  }, [toast]);

  const installModel = useCallback(
    async (tag: string) => {
      setBusyTag(tag);
      setProgress((p) => ({ ...p, [tag]: { percent: 0, status: "Démarrage…" } }));
      try {
        await streamSSE("/api/models/install", { tag }, (event) => {
          if (event.type === "progress") {
            setProgress((p) => ({
              ...p,
              [tag]: {
                percent: event.percent as number,
                status:
                  (event.status as string) === "pulling manifest"
                    ? "Récupération du manifeste…"
                    : `Téléchargement ${(event.percent as number).toFixed(0)}%`,
              },
            }));
          } else if (event.type === "done") {
            setProgress((p) => ({ ...p, [tag]: { percent: 100, status: "Terminé" } }));
          } else if (event.type === "error") {
            throw new Error(String(event.error));
          }
        });
        await refreshModels();
        toast(`Modèle ${tag} installé`, "success");
      } catch (err) {
        toast(`Installation échouée : ${err}`, "error");
      } finally {
        setBusyTag("");
        setTimeout(() => setProgress((p) => omit(p, tag)), 1500);
      }
    },
    [refreshModels, toast]
  );

  const updateAll = useCallback(async () => {
    const installedTags = models.filter((m) => m.installed).map((m) => m.tag);
    if (installedTags.length === 0) return;
    let updated = 0;
    let failed = 0;
    for (const tag of installedTags) {
      setBusyTag(tag);
      setProgress((p) => ({ ...p, [tag]: { percent: 0, status: "Vérification…" } }));
      try {
        let confirmed = false;
        let serverError = "";
        await streamSSE("/api/models/update", { tags: [tag] }, (event) => {
          if (event.type === "model_done" && event.tag === tag) {
            confirmed = event.ok === true;
          } else if (event.type === "error") {
            serverError = String(event.error ?? "Erreur Ollama");
          }
        });
        if (!confirmed) {
          throw new Error(serverError || "Ollama n’a pas confirmé la mise à jour");
        }
        updated += 1;
        setProgress((p) => ({ ...p, [tag]: { percent: 100, status: "À jour" } }));
      } catch (err) {
        failed += 1;
        setProgress((p) => ({
          ...p,
          [tag]: { percent: 0, status: `Échec : ${String(err)}` },
        }));
      }
    }
    setBusyTag("");
    await refreshModels();
    toast(
      `Mises à jour : ${updated} réussie(s), ${failed} échec(s)`,
      failed > 0 ? "error" : "success"
    );
  }, [models, refreshModels, toast]);

  const loadModel = useCallback(
    async (tag: string) => {
      try {
        toast(`Chargement de ${tag} — Ollama répartit automatiquement VRAM/RAM…`);
        await sendJSON("/api/models/load", "POST", { tag });
        await refreshModels();
      } catch (err) {
        toast(`Chargement échoué : ${err}`, "error");
      }
    },
    [refreshModels, toast]
  );

  const unloadModel = useCallback(
    async (tag: string) => {
      try {
        await sendJSON("/api/models/unload", "POST", { tag });
        await refreshModels();
      } catch (err) {
        toast(`Déchargement échoué : ${err}`, "error");
      }
    },
    [refreshModels, toast]
  );

  const deleteModel = useCallback(
    async (tag: string) => {
      if (!window.confirm(`Supprimer définitivement ${tag} du disque ?`)) return;
      try {
        await sendJSON(`/api/models/${encodeURIComponent(tag)}`, "DELETE");
        await refreshModels();
        toast(`Modèle ${tag} supprimé`, "success");
      } catch (err) {
        toast(`Suppression échouée : ${err}`, "error");
      }
    },
    [refreshModels, toast]
  );

  const runBenchmark = useCallback(async () => {
    const installedTags = models
      .filter((m) => m.installed && !m.tag.includes("embed"))
      .map((m) => m.tag);
    if (installedTags.length === 0) return;
    setBenchmarking(true);
    setBenchResults([]);
    try {
      await streamSSE("/api/benchmark/run", { tags: installedTags }, (event) => {
        if (event.type === "model_start") {
          setBenchCurrent(event.tag as string);
        } else if (event.type === "model_done") {
          setBenchResults((prev) => [
            ...prev,
            {
              tag: event.tag as string,
              score_percent: event.score_percent as number,
              avg_tokens_per_second: event.avg_tokens_per_second as number,
              avg_latency_ms: event.avg_latency_ms as number,
            },
          ]);
        }
      });
      toast("Benchmark terminé", "success");
    } catch (err) {
      toast(`Benchmark échoué : ${err}`, "error");
    } finally {
      setBenchmarking(false);
      setBenchCurrent("");
    }
  }, [models, toast]);

  return (
    <>
      <div className="view-header">
        <h2>Modèles</h2>
        <span style={{ color: "var(--text-dim)", fontSize: 13 }}>
          {models.filter((m) => m.installed).length} installés
        </span>
      </div>
      <div className="content-scroll">
        {/* Consigne : filtre par tier + recherche de nouveaux modeles */}
        <div className="toolbar" style={{ marginBottom: 10 }}>
          <span style={{ color: "var(--text-dim)", fontSize: 12.5 }}>Tier :</span>
          <div className="tier-chips">
            {["", "T1", "T2", "T3", "T4", "T5", "Embed"].map((t) => (
              <button
                key={t || "tous"}
                className={`tier-chip ${tierFiltre === t ? "active" : ""}`}
                onClick={() => setTierFiltre(t)}
              >
                {t || "Tous"}
              </button>
            ))}
          </div>
          <span style={{ flex: 1 }} />
          <button
            className="primary"
            disabled={decouvreEnCours}
            onClick={() => void decouvrir()}
          >
            {decouvreEnCours ? "Recherche en cours..." : "Nouveaux modeles"}
          </button>
        </div>
        {decouverte !== null && (
          <div className="panel discover-panel" style={{ marginBottom: 14 }}>
            <h3>Nouveaux modeles disponibles ({decouverte.length})</h3>
            {decouverte.length === 0 ? (
              <div style={{ color: "var(--text-dim)", fontSize: 12.5 }}>
                Aucun nouveau modele trouve au-dela du catalogue local.
              </div>
            ) : (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {decouverte.map((m) => (
                  <button
                    key={m.tag}
                    className="disc-chip"
                    disabled={!!busyTag}
                    title={`Installer ${m.tag}`}
                    onClick={() => void installModel(m.tag)}
                  >
                    + {m.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {optionnels.length > 0 && (
          <div className="toolbar" style={{ flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
            <span style={{ color: "var(--text-dim)", fontSize: 12.5, width: "100%" }}>
              Optionnels recommandés pour cette machine (GLM, DeepSeek-R1, Phi…) :
            </span>
            {optionnels.map((tag) => {
              const installe = estInstalle(tag);
              return (
                <button
                  key={tag}
                  className={installe ? "" : "primary"}
                  disabled={installe || !!busyTag}
                  onClick={() => void installModel(tag)}
                  style={{ fontSize: 12, padding: "4px 10px", borderRadius: 14 }}
                >
                  {installe ? `✓ ${tag}` : busyTag === tag ? "…" : `+ ${tag}`}
                </button>
              );
            })}
          </div>
        )}
        <div className="toolbar">
          <input
            placeholder="Tag Ollama personnalisé (ex: llama3.2:3b)"
            value={customTag}
            onChange={(e) => setCustomTag(e.target.value)}
            style={{ width: 320 }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && customTag.trim()) {
                void installModel(customTag.trim());
                setCustomTag("");
              }
            }}
          />
          <button
            className="primary"
            disabled={!customTag.trim() || !!busyTag}
            onClick={() => {
              void installModel(customTag.trim());
              setCustomTag("");
            }}
          >
            Installer ce modèle
          </button>
          <span style={{ flex: 1 }} />
          <button disabled={!!busyTag} onClick={() => void updateAll()}>
            Tout mettre à jour
          </button>
          <button disabled={benchmarking} onClick={() => void runBenchmark()}>
            Lancer le benchmark
          </button>
          <button onClick={() => void refreshModels()}>Rafraîchir</button>
        </div>

        {benchResults.length > 0 && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <h3>Benchmark {benchCurrent ? `— en cours : ${benchCurrent}` : ""}</h3>
            {[...benchResults]
              .sort((a, b) => b.score_percent - a.score_percent)
              .map((r) => (
                <div key={r.tag} style={{ marginBottom: 10 }}>
                  <div className="progress-label" style={{ marginBottom: 3 }}>
                    <span>{r.tag}</span>
                    <span>
                      score {r.score_percent}% · {r.avg_tokens_per_second} tok/s ·{" "}
                      {(r.avg_latency_ms / 1000).toFixed(1)}s
                    </span>
                  </div>
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${r.score_percent}%` }} />
                  </div>
                </div>
              ))}
            <div style={{ color: "var(--text-faint)", fontSize: 11.5 }}>
              Tests : maths, code, raisonnement, français — température 0.
            </div>
          </div>
        )}

        <div className="models-grid">
          {models
            .filter((m) => !tierFiltre || tierDe(m) === tierFiltre)
            .map((m) => (
            <div key={m.tag} className={`model-card ${m.loaded ? "running" : ""}`}>
              <div className="model-head">
                <span className="model-name">{m.name}</span>
                {m.loaded ? (
                  <span className="chip green">en mémoire</span>
                ) : m.installed ? (
                  <span className="chip accent">installé</span>
                ) : (
                  <span className="chip">non installé</span>
                )}
              </div>
              <div className="model-tag">{m.tag}</div>
              {m.description && <div className="model-desc">{m.description}</div>}
              <div className="model-specs">
                {(m.parameter_size || m.size_label) && (
                  <span className="chip">{m.parameter_size || m.size_label}</span>
                )}
                {m.quantization && <span className="chip">{m.quantization}</span>}
                {m.vram_gb > 0 && <span className="chip">≈{m.vram_gb} Go VRAM</span>}
                {m.context > 0 && <span className="chip">{Math.round(m.context / 1024)}k ctx</span>}
                {m.installed && m.size_bytes > 0 && (
                  <span className="chip">disque : {formatSize(m.size_bytes)}</span>
                )}
                {m.loaded && m.runtime && m.runtime.gpu_memory_share_pct != null && (
                  <span className="chip green">GPU {m.runtime.gpu_memory_share_pct}%</span>
                )}
                {m.loaded && m.runtime && m.runtime.cpu_bytes_estimated > 0 && (
                  <span className="chip">RAM ≈{formatSize(m.runtime.cpu_bytes_estimated)}</span>
                )}
                {m.loaded && m.runtime && m.runtime.context_length != null && (
                  <span className="chip">contexte actif {Math.round(m.runtime.context_length / 1024)}k</span>
                )}
                {m.capabilities.map((c) => (
                  <span key={c} className="chip">
                    {c}
                  </span>
                ))}
              </div>

              {progress[m.tag] && (
                <div>
                  <div className="progress-track">
                    <div className="progress-fill" style={{ width: `${progress[m.tag].percent}%` }} />
                  </div>
                  <div className="progress-label">
                    <span>{progress[m.tag].status}</span>
                    <span>{progress[m.tag].percent.toFixed(0)}%</span>
                  </div>
                </div>
              )}

              <div className="model-actions">
                {!m.installed && (
                  <button
                    className="primary"
                    disabled={!!busyTag}
                    onClick={() => void installModel(m.tag)}
                  >
                    Installer
                  </button>
                )}
                {m.installed && !m.loaded && (
                  <button onClick={() => void loadModel(m.tag)}>Charger</button>
                )}
                {m.loaded && (
                  <button onClick={() => void unloadModel(m.tag)}>Décharger</button>
                )}
                {m.installed && (
                  <button
                    className="danger"
                    disabled={!!busyTag}
                    onClick={() => void deleteModel(m.tag)}
                  >
                    Supprimer
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function omit(obj: Record<string, ProgressState>, key: string): Record<string, ProgressState> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}
