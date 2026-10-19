# -*- coding: utf-8 -*-
import base64
import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from backend.core.security import (
    commande_autorisee as _commande_bloquee,
    extension_autorisee_ecriture as _extension_bloquee,
    verifier_acces as _verifier_acces,
    charger_regles,
    journaliser,
)
from backend.tools.common import (_CMD_FORBIDDEN_CHARS, _SANDBOX_LOCK,
    _est_sous, _resolve_sandbox, _sandbox_cfg)


def tool_list_dir(path: str) -> str:
    p, refus = _resolve_sandbox(path, "list_dir", ecriture=False)
    if refus:
        return refus
    if not p.exists():
        return f"Chemin introuvable: {p}"
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    lines = []
    for entry in entries[:200]:
        kind = "DIR " if entry.is_dir() else "FILE"
        size = entry.stat().st_size if entry.is_file() else 0
        lines.append(f"{kind}  {size:>12}  {entry.name}")
    return "\n".join(lines) if lines else "(vide)"


def tool_read_file(path: str) -> str:
    p, refus = _resolve_sandbox(path, "read_file", ecriture=False)
    if refus:
        return refus
    if not p.is_file():
        return f"Fichier introuvable: {p}"
    data = p.read_text("utf-8", errors="replace")
    if len(data) > 8000:
        return data[:8000] + f"\n...[tronque, {len(data)} caracteres au total]"
    return data


def tool_write_file(path: str, content: str) -> str:
    p, refus = _resolve_sandbox(path, "write_file", ecriture=True)
    if refus:
        return refus
    blocage_ext = _extension_bloquee(path)
    if blocage_ext:
        blocage_ext += " (sandbox: extension executable/script interdite)"
        journaliser("tool_denied", {"tool": "write_file", "path": str(p),
                                    "reason": blocage_ext})
        return blocage_ext
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Ecrit: {p} ({len(content)} caracteres)"



def tool_run_command(command: str) -> str:
    cfg = _sandbox_cfg()
    cmd = (command or "").strip()
    if not cmd:
        return "refusé: commande vide"
    for ch in _CMD_FORBIDDEN_CHARS:
        if ch in cmd:
            msg = f"caractere interdit en sandbox: {ch!r}"
            journaliser("tool_denied", {"tool": "run_command",
                                        "command": cmd[:200], "reason": msg})
            return f"refusé: {msg} (pas de pipelines/redirections en sandbox)"
    try:
        parts = shlex.split(cmd, posix=False)
    except ValueError as exc:
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200],
                                    "reason": f"parsing KO: {exc}"})
        return f"refusé: ligne de commande non parseable ({exc})"
    parts = [p.strip('"') for p in parts if p.strip()]
    if not parts:
        return "refusé: commande vide"
    head = parts[0].lstrip(".").lower()
    nom_base = Path(head).name.lower()
    if nom_base in cfg["cmd_deny"] or head in cfg["cmd_deny"]:
        msg = f"executable interdit en sandbox: {parts[0]}"
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200], "reason": msg})
        return f"refusé: {msg}"
    if head not in cfg["cmd_allow"] and nom_base not in cfg["cmd_allow"]:
        msg = (f"executable hors liste blanche: {parts[0]} "
               f"(autorisé: {', '.join(cfg['cmd_allow'])})")
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200], "reason": msg})
        return f"refusé: {msg}"
    if head in ("dir", "type"):
        parts = ["cmd.exe", "/d", "/c"] + parts
    blocage = _commande_bloquee(cmd)
    if blocage:
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200],
                                    "reason": "motif securite blocage"})
        return blocage
    racine = cfg["root"]
    try:
        racine.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        with _SANDBOX_LOCK:
            proc = subprocess.run(
                parts,
                shell=False,
                cwd=str(racine),
                capture_output=True,
                text=True,
                timeout=cfg["timeout_s"],
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired:
        msg = f"TIMEOUT (> {cfg['timeout_s']}s)"
        journaliser("tool_denied", {"tool": "run_command",
                                    "command": cmd[:200], "reason": msg})
        return f"Erreur: timeout sandbox {msg}, process arrete"
    except FileNotFoundError:
        return f"refusé: executable introuvable: {parts[0]}"
    out = ((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr.strip() else "")).strip()
    cap = cfg["max_output"]
    if len(out) > cap:
        out = out[:cap] + f"\n...[sortie tronquee a {cap} caracteres]"
    return f"[code {proc.returncode}]\n{out if out else '(aucune sortie)'}"



def tool_check_code(code: str, langage: str = "python", executer: bool = False) -> str:
    """Agent de controle : valide la syntaxe et peut executer en sandbox."""
    import ast
    import subprocess
    import sys as _sys
    import tempfile

    code = (code or "").strip()
    if not code:
        return "Erreur: code vide"
    langage = (langage or "python").lower()
    rapports = []

    if langage in ("python", "py"):
        try:
            ast.parse(code)
            rapports.append("Syntaxe Python: OK")
        except SyntaxError as exc:
            return (
                f"ECHEC SYNTAXE Python ligne {exc.lineno}: {exc.msg}\n"
                f">>> {exc.text.strip() if exc.text else ''}"
            )
        if executer:
            try:
                proc = subprocess.run(
                    [_sys.executable, "-X", "utf8", "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cwd=str(Path(tempfile.gettempdir())),
                )
                sortie = (proc.stdout or "").strip()[:2000]
                err = (proc.stderr or "").strip()[:1000]
                rapports.append(
                    f"Execution sandbox: exit {proc.returncode}"
                    + (f"\nSortie:\n{sortie}" if sortie else "")
                    + (f"\nErreurs:\n{err}" if err else "")
                )
            except subprocess.TimeoutExpired:
                rapports.append("Execution sandbox: TIMEOUT (>15s), code probablement bloquant")
    elif langage in ("javascript", "js", "node"):
        f = Path(tempfile.gettempdir()) / "_check_code.js"
        f.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            ["node", "--check", str(f)], capture_output=True, text=True, timeout=20
        )
        if proc.returncode == 0:
            rapports.append("Syntaxe JavaScript: OK")
        else:
            return f"ECHEC SYNTAXE JS:\n{(proc.stderr or '').strip()[:800]}"
    elif langage == "json":
        import json as _json

        try:
            _json.loads(code)
            rapports.append("JSON: valide")
        except ValueError as exc:
            return f"JSON INVALIDE: {exc}"
    else:
        return f"Langage non supporte: {langage} (python, javascript, json)"

    return "\n".join(rapports)
