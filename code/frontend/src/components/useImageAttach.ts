import { useCallback, useEffect, useRef, useState } from "react";

const MAX_SIDE = 1344; // taille max (px) envoyée au modèle vision
const MAX_IMAGES = 4;

export type PendingImage = { id: string; name: string; preview: string; b64: string };

/** Lit un fichier image, le réduit, le convertit en JPEG et renvoie le base64 BRUT (sans "data:...;base64,"). */
async function fileToPending(file: File): Promise<PendingImage> {
  const bmp = await createImageBitmap(file);
  const k = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(bmp.width * k));
  c.height = Math.max(1, Math.round(bmp.height * k));
  const ctx = c.getContext("2d")!;
  ctx.fillStyle = "#fff"; // évite le fond noir des PNG transparents
  ctx.fillRect(0, 0, c.width, c.height);
  ctx.drawImage(bmp, 0, 0, c.width, c.height);
  bmp.close();
  const url = c.toDataURL("image/jpeg", 0.9);
  // pas de crypto.randomUUID : indisponible en http:// hors localhost
  return { id: Math.random().toString(36).slice(2), name: file.name, preview: url, b64: url.split(",")[1] };
}

const hasFiles = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes("Files");

export function useImageAttach() {
  const [images, setImages] = useState<PendingImage[]>([]);
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);

  /** Ajoute des fichiers (trombone, drop, collage). La liste est copiée AVANT tout await. */
  const attachFiles = useCallback(async (list: FileList | File[] | null) => {
    const files = Array.from(list ?? []).filter((f) => f.type.startsWith("image/"));
    if (!files.length) return;
    const res = await Promise.allSettled(files.map(fileToPending));
    const ok = res.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
    setImages((p) => [...p, ...ok].slice(0, MAX_IMAGES));
  }, []);

  const remove = useCallback((id: string) => setImages((p) => p.filter((i) => i.id !== id)), []);
  const clear = useCallback(() => setImages([]), []);

  /** Props à étaler sur le conteneur du chat : <div className="chat-container" {...dragProps}> */
  const dragProps = {
    onDragEnter: (e: React.DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current++;
      setDragging(true);
    },
    onDragOver: (e: React.DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault(); // OBLIGATOIRE sinon le drop est refusé / le navigateur ouvre l'image
      e.dataTransfer.dropEffect = "copy";
    },
    onDragLeave: (e: React.DragEvent) => {
      if (!hasFiles(e)) return;
      if (--depth.current <= 0) { depth.current = 0; setDragging(false); }
    },
    onDrop: (e: React.DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      depth.current = 0;
      setDragging(false);
      void attachFiles(e.dataTransfer.files);
    },
    onPaste: (e: React.ClipboardEvent) => {
      const f = Array.from(e.clipboardData.files).filter((x) => x.type.startsWith("image/"));
      if (f.length) { e.preventDefault(); void attachFiles(f); }
    },
  };

  /** Empêche le navigateur de quitter la page si un fichier est lâché hors de la zone. */
  useEffect(() => {
    const stop = (e: DragEvent) => { if (e.dataTransfer?.types.includes("Files")) e.preventDefault(); };
    window.addEventListener("dragover", stop);
    window.addEventListener("drop", stop);
    return () => { window.removeEventListener("dragover", stop); window.removeEventListener("drop", stop); };
  }, []);

  return { images, dragging, attachFiles, remove, clear, dragProps };
}

/** Corps de requête : texte utilisateur conservé, images en base64 brut. */
export function buildChatBody<T extends object>(text: string, images: PendingImage[], rest: T) {
  const t = text.trim();
  return {
    ...rest,
    message: t || (images.length ? "Décris cette image en détail." : ""),
    images: images.map((i) => i.b64),
  };
}
