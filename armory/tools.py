"""Tools: programs you run inside a game's Proton prefix (script extenders, config editors, xEdit...),
and starting the game through one of them instead of Steam.

Windows programs run with the Proton the game uses in Steam (its compatibility setting, else your
default, else the newest installed), with the game's own prefix, the way Steam runs them.
"""
import json
import os
import re
import shutil
import subprocess

from . import config, games, proton as protons


def tools(game):
    return list((config.get("tools", {}) or {}).get(game.id, []))


def save_tools(game, items):
    all_ = dict(config.get("tools", {}) or {})
    all_[game.id] = items
    config.save_conf(tools=all_)


# ---- starting a game that isn't Steam's ----------------------------------------------------------

NOT_THE_GAME = ("unins", "crash", "report", "redist", "setup", "install", "vc_", "dxsetup", "dotnet", "helper",
                "updater", "launcher_helper", "cleanup", "config", "settings", "benchmark")


def main_exe(game):
    """the program that is the game, in its folder: the one Armory knows it by, else the biggest
    .exe that isn't an installer or a helper, else a native program named like the folder"""
    try:
        names = os.listdir(game.path)
    except OSError:
        return None
    proof = (game.spec.get("proof") or "").lower()
    for n in names:
        if proof and n.lower() == proof and os.path.isfile(os.path.join(game.path, n)):
            return os.path.join(game.path, n)
    exes = [os.path.join(game.path, n) for n in names
            if n.lower().endswith(".exe") and not any(w in n.lower() for w in NOT_THE_GAME)]
    exes = [p for p in exes if os.path.isfile(p)]
    if exes:
        return max(exes, key=os.path.getsize)
    base = os.path.basename(os.path.normpath(game.path)).lower().replace(" ", "")
    for n in sorted(names):
        p = os.path.join(game.path, n)
        stem = os.path.splitext(n)[0].lower().replace(" ", "")
        if os.path.isfile(p) and os.access(p, os.X_OK) and (stem == base or n.lower().endswith((".x86_64", ".sh"))):
            return p
    return None


def hangar_command():
    """how to start Hangar (the non-Steam launcher), or None"""
    p = shutil.which("hangar")
    if p:
        return [p]
    for d in (os.path.join(config.HOME, "Appimages"), os.path.join(config.HOME, "Applications"),
              os.path.join(config.HOME, ".local", "bin")):
        try:
            for n in sorted(os.listdir(d)):
                if n.lower().startswith("hangar") and os.access(os.path.join(d, n), os.X_OK):
                    return [os.path.join(d, n)]
        except OSError:
            pass
    return None


