"""Games installed with the Heroic Games Launcher (GOG, Epic, Amazon): where they are, their Wine
prefix, and starting them through Heroic.

Read from Heroic's own files (its config folder, the Flatpak's too): GOG's gog_store/installed.json
with the titles in store_cache/gog_library.json, Epic's legendaryConfig/legendary/installed.json,
Amazon's nile_config/nile/installed.json with store_cache/nile_library.json, and a game's prefix
from GamesConfig/<id>.json. Nothing is written there. A game is started with Heroic's own link
(heroic://launch?appName=…&runner=…), so it runs with the Wine or Proton, prefix and settings it
has in Heroic."""
import json
import os
import re
import shutil

from .config import HOME

FLATPAK_ID = "com.heroicgameslauncher.hgl"
ROOTS = [os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "heroic"),
         os.path.join(HOME, ".var/app", FLATPAK_ID, "config/heroic")]
RUNNER_NAMES = {"gog": "GOG", "legendary": "Epic", "nile": "Amazon"}

_cache = (None, [])


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _files(root):
    return [os.path.join(root, p) for p in ("gog_store/installed.json", "store_cache/gog_library.json",
                                            "legendaryConfig/legendary/installed.json",
                                            "nile_config/nile/installed.json", "store_cache/nile_library.json")]


def _stamp():
    out = []
    for root in ROOTS:
        for f in _files(root):
            try:
                st = os.stat(f)
                out.append((f, st.st_mtime_ns, st.st_size))
            except OSError:
                pass
    return tuple(out)


def _titles(root, name):
    d = _read(os.path.join(root, "store_cache", name)) or {}
    games = d.get("games") if isinstance(d, dict) else None
    return {str(g.get("app_name")): g.get("title") or "" for g in games or [] if isinstance(g, dict)}


def installed():
    """every game Heroic has installed: [{runner, app, title, path, exe, root, flatpak}]"""
    global _cache
    stamp = _stamp()
    if _cache[0] == stamp:
        return _cache[1]
    out, seen = [], set()

    def add(root, runner, app, title, path, exe=""):
        if not app or not path or not os.path.isdir(path) or (runner, app) in seen:
            return
        seen.add((runner, app))
        out.append({"runner": runner, "app": str(app), "title": title or os.path.basename(os.path.normpath(path)),
                    "path": os.path.normpath(path), "exe": exe or "", "root": root,
                    "flatpak": os.sep + FLATPAK_ID + os.sep in root + os.sep})

    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        d = _read(os.path.join(root, "gog_store/installed.json")) or {}
        titles = _titles(root, "gog_library.json")
        for e in (d.get("installed") if isinstance(d, dict) else None) or []:
            if isinstance(e, dict) and not e.get("is_dlc"):
                add(root, "gog", e.get("appName"), titles.get(str(e.get("appName"))), e.get("install_path"), e.get("executable"))
        d = _read(os.path.join(root, "legendaryConfig/legendary/installed.json")) or {}
        for app, e in (d.items() if isinstance(d, dict) else []):
            if isinstance(e, dict) and not e.get("is_dlc"):
                add(root, "legendary", e.get("app_name") or app, e.get("title"), e.get("install_path"), e.get("executable"))
        d = _read(os.path.join(root, "nile_config/nile/installed.json"))
        titles = _titles(root, "nile_library.json")
        for e in d if isinstance(d, list) else []:
            if isinstance(e, dict):
                add(root, "nile", e.get("id"), titles.get(str(e.get("id"))), e.get("path"))
    _cache = (stamp, out)
    return out


def _plain(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def find(spec):
    """Heroic's copy of a game Armory knows: its proof file is in the folder, or the titles match"""
    proof, name = spec.get("proof"), _plain(spec.get("name"))
    for e in installed():
        if proof and os.path.exists(os.path.join(e["path"], proof)):
            return e
    for e in installed():
        if name and _plain(e["title"]) == name:
            return e
    return None


def at(path):
    """Heroic's game in this folder, or None"""
    if not path:
        return None
    real = os.path.realpath(path)
    return next((e for e in installed() if os.path.realpath(e["path"]) == real), None)


def drive_c(entry):
    """the game's prefix's drive_c, or None (a native game, or never run)"""
    d = _read(os.path.join(entry["root"], "GamesConfig", entry["app"] + ".json")) or {}
    conf = d.get(entry["app"]) if isinstance(d, dict) else None
    prefix = conf.get("winePrefix") if isinstance(conf, dict) else None
    if not prefix:
        return None
    for sub in ("pfx/drive_c", "drive_c"):            # (Proton keeps it in pfx/, Wine at the top)
        p = os.path.join(prefix, sub)
        if os.path.isdir(p):
            return p
    return None


def launch_command(entry):
    """the command that starts the game through Heroic, without its window"""
    url = f"heroic://launch?appName={entry['app']}&runner={entry['runner']}"
    if entry["flatpak"] and shutil.which("flatpak"):
        return ["flatpak", "run", FLATPAK_ID, "--no-gui", url]
    exe = shutil.which("heroic")
    if exe:
        return [exe, "--no-gui", url]
    return ["xdg-open", url]
