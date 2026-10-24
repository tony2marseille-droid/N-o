import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentItem, NeoFile, SearchResult } from "../lib/api";
import { getJSON, sendJSON } from "../lib/api";

interface Props {
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
}

interface Fiche {
  category: string;
  count: number;
  illustration: boolean;
}

const CAT_LABELS: Record<string, string> = {
  programmation: "Programmation",
  ia: "Intelligence artificielle",
  impression3d: "Impression 3D",
  bricolage: "Bricolage",
  cuisine: "Cuisine",
  sante: "Santé",
  finances: "Finances",
  loisirs: "Loisirs",
  science: "Science",
  autre: "Autre",
  neo: "Néo",
  photos_generees: "Photos générées",
  modeles_3d: "Modèles 3D",
};

const CAT_DESCS: Record<string, string> = {
  programmation: "Exercices qui ont servi à l'éducation de Néo.",
  ia: "Textes sur l'intelligence artificielle et l'évolution de Néo.",
  impression3d: "Objets en 3D et fabrication.",
  bricolage: "Travaux manuels et réparations.",
  cuisine: "Recettes et astuces de cuisine.",
  sante: "Santé et bien-être au quotidien.",
  finances: "Argent, budget et économies.",
  loisirs: "Loisirs, jeux et passions.",
  science: "Sciences expliquées simplement.",
  autre: "Documents divers.",
  neo: "Photos, meshes et documents generes par Néo pendant le chat.",
  photos_generees: "Images générées par Néo (FLUX, Kontext, etc.).",
  modeles_3d: "Modèles 3D et projets créés par Néo (STL, OpenSCAD, etc.).",
};

