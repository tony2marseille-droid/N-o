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
    r"3\s*d\s*(?:objet|mod[eè]le|pi[eè]ce)|con[cç]ois[-\s]?moi\s+un|"
    r"mesh\b|maillage|cad\b|openscad|blender)\b",
    re.IGNORECASE,
)
# VOIR une photo/image EXISTANTE (recherche web + telechargement) :
# verbe de consultation + photo/portrait/image de quelque chose.
VOIR_IMAGE_HINTS = re.compile(
    r"\b(?:montre|montrer|affiche|afficher|trouve|trouver|cherche|chercher|"
    r"fais(?:[- ]moi)?\s+voir|montre[- ]moi|fais[- ]moi\s+(?:une\s+)?(?:photo|image)|"
    r"j(?:e\s+)?(?:voudrais|aimerais)\s+voir|a\s+ quoi\s+ressemble|"
    r"une?\s+(?:photo|image|portrait|photo de profil))\s+(?:de|du|des|d')?\s*\w",
    re.IGNORECASE,
)
VOIR_IMAGE_STRONG = re.compile(
    r"\b(montre|montrer|affiche|afficher|trouve|trouver|cherche|chercher|"
    r"voir|regarde|ressemble)\b[^.?!]{0,60}\b(photo|photos|portrait|image|"
    r"images|visage|figure)\b",
    re.IGNORECASE,
)
# MODIFIER une image EXISTANTE (edition img2img via edit_image) :
# verbe de modification + cible visuelle proche (image, photo, deco,
# livree, fond, paquet...). Doit passer AVANT generer_image : la base
# existe deja, on ne doit pas regenerer de zero.
EDIT_IMAGE_HINTS = re.compile(
    r"\b(?:applique|appliquer|superpose|superposer|mets\b|mettre|"
    r"repeins?|repeindre|recolore|recolorer|retouche[rz]?|incruste[rz]?|"
    r"modifie[rz]?|change[rz]?|remplace[rz]?)\b[^.?!]{0,80}\b"
    r"(?:image|photo|deco|d[ée]co|livree|livr[ée]e|couleurs?|fond|style|"
    r"motif|paquet|design|logo|texture)\b",
    re.IGNORECASE,
)
# GENERER une image nouvelle (IA locale).
GEN_IMAGE_HINTS = re.compile(
    r"\b(g[ée]n[eè]re[rz]?|g[ée]n[eè]ration|cr[ée]e[rz]?|dessine|dessiner|"
    r"fais[- ]moi\s+(?:une\s+)?(?:image|deco|d[ée]co|affiche|illustration|vignette|"
    r"banni[eè]re|avatar|sticker|visuel|fond|wallpaper|logo)|"
    r"une?\s+(?:image|illustration|affiche|deco|d[ée]co)\s+(?:de|du|des|sur|th[eè]me))\b"
    r"[^.?!]{0,60}\b(image|images|png|jpg|photo|dessin|affiche|illustration|"
    r"vignette|banni[eè]re|avatar|sticker|visuel|fond|wallpaper|logo|deco|d[ée]co)\b",
    re.IGNORECASE,
)
IMAGE_TOPIC_HINTS = re.compile(
    r"\b(photo|photos|portrait|image|images|dessin|affiche|illustration|"
    r"visage|acteur|actrice|c[eé]l[ée]brit)\b",
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


def _llm_intention(message: str) -> str:
    """Niveau 2 : classifieur LLM leger (qwen3:8b, temp 0) pour les demandes
    ambigues que les regex n'ont pas tranchees. Retourne une intention parmi
    un ensemble ferme, ou "" si indisponible/erreur. ~1-2 s max."""
    import json as _json
    import os as _os
    import urllib.request as _urllib

    prompt = (
        "Classifie cette demande utilisateur en UNE SEULE intention, sans "
        "aucun autre texte. Intentions possibles :\n"
        "- voir_image : l'utilisateur veut VOIR une photo/image EXISTANTE "
        "(recherche d'une photo reelle d'une personne, d'un objet, d'un lieu)\n"
        "- modifier_image : il veut MODIFIER une image existante (appliquer "
        "une livree ou une deco, recolorer, changer le fond, ajouter ou "
        "enlever un element sur une image deja la)\n"
        "- generer_image : il veut faire CREER/DESSINER une nouvelle image\n"
        "- 3d : conception 3D, piece, STL, impression 3D, modélisation\n"
        "- autre : tout le reste (question, code, discussion...)\n"
        f"Demande : {message[:300]}\n"
        "Reponds UNIQUEMENT par un mot : voir_image, modifier_image, "
        "generer_image, 3d ou autre."
    )
    try:
        body = _json.dumps({
            "model": _os.environ.get("LOCALAI_ROUTER_MODEL", "qwen3:8b"),
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "num_predict": 6},
        }).encode("utf-8")
        req = _urllib.Request(
            _os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
            + "/api/chat",
            data=body, headers={"Content-Type": "application/json"},
        )
        with _urllib.urlopen(req, timeout=20) as rep:
            texte = _json.loads(rep.read().decode("utf-8")).get(
                "message", {}).get("content", "").strip().lower()
        for intention in ("voir_image", "modifier_image", "generer_image", "3d", "autre"):
            if intention in texte:
                return intention
    except Exception:
        pass
    return ""


