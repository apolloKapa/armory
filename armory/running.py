"""Whether a game that Play just started is up: its processes, and its window where the desktop
can be asked for the windows (Hyprland, niri). The Play button shows it's loading until then."""
import json
import os
import subprocess

from . import config


def pids(game):
    """the processes that belong to the game: started in its folder, or with its folder (or, for
    a Steam game, Steam's own launcher for its id) in their command line"""
    path = (game.path or "").rstrip("/")
    if not path:
        return set()
    needles = [path.encode()]
    if game.appid:
        needles.append(b"AppId=%d\0" % game.appid)
    me = os.getpid()
    out = set()
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == me:
            continue
        try:
            cwd = os.readlink(f"/proc/{name}/cwd")
            if cwd == path or cwd.startswith(path + "/"):
                out.add(int(name))
                continue
            with open(f"/proc/{name}/cmdline", "rb") as fh:
                cmd = fh.read(8192)
            if cmd and any(n in cmd for n in needles):
                out.add(int(name))
        except OSError:
            # gone already, or someone else's
            continue
    return out


def _json(cmd):
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=2, env=config.clean_env())
        return json.loads(res.stdout) if res.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def window(game, found):
    """True: the game has a window. False: not yet. None: this desktop can't be asked"""
    names = {f"steam_app_{game.appid}"} if game.appid else set()
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        wins = _json(["hyprctl", "clients", "-j"])
        if isinstance(wins, list):
            return any(isinstance(w, dict) and w.get("mapped", True)
                       and (w.get("pid") in found or w.get("class") in names) for w in wins)
    if os.environ.get("NIRI_SOCKET"):
        wins = _json(["niri", "msg", "-j", "windows"])
        if isinstance(wins, list):
            return any(isinstance(w, dict) and (w.get("pid") in found or w.get("app_id") in names) for w in wins)
    return None


def state(game):
    """(the game has processes, it has a window or None when that can't be told)"""
    found = pids(game)
    return bool(found), window(game, found)
