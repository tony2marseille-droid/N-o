import { useCallback, useEffect, useRef, useState } from "react";
import type { DocumentItem, SearchResult } from "../lib/api";
import { getJSON, sendJSON } from "../lib/api";

interface Props {
  toast: (msg: string, kind?: "info" | "error" | "success") => void;
}

export default function DocumentsView({ toast }: Props) {
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    getJSON<{ documents: DocumentItem[] }>("/api/documents")
      .then((d) => setDocs(d.documents))
      .catch(() => toast("Impossible de lister les documents", "error"));
  }, [toast]);

  useEffect(refresh, [refresh]);

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
          toast(`${data.filename} indexé (${data.chunks} extraits)`, "success");
        } catch (err) {
          toast(`Échec de l'indexation : ${err}`, "error");
        }
      }
      setUploading(false);
      refresh();
    },
    [refresh, toast]
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
        toast("Document supprimé", "success");
      } catch {
        toast("Suppression impossible", "error");
      }
    },
    [refresh, toast]
  );

  return (
    <>
      <div className="view-header">
        <h2>Documents</h2>
        <span style={{ color: "var(--text-dim)", fontSize: 13 }}>
          Indexés pour la recherche (RAG)
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

        <div className="models-grid">
          {docs.length === 0 && (
            <div style={{ color: "var(--text-faint)" }}>
              Aucun document. Ajoutez des PDF, DOCX, XLSX, TXT…
            </div>
          )}
          {docs.map((d) => (
            <div key={d.id} className="model-card">
              <div className="model-head">
                <span className="model-name">{d.filename}</span>
              </div>
              <div className="model-specs">
                <span className="chip">{(d.size_bytes / 1024).toFixed(0)} Ko</span>
                <span className="chip">{d.chunk_count} extraits</span>
                <span className="chip">{d.created_at.slice(0, 16).replace("T", " ")}</span>
              </div>
              <div className="model-actions">
                <button className="danger" onClick={() => void removeDoc(d.id)}>
                  Supprimer
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