export default function DocumentsView({ toast }: Props) {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const [fiches, setFiches] = useState<Fiche[]>([]);
  const [nonClasses, setNonClasses] = useState(0);
  const [filtre, setFiltre] = useState("");
  const [analysing, setAnalysing] = useState(false);
  const [neoFiles, setNeoFiles] = useState<NeoFile[]>([]);
  const [selectionNeo, setSelectionNeo] = useState<string[]>([]);
  const [envoiTelegram, setEnvoiTelegram] = useState(false);
  const [appr, setAppr] = useState<{ running: boolean; category: string; etape: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const fichesPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const apprPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const sansDescription = docs.filter((d) => !d.description).length;

  const refresh = useCallback(() => {
    getJSON<{ documents: DocumentItem[] }>("/api/documents")
      .then((d) => setDocs(d.documents))
      .catch(() => toast("Impossible de lister les documents", "error"));
  }, [toast]);

  const refreshFiches = useCallback(() => {
    getJSON<{ fiches: Fiche[]; non_classes: number }>("/api/documents/fiches")
      .then((d) => {
        setFiches(d.fiches);
        setNonClasses(d.non_classes);
        const manque = d.fiches.some((f) => !f.illustration);
        if (manque && !fichesPollRef.current) {
          fichesPollRef.current = setInterval(refreshFiches, 10000);
        } else if (!manque && fichesPollRef.current) {
          clearInterval(fichesPollRef.current);
          fichesPollRef.current = null;
        }
      })
      .catch(() => undefined);
  }, []);

  const apprAvant = useRef(false);
  const refreshAppr = useCallback(() => {
    getJSON<{ running: boolean; category: string; etape: string }>("/api/documents/apprentissage/status")
      .then((d) => {
        if (apprAvant.current && !d.running && d.category) {
          toast(`Néo a fini d'apprendre « ${CAT_LABELS[d.category] ?? d.category} »`, "success");
          refresh();
          refreshFiches();
        }
        apprAvant.current = d.running;
        setAppr(d);
        if (d.running && !apprPollRef.current) {
          apprPollRef.current = setInterval(refreshAppr, 10000);
        } else if (!d.running && apprPollRef.current) {
          clearInterval(apprPollRef.current);
          apprPollRef.current = null;
        }
      })
      .catch(() => undefined);
  }, [toast, refresh, refreshFiches]);

  const lancerApprentissage = useCallback(
    async (category: string) => {
      try {
        const r = await sendJSON<{ ok: boolean; detail?: string }>(
          "/api/documents/apprentissage",
          "POST",
          { category }
        );
        if (!r.ok) {
          toast(r.detail ?? "Impossible de lancer", "error");
          return;
        }
        toast(`Néo commence à apprendre « ${CAT_LABELS[category] ?? category} »`, "info");
        setAppr({ running: true, category, etape: "recherche en cours…" });
        if (!apprPollRef.current) {
          apprPollRef.current = setInterval(refreshAppr, 10000);
        }
      } catch {
        toast("Échec du lancement de l'apprentissage", "error");
      }
    },
    [toast, refreshAppr]
  );

  useEffect(() => {
    refresh();
    refreshFiches();
    refreshAppr();
    return () => {
      if (fichesPollRef.current) clearInterval(fichesPollRef.current);
      if (apprPollRef.current) clearInterval(apprPollRef.current);
    };
  }, [refresh, refreshFiches, refreshAppr]);

  // Categorie "Neo" : fichiers generes par le chat (classement auto).
  useEffect(() => {
    if (filtre === "neo") {
      getJSON<{ files: NeoFile[] }>("/api/documents/neo-files")
        .then((d) => setNeoFiles(d.files ?? []))
        .catch(() => setNeoFiles([]));
    } else {
      setSelectionNeo([]);
    }
  }, [filtre]);

  // TIFF/EXR : affichage et Telegram passent par l'apercu JPEG
  // (les navigateurs et Telegram ne lisent pas ces formats).
  const cheminAffiche = useCallback(
    (p: string) => (/\.(tiff?|exr)$/i.test(p) ? p.replace(/\.(tiff?|exr)$/i, "_apercu.jpg") : p),
    []
  );

  // Envoi Telegram multi-photos : lots de 10 par requete.
  const envoyerSelectionTelegram = useCallback(async () => {
    if (selectionNeo.length === 0 || envoiTelegram) return;
    setEnvoiTelegram(true);
    try {
      const images = selectionNeo.map(cheminAffiche);
      let envoyees = 0;
      for (let i = 0; i < images.length; i += 10) {
        const lot = images.slice(i, i + 10);
        const r = await sendJSON<{ ok: boolean; images?: number; error?: string }>(
          "/api/telegram/send",
          "POST",
          {
            text:
              images.length > 10
                ? `Photos Neo (${i + 1}-${i + lot.length} sur ${images.length})`
                : `Photos Neo (${lot.length})`,
            images: lot,
          }
        );
        if (!r.ok) throw new Error(r.error ?? "echec envoi");
        envoyees += r.images ?? lot.length;
      }
      toast(`${envoyees} photo(s) envoyee(s) sur Telegram`, "success");
      setSelectionNeo([]);
    } catch (err) {
      toast(`Telegram : ${err}`, "error");
    } finally {
      setEnvoiTelegram(false);
    }
  }, [selectionNeo, envoiTelegram, cheminAffiche, toast]);

  const analyser = useCallback(async () => {
    setAnalysing(true);
    try {
      for (let i = 0; i < 30; i++) {
        const r = await sendJSON<{ classified: number; remaining: number }>(
          "/api/documents/classify",
          "POST",
          { limit: 8 }
        );
        if (r.classified === 0 || r.remaining === 0) break;
      }
      for (let i = 0; i < 30; i++) {
        const r = await sendJSON<{ described: number; remaining: number }>(
          "/api/documents/describe",
          "POST",
          { limit: 8 }
        );
        if (r.described === 0 || r.remaining === 0) break;
      }
      toast("Documents classés et décrits", "success");
    } catch {
      toast("Échec de l'analyse", "error");
    } finally {
      setAnalysing(false);
      refresh();
      refreshFiches();
    }
  }, [toast, refresh, refreshFiches]);

  const upload = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      setUploading(true);
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        try {
          const resp = await fetch("/api/documents/upload", {
            method: "POST",
            body: form,
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }));
            throw new Error(String(err.detail));
          }
          const data = await resp.json();
          toast(
            `${data.filename} indexé (${data.chunks} extraits) — ${data.description || (CAT_LABELS[data.category] ?? "à décrire")}`,
            "success"
          );
        } catch (err) {
          toast(`Échec de l'indexation : ${err}`, "error");
        }
      }
      setUploading(false);
      refresh();
      refreshFiches();
    },
    [refresh, refreshFiches, toast]
  );

  const doSearch = useCallback(async () => {
    if (!query.trim()) return;
    setResults(null);
    try {
      const data = await sendJSON<{ results: SearchResult[] }>(
        "/api/documents/search",
        "POST",
        { query, top_k: 5 }
      );
      setResults(data.results);
    } catch (err) {
      toast(`Recherche impossible : ${err}`, "error");
    }
  }, [query, toast]);

  const removeDoc = useCallback(
    async (id: number) => {
      try {
        await sendJSON(`/api/documents/${id}`, "DELETE");
        refresh();
        refreshFiches();
        toast("Document supprimé", "success");
      } catch {
        toast("Suppression impossible", "error");
      }
    },
    [refresh, refreshFiches, toast]
  );

  const docsFiltres = filtre ? docs.filter((d) => (d.category ?? "") === filtre) : [];
  const ficheActive = fiches.find((f) => f.category === filtre);

  return (
    <>
      <div className="view-header">
        <h2>Documents</h2>
        <span style={{ color: "var(--text-dim)", fontSize: 13 }}>
          Bibliothèque par catégories (RAG)
        </span>
        <span style={{ flex: 1 }} />
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".pdf,.docx,.xlsx,.xlsm,.txt,.md,.csv,.json,.py,.log"
          style={{ display: "none" }}
          onChange={(e) => void upload(e.target.files)}
        />
        <button className="primary" disabled={uploading} onClick={() => fileRef.current?.click()}>
          {uploading ? "Indexation…" : "+ Ajouter des documents"}
        </button>
      </div>
      <div className="content-scroll">
        <div className="panel" style={{ marginBottom: 14 }}>
          <h3>Fiches par catégorie</h3>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))",
              gap: 12,
            }}
          >
            {fiches.length === 0 && (
              <div style={{ color: "var(--text-faint)" }}>Chargement…</div>
            )}
            {fiches.map((f) => (
              <div
                key={f.category}
                className="model-card"
                style={{
                  cursor: "pointer",
                  padding: 0,
                  overflow: "hidden",
                  borderColor: filtre === f.category ? "var(--accent)" : undefined,
                }}
                onClick={() => setFiltre(filtre === f.category ? "" : f.category)}
              >
                {f.illustration ? (
                  <img
                    src={`/api/sd/file?path=data/fiches/fiche_${f.category}.jpg`}
                    alt={f.category}
                    style={{ width: "100%", height: 100, objectFit: "cover", display: "block" }}
                  />
                ) : (
                  <div
                    style={{
                      height: 100,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "var(--text-faint)",
                      fontSize: 12,
                    }}
                  >
                    illustration en cours…
                  </div>
                )}
                <div style={{ padding: "8px 10px" }}>
                  <div style={{ fontSize: 12.5, fontWeight: 600 }}>
                    {CAT_LABELS[f.category] ?? f.category}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-dim)", margin: "3px 0 4px" }}>
                    {CAT_DESCS[f.category] ?? ""}
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--accent)" }}>
                    {f.count} document{f.count > 1 ? "s" : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
          {((nonClasses > 0 || sansDescription > 0) || analysing) && (
            <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <button disabled={analysing} onClick={() => void analyser()}>
                {analysing ? "Analyse…" : "Classer et décrire les documents"}
              </button>
              <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                {nonClasses > 0 && `${nonClasses} à classer`}
                {nonClasses > 0 && sansDescription > 0 && " · "}
                {sansDescription > 0 && `${sansDescription} à décrire`}
              </span>
            </div>
          )}
        </div>

        {filtre && ficheActive && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <h3 style={{ margin: 0 }}>{CAT_LABELS[filtre] ?? filtre}</h3>
              <span className="chip">{docsFiltres.length}</span>
              <span style={{ flex: 1 }} />
              <button style={{ padding: "3px 10px", fontSize: 12 }} onClick={() => setFiltre("")}>
                ✕ Fermer
              </button>
            </div>
            <div style={{ fontSize: 12.5, color: "var(--text-dim)", marginBottom: 12 }}>
              {CAT_DESCS[filtre] ?? ""}
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
              <button
                className="primary"
                disabled={(appr?.running ?? false) || filtre === "neo"}
                onClick={() => void lancerApprentissage(filtre)}
              >
                {appr?.running && appr.category === filtre ? "Néo apprend…" : "Lancer un apprentissage"}
              </button>
              {appr?.running && (
                <span style={{ fontSize: 12, color: "var(--orange)" }}>
                  {appr.category === filtre
                    ? `${appr.etape || "en cours…"}`
                    : `en attente : Néo apprend « ${CAT_LABELS[appr.category] ?? appr.category} »`}
                </span>
              )}
            </div>
            {filtre === "neo" && (
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
                <button
                  className="primary"
                  disabled={selectionNeo.length === 0 || envoiTelegram}
                  onClick={() => void envoyerSelectionTelegram()}
                >
                  {envoiTelegram
                    ? "Envoi en cours..."
                    : `Envoyer ${selectionNeo.length} photo(s) sur Telegram`}
                </button>
                {selectionNeo.length > 0 && (
                  <button onClick={() => setSelectionNeo([])}>Tout désélectionner</button>
                )}
                <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
                  Cochez les photos à envoyer (lots de 10).
                </span>
              </div>
            )}
            {filtre === "neo" && (
              <div className="neo-file-grid">
                {neoFiles.length === 0 && (
                  <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>
                    Aucun fichier genere pour l'instant. Demandez une image dans
                    le chat, elle apparaitra ici.
                  </div>
                )}
                {neoFiles.map((nf) => {
                  const affichee = cheminAffiche(nf.path);
                  const choisi = selectionNeo.includes(nf.path);
                  return (
                    <div
                      key={nf.path}
                      className={`neo-file-card ${choisi ? "selected" : ""}`}
                    >
                      <button
                        className={`neo-check ${choisi ? "checked" : ""}`}
                        title={choisi ? "Retirer de la sélection" : "Sélectionner"}
                        onClick={() =>
                          setSelectionNeo((s) =>
                            choisi ? s.filter((x) => x !== nf.path) : [...s, nf.path]
                          )
                        }
                      >
                        {choisi ? "✓" : ""}
                      </button>
                      <img
                        src={`/api/sd/file?path=${encodeURIComponent(affichee)}`}
                        alt={nf.name}
                        onClick={() =>
                          window.dispatchEvent(
                            new CustomEvent("open-fullscreen", {
                              detail: {
                                src: `/api/sd/file?path=${encodeURIComponent(affichee)}`,
                                alt: nf.name,
                              },
                            })
                          )
                        }
                      />
                      <div className="neo-file-meta" title={nf.path}>
                        {nf.name} &mdash; {(nf.size_bytes / 1048576).toFixed(1)} Mo
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {filtre !== "neo" && docsFiltres.length === 0 && (
              <div style={{ color: "var(--text-faint)", fontSize: 12.5 }}>
                Aucun document dans cette catégorie.
              </div>
            )}
            {docsFiltres.map((d) => (
              <div
                key={d.id}
                className="model-card"
                style={{ marginBottom: 10, borderColor: "var(--border)" }}
              >
                <div style={{ fontSize: 13, marginBottom: 4 }}>
                  {d.description || (
                    <span style={{ color: "var(--text-faint)" }}>Description à générer…</span>
                  )}
                </div>
                <div className="model-specs">
                  <span className="chip">{d.filename}</span>
                  <span className="chip">{(d.size_bytes / 1024).toFixed(0)} Ko</span>
                  <span className="chip">{d.chunk_count} extraits</span>
                </div>
                <div className="model-actions">
                  <button className="danger" onClick={() => void removeDoc(d.id)}>
                    Supprimer
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {!filtre && (
          <div className="panel" style={{ marginBottom: 14 }}>
            <h3>Tester la recherche</h3>
            <div style={{ display: "flex", gap: 10 }}>
              <input
                placeholder="Votre question dans les documents…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                style={{ flex: 1 }}
                onKeyDown={(e) => e.key === "Enter" && void doSearch()}
              />
              <button className="primary" onClick={() => void doSearch()}>
                Chercher
              </button>
            </div>
            {results !== null && (
              <div style={{ marginTop: 12 }}>
                {results.length === 0 && (
                  <div style={{ color: "var(--text-dim)", fontSize: 13 }}>Aucun passage trouvé.</div>
                )}
                {results.map((r, i) => (
                  <div key={i} className="search-hit">
                    <div className="search-hit-head">
                      <span>{r.filename}</span>
                      <span>score {r.score}</span>
                    </div>
                    <div>{r.content.slice(0, 300)}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {!filtre && (
          <div style={{ color: "var(--text-faint)", fontSize: 12.5, textAlign: "center", padding: 8 }}>
            Cliquez sur une fiche pour découvrir ses documents et leur rôle.
          </div>
        )}
      </div>
    </>
  );
}
