import { useEffect, useState } from "react";

export interface PipelineStep {
  name: string;
  at: number;
  denied?: boolean;
  preview?: string;
  args?: Record<string, unknown>;
  done: boolean;
  duration?: number;
}

interface Props {
  agent: string;
  model: string;
  etaMs: number | null;
  steps: PipelineStep[];
  streaming: boolean;
}

/** Resume compact des arguments d'un appel d'outil pour l'affichage. */
function resumeArgs(args?: Record<string, unknown>): string {
  if (!args || Object.keys(args).length === 0) return "";
  const parties = Object.entries(args).map(([cle, val]) => {
    let v: string;
    if (typeof val === "string") {
      v = val.length > 48 ? val.slice(0, 45) + "…" : val;
      v = `"${v}"`;
    } else if (typeof val === "number" || typeof val === "boolean") {
      v = String(val);
    } else {
      v = "…";
    }
    return `${cle}=${v}`;
  });
  const s = parties.join(", ");
  return s.length > 110 ? s.slice(0, 107) + "…" : s;
}

function LigneEtape({ step, index }: { step: PipelineStep; index: number }) {
  const [affiche, setAffiche] = useState("");
  const [decrypte, setDecrypte] = useState(step.done);

  useEffect(() => {
    const cible = step.name;
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setAffiche(cible.slice(0, i));
      if (i >= cible.length) {
        clearInterval(id);
        setDecrypte(true);
      }
    }, 24);
    return () => clearInterval(id);
  }, [step.name]);

  const heure = new Date(step.at).toLocaleTimeString("fr-FR", { hour12: false });
  const argsTxt = resumeArgs(step.args);
  const previewTxt = (step.preview ?? "").slice(0, 160);

  return (
    <div className={`pp-line ${decrypte ? "pp-decrypte" : "pp-en-cours"}`} style={{ animationDelay: `${index * 40}ms` }}>
      <span className="pp-time">[{heure}]</span>
      <span className="pp-caret">{affiche}</span>
      {argsTxt && <span className="pp-args">({argsTxt})</span>}
      {step.done ? (
        step.denied ? (
          <span className="pp-status pp-refuse">✗ REFUSÉ</span>
        ) : (
          <span className="pp-status pp-ok">
            ✓{step.duration !== undefined ? ` ${step.duration}s` : ""}
          </span>
        )
      ) : (
        <span className="pp-status pp-run">▸ traitement…</span>
      )}
      {previewTxt && decrypte && (
        <span className="pp-preview" title={step.preview}>
          → {previewTxt}
        </span>
      )}
    </div>
  );
}

export default function PipelinePanel({ agent, model, etaMs, steps, streaming }: Props) {
  const [titreRevele, setTitreRevele] = useState("");
  const [secondes, setSecondes] = useState(0);

  useEffect(() => {
    const cible = agent
      ? `Néo · ${agent}${model ? ` · ${model}` : ""}`
      : "Néo";
    let i = 0;
    const id = setInterval(() => {
      i += 1;
      setTitreRevele(cible.slice(0, i));
      if (i >= cible.length) clearInterval(id);
    }, 18);
    return () => clearInterval(id);
  }, [agent, model]);

  // Chronometre vivant : combien de temps Néo travaille en continu.
  useEffect(() => {
    if (!streaming) {
      setSecondes(0);
      return;
    }
    const debut = Date.now();
    const id = setInterval(() => setSecondes(Math.floor((Date.now() - debut) / 1000)), 1000);
    return () => clearInterval(id);
  }, [streaming]);

  const minutes = etaMs ? Math.round(etaMs / 60000) : null;
  const secondesEta = etaMs ? Math.round(etaMs / 1000) : null;
  const enCours = steps.filter((s) => !s.done).length;
  const phase = enCours > 0
    ? `Néo fait appel à « ${steps[steps.length - 1].name} »${steps[steps.length - 1].args ? " " + resumeArgs(steps[steps.length - 1].args) : ""}`
    : `Néo génère la réponse`;

  return (
    <div className="pipeline-panel">
      <div className="pp-scanline" />
      <div className="pp-head">
        <span className="pp-title">{titreRevele}</span>
        {etaMs !== null && (
          <span className="pp-eta">
            ⏱ ~{minutes !== null && minutes >= 1 ? `${minutes} min` : `${secondesEta}s`}
          </span>
        )}
        {streaming && <span className="pp-live">● live</span>}
      </div>
      <div className="pp-steps">
        {steps.map((s, i) => (
          <LigneEtape key={`${s.name}-${s.at}`} step={s} index={i} />
        ))}
        {streaming && (
          <div className="pp-line pp-en-cours">
            <span className="pp-caret">▍{phase}… {secondes}s</span>
          </div>
        )}
      </div>
      {streaming && (
        <div className="pp-foot">
          <span className="pp-bar">
            <span className="pp-bar-fill" />
          </span>
        </div>
      )}
    </div>
  );
}
