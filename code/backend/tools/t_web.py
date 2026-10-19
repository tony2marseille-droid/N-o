# -*- coding: utf-8 -*-
import os
import re

from backend.tools.common import html_unescape, _llm_rapide


def tool_web_search(query: str) -> str:
    import time

    import httpx

    query = (query or "").strip()[:300]
    if not query:
        return "Erreur: requete vide"
    ua = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Referer": "https://html.duckduckgo.com/",
    }

    def parse_ddg(text: str) -> list[str]:
        out: list[str] = []
        pairs = re.findall(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.S
        )
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', text, re.S)
        for i, (url, title) in enumerate(pairs[:6]):
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                from urllib.parse import unquote

                url = unquote(m.group(1))
            clean = html_unescape(re.sub(r"<[^>]+>", "", title)).strip()
            snippet = (
                html_unescape(re.sub(r"<[^>]+>", "", snips[i])).strip()[:220]
                if i < len(snips)
                else ""
            )
            out.append(f"{i+1}. {clean}\n   URL: {url}\n   {snippet}")
        return out

    results: list[str] = []
    statuses: list[str] = []
    try:
        time.sleep(1.0)
        resp = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=ua,
            timeout=14,
            follow_redirects=True,
        )
        statuses.append(f"POST {resp.status_code}/{len(resp.text)}o")
        results = parse_ddg(resp.text)
        if not results:
            time.sleep(2.5)
            resp = httpx.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=ua,
                timeout=14,
                follow_redirects=True,
            )
            statuses.append(f"GET {resp.status_code}/{len(resp.text)}o")
            results = parse_ddg(resp.text)
    except Exception as exc:
        statuses.append(f"EXC {type(exc).__name__}: {exc}")
    if not results:
        short = " ".join(query.split()[:3])
        try:
            resp = httpx.get(
                "https://fr.wikipedia.org/w/api.php",
                params={"action": "opensearch", "search": short, "limit": 4, "format": "json"},
                headers={"User-Agent": ua["User-Agent"]},
                timeout=10,
            )
            data = resp.json()
            for title, link in zip(data[1], data[3]):
                results.append(f"- {title}\n   URL: {link}")
        except Exception as exc:
            return f"Aucun resultat web ({exc}) [{'; '.join(statuses)}]"
    return (
        "\n\n".join(results)
        if results
        else f"Aucun resultat web [{'; '.join(statuses)}]"
    )



def tool_web_fetch(url: str) -> str:
    import httpx

    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        return "Erreur: URL invalide (http/https uniquement)"
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=15,
            follow_redirects=True,
        )
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", resp.text, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", html_unescape(text)).strip()
        return f"[{url}]\n{text[:3500]}"
    except Exception as exc:
        return f"Erreur de recuperation: {exc}"



def tool_deep_research(sujet: str, profondeur: int = 2) -> str:
    """Agent de recherche : multi-requetes web + lecture de pages + synthese."""
    sujet = (sujet or "").strip()[:300]
    if not sujet:
        return "Erreur: sujet vide"
    profondeur = max(1, min(int(profondeur or 2), 3))

    brut = _llm_rapide(
        f"Genere exactement {profondeur + 1} requetes de recherche web courtes et "
        f"complementaires (une par ligne, sans numeros ni tirets) pour repondre a : {sujet}",
        90,
    )
    requetes = [l.strip(" -*0123456789.") for l in brut.splitlines() if l.strip()]
    requetes = [r for r in requetes if len(r) > 5][: profondeur + 1] or [sujet]
    if sujet not in requetes:
        requetes.insert(0, sujet)

    resultats_bruts = []
    urls_vedettes = []
    import re as _re

    motif_url = _re.compile(r"https?://[^\s)\]>\"]+")
    import time as _time

    for i, req in enumerate(requetes):
        if i:
            _time.sleep(1.5)
        res = tool_web_search(req)
        resultats_bruts.append(f"### Requete: {req}\n{res[:2500]}")
        trouvées = motif_url.findall(res)
        if trouvées:
            urls_vedettes.extend(u for u in trouvées if "duckduckgo" not in u)

    pages = []
    for url in urls_vedettes[:profondeur]:
        contenu = tool_web_fetch(url)
        pages.append(f"### Source: {url}\n{contenu[:3500]}")

    synthese_in = "\n\n".join(resultats_bruts + pages)[:12000]
    synthese = _llm_rapide(
        f"Synthetise en francais (10-15 phrases max, points cles structurés) la reponse "
        f"a cette question a partir des extraits suivants. Cite les URLs utiles.\n\n"
        f"Question: {sujet}\n\nExtraits:\n{synthese_in}",
        240,
    )
    if not synthese:
        synthese = "(Synthese LLM indisponible - resultats bruts ci-dessous)"

    return (
        f"[deep_research | {len(requetes)} requetes | {len(pages)} pages lues]\n\n"
        f"SYNTHESE:\n{synthese}\n\nSOURCES PRINCIPALES:\n"
        + "\n".join(f"- {u}" for u in dict.fromkeys(urls_vedettes))
    )[:6000]