def hangar_entry(game):
    """the id of Hangar's entry for this game (its program is in the game's folder), or None"""
    try:
        lib = json.load(open(os.path.join(config.HOME, ".config", "hangar", "library.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    root = os.path.realpath(game.path) + os.sep
    for e in lib if isinstance(lib, list) else []:
        exe = e.get("exe") if isinstance(e, dict) else None
        if exe and os.path.realpath(exe).startswith(root) and e.get("id"):
            return e["id"]
    return None


def start_external(game):
    """start a game that isn't Steam's copy, never through Steam: (the process, how it was started).
    Your own command; else Hangar (its entry for the game, with the prefix and runner set there, or
    the game's program run by it); else the program itself (a native one), umu-run or Wine in a
    prefix of Armory's. Nothing to start it with: an OSError that says what to do"""
    env = config.clean_env()
    quiet = dict(start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if game.launch_command:
        return subprocess.Popen(game.launch_command, shell=True, cwd=game.path, env=env, **quiet), "your command"
    if game.heroic:
        # Heroic's own copy: through Heroic, with the Wine or Proton, prefix and settings it has there
        from . import heroic
        return subprocess.Popen(heroic.launch_command(game.heroic), env=env, **quiet), "Heroic"
    hangar = hangar_command()
    if hangar:
        eid = hangar_entry(game)
        if eid:
            return subprocess.Popen(hangar + ["--launch", eid], env=env, **quiet), "Hangar"
    exe = main_exe(game)
    if not exe:
        raise OSError(f"Armory couldn't tell which program in {game.path} is the game, and it doesn't start "
                      "games outside Steam's libraries through Steam. Set how it starts: the game menu, "
                      "\"How Play starts it...\"")
    if not exe.lower().endswith(".exe"):
        return subprocess.Popen([exe], cwd=game.path, env=env, **quiet), "its own program"
    if hangar:
        return subprocess.Popen(hangar + ["--run", exe], env=env, **quiet), "Hangar"
    prefix = os.path.join(config.HOME, ".local", "share", "armory", "prefixes", game.id)
    umu = shutil.which("umu-run")
    picked = protons.folder_for(game)          # (the Proton picked for the game, if any)
    if umu:
        os.makedirs(prefix, exist_ok=True)
        env["WINEPREFIX"] = prefix
        env.setdefault("GAMEID", f"umu-{game.appid}" if game.appid else "0")
        if picked:
            env["PROTONPATH"] = picked
        return subprocess.Popen([umu, exe], cwd=game.path, env=env, **quiet), "umu-run"
    # a Proton that's on this PC already (Steam's own, a GE-Proton), run directly: most people
    # with Steam have one and neither umu-run nor Wine
    found = protons.installed()
    folder = picked or (found[0]["path"] if found else None)
    if folder:
        os.makedirs(prefix, exist_ok=True)
        env.update(STEAM_COMPAT_DATA_PATH=prefix, STEAM_COMPAT_CLIENT_INSTALL_PATH=games.steam_root() or "")
        return subprocess.Popen([os.path.join(folder, "proton"), "run", exe], cwd=game.path, env=env, **quiet), \
            os.path.basename(folder)
    wine = shutil.which("wine")
    if wine:
        os.makedirs(prefix, exist_ok=True)
        env["WINEPREFIX"] = prefix
        return subprocess.Popen([wine, exe], cwd=game.path, env=env, **quiet), "Wine"
    raise OSError("This copy of the game isn't Steam's, so Armory doesn't start it through Steam, and there's "
                  "nothing on this PC to run a Windows game with. The quickest fix: the game menu, \"Proton "
                  "version...\", Check for updates (it downloads GE-Proton). Or install Hangar, umu-launcher "
                  "or Wine, or set a command: the game menu, \"How Play starts it...\"")

def play_with(game):
    """the tool Play starts the game with ("" = Steam)"""
    name = (config.get("play_with", {}) or {}).get(game.id, "")
    return name if any(t["name"] == name for t in tools(game)) else ""


def set_play_with(game, name):
    d = dict(config.get("play_with", {}) or {})
    if name:
        d[game.id] = name
    else:
        d.pop(game.id, None)
    config.save_conf(play_with=d)


def _compat_tool(appid):
    """the compatibility tool Steam is set to use for a game (or the default one)"""
    root = games.steam_root()
    if not root:
        return ""
    try:
        text = open(os.path.join(root, "config", "config.vdf"), encoding="utf-8", errors="replace").read()
    except OSError:
        return ""
    m = re.search(r'"CompatToolMapping"\s*\{', text)
    if not m:
        return ""
    block = text[m.end():]
    names = {}
    for am in re.finditer(r'"(\d+)"\s*\{\s*"name"\s*"([^"]*)"', block):
        names[am.group(1)] = am.group(2)
    return names.get(str(appid)) or names.get("0") or ""


def proton_for(appid, game=None):
    """the proton script to run a game's tools with, or None: the version picked for the game
    (armory/proton.py), else what Steam uses for it"""
    if game is not None:
        picked = protons.script_for(game)
        if picked:
            return picked
    root = games.steam_root()
    if not root:
        return None
    custom = [os.path.join(r, "compatibilitytools.d") for r in games.STEAM_ROOTS] + \
        ["/usr/share/steam/compatibilitytools.d"]
    commons = [os.path.join(lib, "steamapps", "common") for lib in games.steam_libraries()]
    want = _compat_tool(appid)
    cands = []
    if want:
        for d in custom:
            cands.append(os.path.join(d, want, "proton"))
        pretty = {"proton_experimental": "Proton - Experimental", "proton_hotfix": "Proton Hotfix"}.get(want)
        n = re.match(r"proton_(\d+)", want)
        for c in commons:
            try:
                for name in os.listdir(c):
                    if name == pretty or (n and name.startswith(f"Proton {n.group(1)}")):
                        cands.append(os.path.join(c, name, "proton"))
            except OSError:
                pass
    # no setting for the game: what Steam uses then (its own Proton, Experimental first), then the
    # newest GE-Proton, then anything else
    def natural(n):
        return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", n)]

    for c in commons:
        try:
            names = os.listdir(c)
        except OSError:
            continue
        steam = [n for n in names if n.startswith("Proton") and "Runtime" not in n]
        steam.sort(key=lambda n: (n != "Proton - Experimental", [-x if isinstance(x, int) else 0 for x in natural(n)]))
        cands += [os.path.join(c, n, "proton") for n in steam]
    for d in custom:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        ge = sorted((n for n in names if n.lower().startswith("ge-proton")), key=natural, reverse=True)
        rest = sorted((n for n in names if n not in ge), key=natural, reverse=True)
        cands += [os.path.join(d, n, "proton") for n in ge + rest]
    return next((p for p in cands if os.path.isfile(p)), None)


def run(game, tool):
    """start a tool for a game: the process"""
    exe = tool["exe"]
    args = tool.get("args", "").split() if tool.get("args") else []
    cwd = os.path.dirname(exe)
    env = config.clean_env()
    if exe.lower().endswith((".exe", ".bat", ".msi")):
        if not game.appid:
            raise OSError("Windows tools need a Steam game (its Proton prefix)")
        proton = proton_for(game.appid, game)
        if not proton:
            raise OSError("No Proton found: install one in Steam")
        lib = game.library or (games.steam_install(game.appid)[0])
        compat = os.path.join(lib, "steamapps", "compatdata", str(game.appid)) if lib else ""
        if not compat or not os.path.isdir(compat):
            raise OSError("Run the game once from Steam first, so its Proton prefix exists")
        env.update(STEAM_COMPAT_DATA_PATH=compat, STEAM_COMPAT_CLIENT_INSTALL_PATH=games.steam_root() or "",
                   SteamAppId=str(game.appid), SteamGameId=str(game.appid))
        # the same DLL overrides the game's launch options set (mod loaders)
        opts = games.steam_launch_options(game.appid) or ""
        m = re.search(r'WINEDLLOVERRIDES="?([^"\s]+)', opts)
        if m:
            env["WINEDLLOVERRIDES"] = m.group(1)
        cmd = [proton, "run", exe] + args
    else:
        cmd = [exe] + args
    return subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
