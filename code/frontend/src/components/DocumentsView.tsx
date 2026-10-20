import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentItem, SearchResult } from "../lib/api";
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
};

const CAT_DESCS: Record<string, string> = {
  programmation: "Fonctions Python demandées et validées : le corpus qui a entraîné le champion.",
  ia: "Corpus et notes sur l'IA et l'auto-amélioration de Néo.",
  impression3d: "Meshes, STL et impression 3D.",
  bricolage: "Projets manuels et conseils pratiques.",
  cuisine: "Recettes et techniques culinaires.",
  sante: "Santé et bien-être.",
  finances: "Budget et finances personnelles.",
  loisirs: "Passions, jeux et lectures.",
  science: "Sciences et curiosités.",
  autre: "Documents divers non rangés ailleurs.",
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
  const fileRef = useRef<HTMLInputElement>(null);
  const fichesPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  useEffect(() => {
    refresh();
    refreshFiches();
    return () => {
      if (fichesPollRef.current) clearInterval(fichesPollRef.current);
    };
  }, [refresh, refreshFiches]);

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
            {docsFiltres.length === 0 && (
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
