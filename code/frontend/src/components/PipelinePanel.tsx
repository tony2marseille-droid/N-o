export interface PipelineStep {
  name: string;
  at: number;
  denied?: boolean;
  preview?: string;
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

function LigneEtape({ step, index }: { step: PipelineStep; index: number }) {
  const heure = new Date(step.at).toLocaleTimeString("fr-FR", { hour12: false });

  return (
    <div className={`pp-line ${step.done ? "pp-decrypte" : "pp-en-cours"}`} style={{ animationDelay: `${index * 40}ms` }}>
      <span className="pp-time">[{heure}]</span>
      <span className="pp-caret">{step.name}</span>
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
      {step.preview && step.done && (
        <span className="pp-preview">{step.preview.slice(0, 90)}</span>
      )}
    </div>
  );
}

export default function PipelinePanel({ agent, model, etaMs, steps, streaming }: Props) {
  const titre = `pipeline · agent:${agent || "auto"} · modèle:${model || "auto"}`;
  const minutes = etaMs ? Math.round(etaMs / 60000) : null;
  const secondes = etaMs ? Math.round(etaMs / 1000) : null;

  return (
    <div className="pipeline-panel">
      <div className="pp-scanline" />
      <div className="pp-head">
        <span className="pp-title">{titre}</span>
        {etaMs !== null && (
          <span className="pp-eta">
            ⏱ ~{minutes !== null && minutes >= 1 ? `${minutes} min` : `${secondes}s`}
          </span>
        )}
        {streaming && <span className="pp-live">● live</span>}
      </div>
      <div className="pp-steps">
        {steps.map((s, i) => (
          <LigneEtape key={`${s.name}-${s.at}`} step={s} index={i} />
        ))}
        {streaming && steps.length === 0 && (
          <div className="pp-line pp-en-cours">
            <span className="pp-caret">▍Néo réfléchit…</span>
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
