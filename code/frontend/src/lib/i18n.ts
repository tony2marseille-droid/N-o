// Traductions du shell (v1) — FR/EN complets, ES/DE/IT partiels (repli EN).
export type Lang = "fr" | "en" | "es" | "de" | "it";

export const LANGS: { code: Lang; label: string; flag: string }[] = [
  { code: "fr", label: "Français", flag: "🇫🇷" },
  { code: "en", label: "English", flag: "🇬🇧" },
  { code: "es", label: "Español", flag: "🇪🇸" },
  { code: "de", label: "Deutsch", flag: "🇩🇪" },
  { code: "it", label: "Italiano", flag: "🇮🇹" },
];

const FR: Record<string, string> = {
  "nav.home": "Accueil",
  "nav.chat": "Chat",
  "nav.models": "Modèles",
  "nav.documents": "Documents",
  "nav.settings": "Réglages",
  "nav.workshop": "Ateliers",
  "nav.finetune": "Fine-tune",
  "nav.newChat": "+ Nouvelle conversation",
  "nav.history": "Historique",
  "nav.emptyHistory": "Aucune conversation",
  "status.ollama": "Ollama",
  "status.online": "en ligne",
  "status.offline": "hors ligne",
  "status.gpu": "GPU",
  "status.vram": "VRAM",
  "status.ram": "RAM",
  "status.uptime": "Uptime PC",
  "home.tagline": "Intelligence locale. Aucun nuage.",
  "home.agents": "agents en ligne",
  "home.newChat": "Nouvelle conversation",
  "home.cycle": "Lancer un cycle d'auto-amélioration",
  "home.system": "SYSTÈME",
  "home.auto": "AUTO-AMÉLIORATION",
  "home.models": "MODÈLES & PONT",
  "settings.title": "Réglages",
  "settings.refresh": "Rafraîchir les mesures",
  "settings.language": "Langue de l'assistant",
  "settings.languageHint": "Langue des réponses et de l'interface.",
  "settings.system": "Système",
  "settings.memoryTitle": "Mémoire de l'assistant",
  "settings.memoryHint": "Ces informations sont injectées dans chaque conversation.",
  "settings.memoryPlaceholder": "Ex : Je m'appelle Tony, je code en Python",
  "settings.remember": "Mémoriser",
  "settings.tools": "Niveau d'outils",
  "settings.voice": "Voix (TTS)",
  "settings.voiceTest": "Tester la voix",
  "settings.autoLearn": "Apprentissage automatique",
  "settings.coach": "Auto-amélioration",
  "settings.coachRun": "Lancer le coach maintenant",
  "settings.rules": "Règles actives",
  "settings.feedback": "Retours utilisateur",
  "settings.average": "Moyenne / 5",
  "settings.autoEvals": "Auto-évaluations",
  "settings.saved": "Réglage enregistré",
  "settings.delete": "Supprimer",
  "common.loading": "Chargement…",
  "common.cancel": "Annuler",
  "onboarding.welcome": "Bienvenue dans Néo",
  "onboarding.subtitle": "Votre assistant IA local, adapté à votre machine.",
  "onboarding.chooseLang": "Choisissez votre langue",
  "onboarding.continue": "Continuer",
  "onboarding.detect": "Analyse de votre configuration",
  "onboarding.machine": "Votre machine",
  "onboarding.tier": "Palier",
  "onboarding.recommended": "Modèles recommandés pour votre matériel",
  "onboarding.install": "Installer",
  "onboarding.installed": "Installé",
  "onboarding.skip": "Passer l'installation guidée",
  "onboarding.finish": "Terminer et démarrer",
  "onboarding.ready": "Tout est prêt",
  "onboarding.readyHint": "Néo est configuré pour votre machine. Vous pourrez changer ces réglages plus tard.",
  "onboarding.step": "Étape",
  "onboarding.analyzing": "Détection du matériel en cours…",
  "error.title": "Une erreur est survenue dans cette vue",
  "error.reload": "Recharger",
};

