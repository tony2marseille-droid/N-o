import { useEffect, useState } from "react";
import type { ProviderCredit } from "../lib/api";
import { getJSON } from "../lib/api";

interface StatsReq {
  in: number;
  out: number;
  tps: number;
  model: string;
  estime?: boolean;
}

/**
 * Encart tokens (consigne) : remplace l'historique dans la barre
 * laterale. Affiche la consommation de la DERNIERE requete (entree /
 * sortie / total / vitesse) et le suivi en direct par fournisseur
 * cloud (credit restant, tokens consommes, erreurs de quota).
 */
export default function TokenPanel() {
  const [credits, setCredits] = useState<ProviderCredit[]>([]);
  const [derniere, setDerniere] = useState<StatsReq | null>(null);

  useEffect(() => {
    const charger = () => {
      getJSON<{ fournisseurs: ProviderCredit[] }>("/api/fournisseurs/credits")
        .then((d) => setCredits(d.fournisseurs ?? []))
        .catch(() => undefined);
    };
    charger();
    const timer = setInterval(charger, 5000);
    const surStats = (e: Event) => {
      setDerniere((e as CustomEvent).detail as StatsReq);
    };
    window.addEventListener("neo-stats", surStats);
    return () => {
      clearInterval(timer);
      window.removeEventListener("neo-stats", surStats);
    };
  }, []);

  const fmtDate = (unix: number) => {
    if (!unix) return "";
    const d = new Date(unix * 1000);
    return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  };

  return (
    <div className="token-panel">
      <div className="sidebar-section-title">Tokens</div>

      <div className="token-box">
        <div className="token-head">Dernière requête</div>
        {derniere ? (
          <>
            <div className="token-row">
              <span>entrée</span>
              <span className="token-strong">{derniere.in}</span>
            </div>
            <div className="token-row">
              <span>sortie</span>
              <span className="token-strong">{derniere.out}</span>
            </div>
            <div className="token-row">
              <span>total</span>
              <span className="token-strong">{derniere.in + derniere.out}</span>
            </div>
            <div className="token-row">
              <span>vitesse</span>
              <span>
                {derniere.tps} tok/s{derniere.estime ? " (estimé)" : ""}
              </span>
            </div>
            {derniere.model && (
              <div className="token-model">{derniere.model}</div>
            )}
          </>
        ) : (
          <div className="token-empty">En attente d'une requête…</div>
        )}
      </div>

      <div className="token-head" style={{ marginTop: 6 }}>
        Fournisseurs cloud
      </div>
      {credits.map((f) => (
        <div key={f.id} className={`token-prov ${f.configue ? "" : "off"}`}>
          <span
            className={`hud-dot ${f.configue ? (f.quota_err ? "bad" : "ok") : ""}`}
          />
          <span className="token-nom" title={`${f.nom} — ${f.modele}`}>
            {f.nom}
          </span>
          <span className="token-val">
            {f.credit
              ? `${(f.credit.restant ?? 0).toFixed(2)} $ restant`
              : f.configue
                ? `${f.tokens_in}↑ ${f.tokens_out}↓`
                : "non configuré"}
          </span>
        </div>
      ))}
      {credits.some((f) => f.quota_err) && (
        <div className="token-quota-err">
          Quota/credit épuisé détecté — bascule automatique vers un modèle
          local active.
        </div>
      )}
      {credits
        .filter((f) => f.configue && f.updated_at)
        .slice(0, 1)
        .map((f) => (
          <div key="maj" className="token-empty" style={{ fontSize: 10.5 }}>
            MAJ {fmtDate(0) || f.updated_at.slice(11, 16)}
          </div>
        ))}
    </div>
  );
}
