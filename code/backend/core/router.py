"""Routage automatique des questions vers le modele le plus adapte.

Logique : detecter l'intention de la question puis choisir le meilleur
modele installe selon la capacite requise, en privilegiant le plus gros
modele pour les intentions exigeantes (code, fait precis, raisonnement)
et le plus rapide pour la conversation simple.
"""

import re

CODING_HINTS = re.compile(
    r"\b(code|coder|codage|programme|programmation|developper|développer|python|"
    r"javascript|typescript|typescript|java\b|c\+\+|c#|rust|golang|sql|html|css|"
    r"react|vue|angular|node|django|flask|fastapi|docker|git\b|github|"
    r"fonction|classe|methode|méthode|algorithme|bug|debug|deboguer|déboguer|"
    r"exception|traceback|compil|script|api|rest|json|regex|refactor|"
    r"ide\b|vs ?code|terminal|commande shell|powershell|bash)\b",
    re.IGNORECASE,
)
FACTUAL_HINTS = re.compile(
    r"^\s*(c(?:\s*')?\s*est\s*quoi|qu(?:\s*')?\s*est[\s'-]*ce que|qui (?:est|etait|était)|"
    r"que (?:signifie|veut dire)|quelle(?:s)? (?:sont|est)|o[uù] se trouve|"
    r"comment fonctionne|pourquoi\b|definis?|definition|définition|explique[- ]moi|"
    r"parle[- ]moi (?:de|du|des|d')|donne[- ]moi des informations?|histoire du|liste des)\b",
    re.IGNORECASE,
)
REASONING_HINTS = re.compile(
    r"\b(demontre|démontre|prouve|raisonnement|logique|enigme|énigme|resolve|résous|"
    r"calcul complexe|analyse approfondie|strategie|stratégie|compare\s+\w+\s+et\b|"
    r"avantages et inconvénients|avantages et inconvenients|maths?\b|equation|équation)\b",
    re.IGNORECASE,
)
MATH_HINTS = re.compile(
    r"\b(combien font|calcule|résultat de|resultat de|\d+\s*[\+\-\*x×\/÷]\s*\d+)\b",
    re.IGNORECASE,
)
SUMMARY_HINTS = re.compile(
    r"\b(resume|résume|résumé|synth[eé]tise|tldr|en bref|points cles|points clés)\b",
    re.IGNORECASE,
)
CREATIVE_HINTS = re.compile(
    r"\b(poeme|poème|poesie|poésie|chanson|histoire|récit|recit|conte|blague|"
    r"haiku|sonnet|nouvelle courte|invente[- ]moi)\b",
    re.IGNORECASE,
)
SMALLTALK_RE = re.compile(
    r"^\s*(salut|bonjour|bonsoir|coucou|hello|hi\b|merci|au revoir|ça va|ca va|"
    r"hey\b|yo\b|super|ok\b|d'accord)\b[\s!.?]*$",
    re.IGNORECASE,
)
CAD3D_HINTS = re.compile(
    r"\b(imprim\w*\s*3\s*d|impression\s+3\s*d|imprime[-\s]?moi|\.stl\b|\bstl\b|"
    r"g-?code\b|\bscad\b|slicer\b|tranch\w+|"
    r"(?:objet|mod[eè]le|pi[eè]ce|design)\s+(?:en\s+)?3\s*d(?:\b|$)|"
    r"3\s*d\s*(?:objet|mod[eè]le|pi[eè]ce)|con[cç]ois[-\s]?moi\s+un)\b",
    re.IGNORECASE,
)
DOCS_HINTS = re.compile(
    r"\b(mes\s+documents?|le\s+(?:document|pdf|rapport|contrat|facture)|"
    r"dans\s+(?:le|mon)\s+(?:document|pdf|fichier\s+(?:pdf|word|excel)))\b",
    re.IGNORECASE,
)
DEV_ACTION_HINTS = re.compile(
    r"\b(?:cr[ée]e[rz]?|g[ée]n[eè]re[rz]?|écris|ecris|ajoute|modifie|corrige|refactore|"
    r"compile|exécute|execute|lance|déploie|deploie|installe)\b[^.?!]*\b"
    r"(?:script|programme|fonction|classe|code|application|app\b|site|page|api|test|"
    r"fichier|commande)\b|\bdebug\b|\bdéboguer\b",
    re.IGNORECASE,
)
NEWS_HINTS = re.compile(
    r"\b(actualit\w*|nouvelles?\b|news\b|derni[eè]res?\s+(?:nouvelle|version|info)|"
    r"r[eé]cemment|aujourd'hui|en\s+ce\s+moment|cette\s+semaine|m[eé]t[eé]o\b|"
    r"prix\s+(?:de|du|des|d')|cours\s+(?:de|du)|sorti|publi[eé]|date\s+de\s+sortie)\b",
    re.IGNORECASE,
)

