# -*- coding: utf-8 -*-
"""Outils Systeme : meteo, stats PC, volume, apps, ecran.

Toutes les donnees restent locales (meteo : API Open-Meteo, gratuite, sans
cle). Actions PC (volume/apps/ecran) au niveau POWER (confirmation par
l'utilisateur dans la conversation avant execution).
"""
import re
import subprocess
from pathlib import Path

import httpx

from backend.core.security import verifier_acces as _verifier_acces


# ---- Météo (Open-Meteo : gratuit, sans clé) ----

def tool_meteo(ville: str) -> str:
    """Meteo actuelle + aujourd'hui/demain pour une ville (Open-Meteo, sans cle)."""
    ville = (ville or "").strip()[:80]
    if not ville:
        return "Erreur: ville vide"
    try:
        geo = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": ville, "count": 1, "language": "fr"},
            timeout=15,
        ).json()
        res = geo.get("results") or []
        if not res:
            return f"Aucune ville trouvee pour « {ville} »"
        r = res[0]
        nom = r.get("name", ville)
        pays = r.get("country", "")
        lat, lon = r.get("latitude"), r.get("longitude")
        meteo = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
                "forecast_days": 2,
            },
            timeout=15,
        ).json()
        codes = {
            0: "ciel degage", 1: "peu nuageux", 2: "partiellement nuageux",
            3: "couvert", 45: "brouillard", 48: "brouillard givrant",
            51: "bruine legere", 53: "bruine", 61: "pluie legere",
            63: "pluie", 65: "pluie forte", 71: "neige legere",
            73: "neige", 75: "neige forte", 80: "averses faibles",
            81: "averses", 82: "averses violentes", 95: "orage",
            96: "orage avec grele", 99: "orage violent avec grele",
        }
        actuel = meteo.get("current", {})
        t = actuel.get("temperature_2m", "?")
        ressenti = actuel.get("apparent_temperature", "?")
        code = codes.get(int(actuel.get("weather_code", -1)), "inconnu")
        vent = actuel.get("wind_speed_10m", "?")
        hum = actuel.get("relative_humidity_2m", "?")
        lignes = [
            f"Meteo {nom} ({pays}) :",
            f"- Actuellement : {t}°C (ressenti {ressenti}°C), {code}, "
            f"vent {vent} km/h, humidite {hum}%",
        ]
        d = meteo.get("daily", {})
        for i in range(min(2, len(d.get("time", [])))):
            jour = "aujourd'hui" if i == 0 else "demain"
            c = codes.get(int(d.get("weather_code", [-1, -1])[i]), "inconnu")
            lignes.append(
                f"- {jour} : min {d.get('temperature_2m_min', [0, 0])[i]}°C, "
                f"max {d.get('temperature_2m_max', [0, 0])[i]}°C, {c}"
            )
        return "\n".join(lignes)
    except Exception as exc:
        return f"Erreur meteo: {exc}"


# ---- Statistiques du PC ----

def tool_system_stats() -> str:
    """Statistiques du PC : CPU, RAM, disques, GPU (local)."""
    import psutil

    lignes = [
        f"CPU : {psutil.cpu_percent(interval=1)} % utilise "
        f"({psutil.cpu_count(logical=True)} cœurs logiques)",
        f"RAM : {psutil.virtual_memory().percent} % utilisee "
        f"({psutil.virtual_memory().used // 2**20} Mo / {psutil.virtual_memory().total // 2**20} Mo)",
    ]
    for p in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(p.mountpoint)
            lignes.append(f"Disque {p.device} : {u.percent} % utilise "
                          f"({u.free // 2**30} Go libres)")
        except Exception:
            pass
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            lignes.append(f"GPU : {r.stdout.strip()}")
        else:
            lignes.append("GPU : nvidia-smi indisponible")
    except Exception:
        lignes.append("GPU : nvidia-smi indisponible")
    lignes.append(f"Demarrage du systeme : {psutil.boot_time():.0f} (epoch)")
    return "\n".join(lignes)


# ---- Volume (pycaw) ----

