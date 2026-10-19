import { useCallback, useEffect, useState } from "react";
import { getJSON, sendJSON, streamSSE } from "../lib/api";
import { LANGS, type Lang, t } from "../lib/i18n";

interface GpuInfo {
  name: string;
  vram_gb: number;
}
interface Hardware {
  tier: string;
  gpus: GpuInfo[];
  ram_gb: number;
  disk: { free_gb: number; total_gb: number };
  cpu_only: boolean;
  recommended: string[];
  optional: string[];
  plan: { resolu: Record<string, string>; manquants: Record<string, string[]> };
  installed: string[];
}
interface OnboardingData {
  language: string;
  onboarded: boolean;
  hardware: Hardware;
}

interface Props {
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
  onDone: (lang: Lang) => void;
}

type InstallState = "idle" | "running" | "done" | "error";

export default function OnboardingWizard({ toast, onDone }: Props) {
  const [step, setStep] = useState(1);
  const [lang, setLang] = useState<Lang>("fr");
  const [data, setData] = useState<OnboardingData | null>(null);
  const [loading, setLoading] = useState(false);
  const [installs, setInstalls] = useState<Record<string, InstallState>>({});
  const [messages, setMessages] = useState<Record<string, string>>({});

  const texte = useCallback((key: string) => t(lang, key), [lang]);

  const detecter = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getJSON<OnboardingData>("/api/onboarding");
      setData(d);
      const etats: Record<string, InstallState> = {};
      for (const tag of d.hardware.recommended || []) {
        etats[tag] = (d.hardware.installed || []).some(
          (x) => x.toLowerCase() === tag.toLowerCase()
        )
          ? "done"
          : "idle";
      }
      setInstalls(etats);
    } catch (err) {
      toast(`Détection impossible : ${err}`, "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (step === 2 && !data) void detecter();
  }, [step, data, detecter]);

  const installer = useCallback(
    async (tag: string) => {
      setInstalls((s) => ({ ...s, [tag]: "running" }));
      setMessages((m) => ({ ...m, [tag]: "…" }));
      try {
        await streamSSE("/api/models/install", { tag }, (ev) => {
          if (ev.type === "done") {
            setInstalls((s) => ({ ...s, [tag]: "done" }));
            setMessages((m) => ({ ...m, [tag]: "" }));
          } else if (ev.type === "error") {
            setInstalls((s) => ({ ...s, [tag]: "error" }));
            setMessages((m) => ({ ...m, [tag]: String(ev.error ?? "erreur") }));
          } else {
            const pct = ev.percent ?? ev.completed ?? ev.status ?? "…";
            setMessages((m) => ({ ...m, [tag]: String(pct) }));
          }
        });
      } catch (err) {
        setInstalls((s) => ({ ...s, [tag]: "error" }));
        setMessages((m) => ({ ...m, [tag]: String(err) }));
      }
    },
    []
  );

  const terminer = useCallback(async () => {
    try {
      await sendJSON("/api/settings", "PUT", { key: "language", value: lang });
      await sendJSON("/api/settings", "PUT", { key: "onboarded", value: "1" });
      await sendJSON("/api/settings", "PUT", { key: "auto_learn", value: "1" });
      toast("Néo est prêt", "success");
      onDone(lang);
    } catch (err) {
      toast(`Enregistrement impossible : ${err}`, "error");
    }
  }, [lang, onDone, toast]);

  const passer = useCallback(async () => {
    try {
      await sendJSON("/api/settings", "PUT", { key: "language", value: lang });
      await sendJSON("/api/settings", "PUT", { key: "onboarded", value: "1" });
    } catch {
      /* ignore */
    }
    onDone(lang);
  }, [lang, onDone]);

  return (
    <div className="onboarding-overlay">
      <div className="onboarding-card">
        <div className="onboarding-head">
          <div className="onboarding-logo">◆</div>
          <div>
            <h1>{texte("onboarding.welcome")}</h1>
            <p>{texte("onboarding.subtitle")}</p>
          </div>
          <span className="onboarding-step">
            {texte("onboarding.step")} {step}/3
          </span>
        </div>

        {step === 1 && (
          <div className="onboarding-body">
            <h2>{texte("onboarding.chooseLang")}</h2>
            <div className="lang-grid">
              {LANGS.map((l) => (
                <button
                  key={l.code}
                  className={`lang-btn ${lang === l.code ? "active" : ""}`}
                  onClick={() => setLang(l.code)}
                >
                  <span className="lang-flag">{l.flag}</span>
                  <span>{l.label}</span>
                </button>
              ))}
            </div>
            <div className="onboarding-actions">
              <button className="primary" onClick={() => setStep(2)}>
                {texte("onboarding.continue")} ▸
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="onboarding-body">
            <h2>{texte("onboarding.detect")}</h2>
            {loading && <div className="onboarding-note">{texte("onboarding.analyzing")}</div>}
            {data && (
              <>
                <div className="hw-grid">
                  <div className="hw-cell">
                    <span className="hw-label">{texte("onboarding.machine")}</span>
                    <strong>
                      {data.hardware.gpus[0]?.name ?? "CPU uniquement"}
                    </strong>
                    <span className="hw-sub">
                      {data.hardware.gpus[0]?.vram_gb ?? 0} Go VRAM · {data.hardware.ram_gb} Go RAM
                    </span>
                  </div>
                  <div className="hw-cell">
                    <span className="hw-label">{texte("onboarding.tier")}</span>
                    <strong>{data.hardware.tier}</strong>
                    <span className="hw-sub">
                      {data.hardware.tier === "T1"
                        ? "modèles légers"
                        : data.hardware.tier === "T2"
                        ? "modèles équilibrés"
                        : "grands modèles"}
                    </span>
                  </div>
                  <div className="hw-cell">
                    <span className="hw-label">Disque</span>
                    <strong>{data.hardware.disk.free_gb} Go libres</strong>
                    <span className="hw-sub">/ {data.hardware.disk.total_gb} Go</span>
                  </div>
                </div>

                <h3 className="onboarding-subtitle">{texte("onboarding.recommended")}</h3>
                <div className="model-lines">
                  {(data.hardware.recommended || []).map((tag) => {
                    const etat = installs[tag] ?? "idle";
                    return (
                      <div className="model-line" key={tag}>
                        <span className="model-tag">{tag}</span>
                        {etat === "done" && (
                          <span className="chip green">{texte("onboarding.installed")}</span>
                        )}
                        {etat === "running" && (
                          <span className="chip accent">{messages[tag] || "…"}</span>
                        )}
                        {etat === "error" && (
                          <span className="chip" style={{ color: "var(--red)" }}>
                            {messages[tag]}
                          </span>
                        )}
                        {etat === "idle" && (
                          <button onClick={() => void installer(tag)}>
                            {texte("onboarding.install")}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}
            <div className="onboarding-actions">
              <button onClick={() => setStep(1)}>◂</button>
              <button className="primary" onClick={() => setStep(3)}>
                {texte("onboarding.continue")} ▸
              </button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="onboarding-body">
            <div className="onboarding-ready">
              <div className="core-dot" />
              <h2>{texte("onboarding.ready")}</h2>
              <p>{texte("onboarding.readyHint")}</p>
            </div>
            <div className="onboarding-actions">
              <button onClick={() => setStep(2)}>◂</button>
              <button className="primary" onClick={() => void terminer()}>
                {texte("onboarding.finish")} ▸
              </button>
            </div>
          </div>
        )}

        <button className="onboarding-skip" onClick={() => void passer()}>
          {texte("onboarding.skip")}
        </button>
      </div>
    </div>
  );
}
