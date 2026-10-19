import { useEffect, useRef } from "react";

/**
 * Pluie numerique Matrix — canvas en fond, subtile et premium.
 * 100 % locale : caracteres katakana/latin, phosphore vert, traînees.
 * Respecte prefers-reduced-motion (trame statique) et se met en pause
 * quand l'onglet est masque (coût CPU quasi nul : ~15 images/s).
 */
const GLYPHES =
  "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホ0123456789<>[]{}=+*";

export default function MatrixRain({ background = false }: { background?: boolean }) {
  const ref = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const FONT = 14;
    const PAS_MS = 66;
    let largeur = 0;
    let hauteur = 0;
    let colonnes = 0;
    let gouttes: number[] = [];
    let raf = 0;
    let derniere = 0;
    let visible = true;

    const redimensionne = () => {
      largeur = canvas.clientWidth;
      hauteur = canvas.clientHeight;
      canvas.width = Math.max(1, Math.floor(largeur));
      canvas.height = Math.max(1, Math.floor(hauteur));
      colonnes = Math.ceil(largeur / (FONT + 2));
      gouttes = Array.from({ length: colonnes }, () => Math.random() * -60);
    };
    redimensionne();
    const obs = new ResizeObserver(redimensionne);
    obs.observe(canvas);

    const trame = () => {
      ctx.fillStyle = "rgba(2, 6, 3, 0.14)";
      ctx.fillRect(0, 0, largeur, hauteur);
      ctx.font = `${FONT}px Consolas, monospace`;
      for (let i = 0; i < colonnes; i++) {
        const x = i * (FONT + 2);
        const y = gouttes[i] * FONT;
        ctx.fillStyle =
          Math.random() < 0.06 ? "rgba(190, 255, 205, 0.95)" : "rgba(0, 255, 65, 0.62)";
        ctx.fillText(GLYPHES[(Math.random() * GLYPHES.length) | 0], x, y);
        if (y > hauteur && Math.random() > 0.976) {
          gouttes[i] = Math.random() * -40;
        }
        gouttes[i]++;
      }
    };

    const boucle = (t: number) => {
      raf = requestAnimationFrame(boucle);
      if (!visible || t - derniere < PAS_MS) return;
      derniere = t;
      trame();
    };

    const surVisibilite = () => {
      visible = document.visibilityState === "visible";
    };
    document.addEventListener("visibilitychange", surVisibilite);

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      // Trame statique : l'esthetique sans l'animation.
      ctx.fillStyle = "#020603";
      ctx.fillRect(0, 0, largeur, hauteur);
      ctx.font = `${FONT}px Consolas, monospace`;
      for (let i = 0; i < colonnes; i++) {
        for (let j = 0; j < 20; j++) {
          ctx.fillStyle = "rgba(0, 255, 65, 0.25)";
          ctx.fillText(GLYPHES[(Math.random() * GLYPHES.length) | 0], i * (FONT + 2), j * FONT);
        }
      }
    } else {
      raf = requestAnimationFrame(boucle);
    }

    return () => {
      cancelAnimationFrame(raf);
      obs.disconnect();
      document.removeEventListener("visibilitychange", surVisibilite);
    };
  }, []);

  return (
    <canvas
      ref={ref}
      className={background ? "matrix-rain matrix-rain-fond" : "matrix-rain"}
      aria-hidden="true"
    />
  );
}