def _volume_actuel() -> tuple[int, bool]:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL

    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    vol = cast(interface, POINTER(IAudioEndpointVolume))
    niveau = int(round(vol.GetMasterVolumeLevelScalar() * 100))
    muet = bool(vol.GetMute())
    return niveau, muet


def _volume_fixer(niveau: int):
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL

    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    vol = cast(interface, POINTER(IAudioEndpointVolume))
    vol.SetMasterVolumeLevelScalar(max(0, min(100, niveau)) / 100, None)


def tool_volume(action: str) -> str:
    """Volume systeme : 'up', 'down', 'muet', '35' (niveau fixe) ou vide (etat)."""
    action = (action or "").strip().lower()
    try:
        niveau, muet = _volume_actuel()
        if not action or action in ("etat", "status"):
            return f"Volume : {niveau} % {'(muet)' if muet else ''}"
        if action == "muet":
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL

            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = cast(interface, POINTER(IAudioEndpointVolume))
            vol.SetMute(not muet, None)
            return f"Son {'coupe' if not muet else 'retabli'} (volume {niveau} %)"
        if action in ("up", "monte", "plus"):
            _volume_fixer(niveau + 10)
            return f"Volume monte a {min(100, niveau + 10)} %"
        if action in ("down", "baisse", "moins"):
            _volume_fixer(niveau - 10)
            return f"Volume baisse a {max(0, niveau - 10)} %"
        if re.fullmatch(r"\d{1,3}", action):
            _volume_fixer(int(action))
            return f"Volume regle a {int(action)} %"
        return f"Action volume inconnue: « {action} » (up/down/muet/N)"
    except Exception as exc:
        return f"Erreur volume: {exc}"


# ---- Ouvrir une application ----

_APPS = {
    "notepad": ("Bloc-notes", "notepad.exe"),
    "bloc notes": ("Bloc-notes", "notepad.exe"),
    "calculatrice": ("Calculatrice", "calc.exe"),
    "calculateur": ("Calculatrice", "calc.exe"),
    "explorateur": ("Explorateur", "explorer.exe"),
    "chrome": ("Google Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    "firefox": ("Firefox", r"C:\Program Files\Mozilla Firefox\firefox.exe"),
    "edge": ("Edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    "vlc": ("VLC", r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
    "invite de commandes": ("Invite de commandes", "cmd.exe"),
    "powershell": ("PowerShell", "powershell.exe"),
    "paint": ("Paint", "mspaint.exe"),
    "gestionnaire des taches": ("Gestionnaire des taches", "taskmgr.exe"),
    "parametres": ("Parametres", "ms-settings:"),
    "reglages": ("Parametres", "ms-settings:"),
}


def tool_open_app(nom: str) -> str:
    """Ouvre une application connue (notepad, calculatrice, chrome...)."""
    nom = (nom or "").strip().lower()[:60]
    for cle, (label, cmd) in _APPS.items():
        if cle in nom or nom in cle:
            try:
                if cmd.endswith(":"):
                    subprocess.Popen(["start", cmd], shell=True)
                else:
                    subprocess.Popen([cmd])
                return f"{label} ouvert."
            except Exception as exc:
                return f"Erreur ouverture {label}: {exc}"
    return (f"Application « {nom} » inconnue. Applications connues : "
            + ", ".join(sorted({l for l, _ in _APPS.values()})))


# ---- Vision de l'ecran (capture -> modele vision) ----

def tool_capture_ecran(question: str = "") -> str:
    """Capture l'ecran et repond a une question dessus (modele vision local).

    Ex : « c'est quoi cette erreur ? », « lis ce texte », « traduis ceci ».
    """
    from backend.tools.t_image import tool_analyze_image

    chemin = Path("data") / "ecran_capture.png"
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab()
        img.save(str(chemin))
    except Exception as exc:
        return f"Erreur capture ecran: {exc}"
    question = (question or "").strip()[:400]
    if not question:
        question = "Decris ce qui est affiche a l'ecran en 2 phrases."
    return tool_analyze_image(str(chemin), question)