CAPABILITY_PRIORITY = {
    "coding": ["coding", "general"],
    "reasoning": ["raisonnement", "general"],
    "factual": ["connaissance", "general"],
    "summary": ["rapide", "general"],
    "math": ["general", "coding"],
    "creative": ["general"],
    "chat": ["rapide", "general"],
}


def _size_value(model: dict) -> float:
    size = model.get("parameter_size", "") or model.get("size_label", "")
    digits = re.sub(r"[^\d.]", "", str(size))
    try:
        return float(digits) if digits else 999.0
    except ValueError:
        return 999.0


def classify(message: str) -> str:
    msg = message.strip()
    if len(msg) < 40 and SMALLTALK_RE.match(msg):
        return "chat"
    if CREATIVE_HINTS.search(msg):
        return "creative"
    if MATH_HINTS.search(msg):
        return "math"
    if not re.search(r"\bcodes?\s+posta(?:l|ux)\b", msg, re.IGNORECASE):
        if CODING_HINTS.search(msg):
            return "coding"
    if SUMMARY_HINTS.search(msg):
        return "summary"
    if REASONING_HINTS.search(msg):
        return "reasoning"
    if FACTUAL_HINTS.search(msg):
        return "factual"
    return "general"


def route_agent(message: str) -> tuple[str, str]:
    """Choisit l'agent automatiquement selon le type de demande."""
    msg = message.strip()
    if CAD3D_HINTS.search(msg):
        return "cad3d", "agent auto: conception/impression 3D"
    if DOCS_HINTS.search(msg):
        return "docs", "agent auto: recherche documentaire"
    if DEV_ACTION_HINTS.search(msg) or (
        CODING_HINTS.search(msg) and len(msg) > 60
    ):
        return "dev", "agent auto: developpement/action code"
    return "general", ""


def wants_web(message: str) -> bool:
    """True si la question gagne a etre enrichie par une recherche web."""
    msg = message.strip().lower()
    if NEWS_HINTS.search(msg):
        return True
    if classify(message) == "factual" and re.search(
        r"\b(20\d\d|aujourd|dernier|r[eé]cent|actuel)\b", msg
    ):
        return True
    return False


def route_model(message: str, installed_models: list[dict]) -> tuple[str, str]:
    intent = classify(message)
    priorities = CAPABILITY_PRIORITY.get(intent, ["general"])
    eligible = [m for m in installed_models if m.get("tag")]

    for capability in priorities:
        matches = [
            m
            for m in eligible
            if capability in (m.get("capabilities") or [])
            and "embeddings" not in (m.get("capabilities") or [])
            and _size_value(m) <= 15
        ]
        if not matches:
            continue
        if intent == "creative":
            gemmas = [m for m in matches if m["tag"].startswith("gemma")]
            best = max(gemmas if gemmas else matches, key=_size_value)
        elif intent in ("chat", "summary"):
            best = min(matches, key=_size_value)
        else:
            best = max(matches, key=_size_value)
        return best["tag"], (
            f"intention={intent}, capacite={capability}, taille={_size_value(best):g}B"
        )

    fallback_pool = [m for m in eligible if _size_value(m) <= 15] or eligible
    if intent == "chat" and fallback_pool:
        fallback = min(fallback_pool, key=_size_value)
    else:
        fallback = max(fallback_pool, key=_size_value) if fallback_pool else {"tag": ""}
    return fallback["tag"], f"intention={intent}, repli"