const EN: Record<string, string> = {
  "nav.home": "Home",
  "nav.chat": "Chat",
  "nav.models": "Models",
  "nav.documents": "Documents",
  "nav.settings": "Settings",
  "nav.workshop": "Workshops",
  "nav.finetune": "Fine-tune",
  "nav.newChat": "+ New chat",
  "nav.history": "History",
  "nav.emptyHistory": "No conversations",
  "status.ollama": "Ollama",
  "status.online": "online",
  "status.offline": "offline",
  "status.gpu": "GPU",
  "status.vram": "VRAM",
  "status.ram": "RAM",
  "status.uptime": "PC uptime",
  "home.tagline": "Local intelligence. No cloud.",
  "home.agents": "agents online",
  "home.newChat": "New chat",
  "home.cycle": "Run a self-improvement cycle",
  "home.system": "SYSTEM",
  "home.auto": "SELF-IMPROVEMENT",
  "home.models": "MODELS & BRIDGE",
  "settings.title": "Settings",
  "settings.refresh": "Refresh metrics",
  "settings.language": "Assistant language",
  "settings.languageHint": "Language for answers and interface.",
  "settings.system": "System",
  "settings.memoryTitle": "Assistant memory",
  "settings.memoryHint": "This information is injected into every conversation.",
  "settings.memoryPlaceholder": "E.g. My name is Tony, I code in Python",
  "settings.remember": "Remember",
  "settings.tools": "Tool level",
  "settings.voice": "Voice (TTS)",
  "settings.voiceTest": "Test voice",
  "settings.autoLearn": "Automatic learning",
  "settings.coach": "Self-improvement",
  "settings.coachRun": "Run the coach now",
  "settings.rules": "Active rules",
  "settings.feedback": "User feedback",
  "settings.average": "Average / 5",
  "settings.autoEvals": "Self-evaluations",
  "settings.saved": "Setting saved",
  "settings.delete": "Delete",
  "common.loading": "Loading…",
  "common.cancel": "Cancel",
  "onboarding.welcome": "Welcome to Néo",
  "onboarding.subtitle": "Your local AI assistant, tailored to your machine.",
  "onboarding.chooseLang": "Choose your language",
  "onboarding.continue": "Continue",
  "onboarding.detect": "Analyzing your configuration",
  "onboarding.machine": "Your machine",
  "onboarding.tier": "Tier",
  "onboarding.recommended": "Recommended models for your hardware",
  "onboarding.install": "Install",
  "onboarding.installed": "Installed",
  "onboarding.skip": "Skip guided setup",
  "onboarding.finish": "Finish and start",
  "onboarding.ready": "All set",
  "onboarding.readyHint": "Néo is configured for your machine. You can change this later.",
  "onboarding.step": "Step",
  "onboarding.analyzing": "Detecting hardware…",
  "error.title": "Something went wrong in this view",
  "error.reload": "Reload",
};

const ES: Record<string, string> = {
  "nav.home": "Inicio", "nav.chat": "Chat", "nav.models": "Modelos",
  "nav.documents": "Documentos", "nav.settings": "Ajustes",
  "nav.workshop": "Talleres", "nav.finetune": "Fine-tune",
  "nav.newChat": "+ Nueva conversación", "nav.history": "Historial",
  "nav.emptyHistory": "Sin conversaciones",
  "home.tagline": "Inteligencia local. Sin nube.",
  "onboarding.welcome": "Bienvenido a Néo",
  "onboarding.chooseLang": "Elija su idioma",
  "onboarding.continue": "Continuar", "onboarding.finish": "Terminar",
  "onboarding.skip": "Omitir configuración", "onboarding.install": "Instalar",
};

const DE: Record<string, string> = {
  "nav.home": "Start", "nav.chat": "Chat", "nav.models": "Modelle",
  "nav.documents": "Dokumente", "nav.settings": "Einstellungen",
  "nav.workshop": "Werkstätten", "nav.finetune": "Fine-tune",
  "nav.newChat": "+ Neuer Chat", "nav.history": "Verlauf",
  "nav.emptyHistory": "Keine Unterhaltungen",
  "home.tagline": "Lokale Intelligenz. Keine Cloud.",
  "onboarding.welcome": "Willkommen bei Néo",
  "onboarding.chooseLang": "Wählen Sie Ihre Sprache",
  "onboarding.continue": "Weiter", "onboarding.finish": "Fertig",
  "onboarding.skip": "Anleitung überspringen", "onboarding.install": "Installieren",
};

const IT: Record<string, string> = {
  "nav.home": "Home", "nav.chat": "Chat", "nav.models": "Modelli",
  "nav.documents": "Documenti", "nav.settings": "Impostazioni",
  "nav.workshop": "Laboratori", "nav.finetune": "Fine-tune",
  "nav.newChat": "+ Nuova conversazione", "nav.history": "Cronologia",
  "nav.emptyHistory": "Nessuna conversazione",
  "home.tagline": "Intelligenza locale. Nessun cloud.",
  "onboarding.welcome": "Benvenuto in Néo",
  "onboarding.chooseLang": "Scegli la tua lingua",
  "onboarding.continue": "Continua", "onboarding.finish": "Termina",
  "onboarding.skip": "Salta configurazione", "onboarding.install": "Installa",
};

const DICTS: Record<Lang, Record<string, string>> = { fr: FR, en: EN, es: ES, de: DE, it: IT };

export function t(lang: Lang, key: string): string {
  return DICTS[lang]?.[key] ?? DICTS.en[key] ?? DICTS.fr[key] ?? key;
}

export function normalizeLang(raw: string | undefined | null): Lang {
  const code = (raw || "fr").slice(0, 2).toLowerCase();
  return (["fr", "en", "es", "de", "it"].includes(code) ? code : "fr") as Lang;
}

export function getLang(): Lang {
  try {
    return normalizeLang(localStorage.getItem("neo.lang"));
  } catch {
    return "fr";
  }
}

export function setLang(lang: Lang): void {
  try {
    localStorage.setItem("neo.lang", lang);
  } catch {
    /* ignore */
  }
  document.documentElement.lang = lang;
}