def route_agent(message: str) -> tuple[str, str]:
    """Choisit l'agent automatiquement selon le type de demande.

    Ordre de priorite des regles rapides (niveau 1) :
    1. voir_image : montrer/trouver une photo EXISTANTE (avant 3d : une
       demande de photo d'une personne ne doit jamais finir en mesh)
    2. generer_image : creer une image IA
    3. cad3d / docs / dev (regles historiques)
    Niveau 2 : si le message parle d'image/3d sans qu'aucune regle ne
    tranche, un classifieur LLM leger arbitre (~1-2 s).
    """
    msg = message.strip()

    # 1. VOIR une photo existante (doit passer AVANT generer_image et 3d :
    #    "montre-moi une photo de X" ne doit jamais devenir un mesh).
    if VOIR_IMAGE_STRONG.search(msg):
        return "image", (
            "intention=voir_image (photo existante : web_search + fetch_image, "
            "pas de generation IA)"
        )

    # 2. MODIFIER une image existante (edition : la base existe deja,
    #    edit_image doit etre utilise, pas une regeneration complete).
    if EDIT_IMAGE_HINTS.search(msg):
        return "image", "intention=modifier_image (edition d'une image existante)"

    # 3. GENERER une image IA.
    if GEN_IMAGE_HINTS.search(msg) and not CAD3D_HINTS.search(msg):
        return "image", "intention=generer_image (generation IA locale)"

    # 4. Regles historiques.
    if CAD3D_HINTS.search(msg):
        return "cad3d", "agent auto: conception/impression 3D"
    if DOCS_HINTS.search(msg):
        return "docs", "agent auto: recherche documentaire"
    if DEV_ACTION_HINTS.search(msg) or (
        CODING_HINTS.search(msg) and len(msg) > 60
    ):
        return "dev", "agent auto: developpement/action code"

    # 5. Niveau 2 (LLM) uniquement si le sujet image/3d est present mais
    #    qu'aucune regle n'a tranche (cas ambigus sans verbe clair).
    if IMAGE_TOPIC_HINTS.search(msg) or CAD3D_HINTS.search(msg):
        intention = _llm_intention(msg)
        if intention == "voir_image":
            return "image", "intention=voir_image (classifieur LLM)"
        if intention == "modifier_image":
            return "image", "intention=modifier_image (classifieur LLM)"
        if intention == "generer_image":
            return "image", "intention=generer_image (classifieur LLM)"
        if intention == "3d":
            return "cad3d", "intention=3d (classifieur LLM)"

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
