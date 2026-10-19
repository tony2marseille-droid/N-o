# -*- coding: utf-8 -*-
"""Worker Python residant — modele d'execution emprunte a OpenJarvis.

Un seul process chaud execute les operations frequentes du pont (outils,
specs, agents, routage). Les modules restent importes une seule fois :
plus de cout de demarrage Python (1-4 s) par appel.

Protocole : une ligne JSON par requete sur stdin, une ligne JSON par
reponse sur stdout. Les print() des outils sont captures et ne polluent
jamais le protocole. Le serveur Rust relance le worker s'il tombe.
"""
import contextlib
import io
import json
import sys

_REAL_OUT = sys.stdout


def _out(resp: dict) -> None:
    _REAL_OUT.write(json.dumps(resp, default=str) + "\n")
    _REAL_OUT.flush()


def _handle(req: dict):
    op = req.get("op", "")
    if op == "tool":
        from backend.tools.executor import PermissionManager, execute_tool

        return execute_tool(
            str(req.get("name", "")),
            req.get("args") or {},
            PermissionManager(str(req.get("level", "USER"))),
        )
    if op == "specs":
        from backend.tools.executor import ollama_tool_specs

        return ollama_tool_specs(list(req.get("names") or []))
    if op == "agent_info":
        from backend.core.agents import get_agent

        a = get_agent(str(req.get("id", "")))
        if a is None:
            return {"tools": [], "system": "", "rag": False}
        return {
            "tools": list(a.tools or []),
            "system": a.system_prompt or "",
            "rag": bool(a.rag),
        }
    if op == "route":
        from backend.core.router import route_agent

        r = route_agent(str(req.get("message", "")))
        return r[0] if isinstance(r, (list, tuple)) else r
    if op == "reload":
        for m in [m for m in sys.modules if m.startswith("backend.")]:
            del sys.modules[m]
        return "ok"
    if op == "code":
        ns = {"__name__": "__main__"}
        exec(compile(str(req.get("code", "")), "<worker>", "exec"), ns)
        return ""
    raise ValueError(f"operation inconnue: {op}")


def main() -> None:
    # Prechauffage : les imports du pont sont faits une seule fois, au
    # demarrage du serveur (le premier appel utilisateur est deja chaud).
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            import backend.tools.executor  # noqa: F401
            from backend.core.agents import get_agent  # noqa: F401
            from backend.core.router import route_agent  # noqa: F401
        except Exception:
            pass
    for ligne in sys.stdin:
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            req = json.loads(ligne)
        except Exception:
            continue
        rid = req.get("id")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                resultat = _handle(req)
            if req.get("op") == "code":
                resultat = buf.getvalue()
            _out({"id": rid, "ok": True, "out": resultat})
        except Exception as exc:
            _out({"id": rid, "ok": False, "err": f"{exc}"})


if __name__ == "__main__":
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
