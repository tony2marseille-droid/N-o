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


_SANDBOX_LOCK = threading.Lock()

_CMD_FORBIDDEN_CHARS = ("|", "&", ";", ">", "<", "`", "$(")

_CMD_DENY_DEFAULT = [
    "del", "rmdir", "format", "reg", "schtasks", "net", "sc",
    "powershell", "pwsh", "cmd", "curl", "wget", "ssh", "scp", "taskkill",
]

_CMD_ALLOW_DEFAULT = [
    "python", "python.exe", "node", "node.exe", "git", "git.exe",
    "openscad", "openscad.exe", "dir", "type", "findstr", "findstr.exe",
]


def _est_sous(chemin: Path, racine: Path) -> bool:
    try:
        chemin.relative_to(racine)
        return True
    except ValueError:
        return False


def _sandbox_cfg() -> dict:
    regles = charger_regles()
    return {
        "root": Path(regles.get("sandbox_root", r"E:\ia\workspace")),
        "read_roots": [Path(p) for p in regles.get(
            "sandbox_read_roots",
            [r"E:\ia\workspace", r"E:\ia\data\documents"])],
        "cmd_allow": [a.lower() for a in regles.get("cmd_allow", _CMD_ALLOW_DEFAULT)],
        "cmd_deny": [d.lower() for d in regles.get("cmd_deny", _CMD_DENY_DEFAULT)],
        "timeout_s": int(regles.get("timeout_s", 60)),
        "max_output": int(regles.get("max_output", 20000)),
    }


def _refus_sandbox(outil: str, chemin: str, raison: str, ecriture: bool) -> str:
    journaliser("read_refused" if not ecriture else "tool_denied",
                {"tool": outil, "path": chemin, "reason": raison, "write": ecriture})
    return f"refusé: {raison} ({outil}: {chemin})"


def _resolve_sandbox(path: str, outil: str, ecriture: bool) -> tuple:
    """(Path, None) si le chemin resolue est avec un root autorise, sinon (None, refus)."""
    cfg = _sandbox_cfg()
    brut = str(path or "")
    if not brut.strip():
        return None, _refus_sandbox(outil, brut, "chemin vide", ecriture)
    p = Path(brut).expanduser()
    if not p.is_absolute():
        return None, _refus_sandbox(
            outil, brut, "chemin relatif non autorise (utilise un chemin absolu sous le workspace)", ecriture)
    racines = [cfg["root"]] if ecriture else cfg["read_roots"]
    try:
        resol = p.resolve()
    except OSError:
        return None, _refus_sandbox(outil, brut, "resolution impossible (lien/junction)", ecriture)
    for r in racines:
        try:
            r_resol = r.resolve()
        except OSError:
            continue
        if _est_sous(resol, r_resol):
            return resol, None
    journaliser("acces_refuse" if ecriture else "lecture_refusee",
                {"tool": outil, "path": str(resol), "write": ecriture})
    return None, _refus_sandbox(
        outil, brut,
        f"chemin hors des racines autorisees {[str(r) for r in racines]}", ecriture)



def _html_unescape(s: str) -> str:
    import html as _html

    return _html.unescape(s)


html_unescape = _html_unescape



_OPENCAD_PATHS = [
    r"E:\ia\tools\OpenSCAD\openscad-2021.01\openscad.exe",
    r"C:\Program Files\OpenSCAD\openscad.exe",
    r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
]
_SLICER_PATHS = {
    "prusa": [
        r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer-console.exe",
        r"C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe",
    ],
    "orca": [
        r"C:\Program Files\OrcaSlicer\orca-console.exe",
        r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
    ],
    "cura": [
        r"C:\Program Files\Ultimaker Cura 5*\CuraEngine.exe",
    ],
    "bambu": [
        r"C:\Program Files\Bambu Studio\bambu-studio-console.exe",
        r"C:\Program Files\Bambu Studio\BambuStudio.exe",
    ],
}


def _find_exe(patterns: list[str]) -> str:
    import glob as _glob

    env_override = os.environ.get("LOCALAI_OPENSCAD", "") or os.environ.get("LOCALAI_SLICER", "")
    if env_override and Path(env_override).exists():
        return env_override
    for p in patterns:
        if not p:
            continue
        if "*" in p:
            found = sorted(_glob.glob(p))
            if found:
                return found[-1]
            continue
        if Path(p).exists():
            return p
    return ""



def _find_vision_model() -> str:
    import httpx

    try:
        base = os.environ.get("LOCALAI_OLLAMA_URL", "http://127.0.0.1:11434")
        tags = httpx.get(f"{base}/api/tags", timeout=6).json().get("models", [])
        names = [m.get("name", "") for m in tags]
    except Exception:
        return ""
    patterns = [
        "qwen2.5vl", "qwen2-vl", "qvq", "llava",
        "minicpm-v", "llama3.2-vision", "gemma3", "moondream",
    ]
    for pat in patterns:
        for n in names:
            if pat in n.lower():
                return n
    return ""



def _llm_rapide(prompt: str, timeout_s: int = 120) -> str:
    """Appel court au modele auxiliaire local pour synthese/variations."""
    import httpx as _httpx

    try:
        rep = _httpx.post(
            "http://127.0.0.1:11434/api/generate",
            json={"model": "gemma3:4b", "prompt": prompt, "stream": False},
            timeout=timeout_s,
        ).json()
        return rep.get("response", "")
    except Exception:
        return ""
