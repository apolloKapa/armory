"""Which Proton a game's Windows programs run with, and getting a newer GE-Proton.

Armory runs Windows programs itself in two places: the tools you add to a game (script extenders,
mod tools) and a game that isn't Steam's copy. Both use "Default" unless you pick a version for the
game: what Steam is set to use for it, else Steam's own Proton, else the newest GE-Proton. (A game
Steam starts uses whatever Steam is set to: that's Steam's setting, not this one.)

GE-Proton comes from its GitHub releases; the download is checked against the release's sha512
before anything is unpacked, and unpacked next to the others (Steam's compatibilitytools.d, so
Steam can use it too; Armory's own folder if there's no Steam)."""
import hashlib
import os
import platform
import re
import shutil
import tarfile

from . import config, games
from .config import DATA, HOME
from .net import NetError, download, get_json, request

GE_API = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases/latest"
OWN = os.path.join(DATA, "protons")


def natural(n):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", n)]


def compat_dirs():
    return [os.path.join(r, "compatibilitytools.d") for r in games.STEAM_ROOTS] + \
        ["/usr/share/steam/compatibilitytools.d", OWN, os.path.join(HOME, ".local/share/hangar/runners/proton")]


def installed():
    """every Proton on this PC: [{name, path (its folder), ge}], GE-Protons first (newest first),
    then Steam's own (Experimental first), then anything else"""
    seen, ge, steam, rest = set(), [], [], []
    for d in compat_dirs():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            p = os.path.realpath(os.path.join(d, n))
            if p in seen or not os.path.isfile(os.path.join(p, "proton")):
                continue
            seen.add(p)
            (ge if n.lower().startswith("ge-proton") else rest).append({"name": n, "path": p, "ge": n.lower().startswith("ge-proton")})
    for lib in games.steam_libraries():
        c = os.path.join(lib, "steamapps", "common")
        try:
            names = os.listdir(c)
        except OSError:
            continue
        for n in names:
            p = os.path.realpath(os.path.join(c, n))
            if n.startswith("Proton") and p not in seen and os.path.isfile(os.path.join(p, "proton")):
                seen.add(p)
                steam.append({"name": n, "path": p, "ge": False})
    ge.sort(key=lambda e: natural(e["name"]), reverse=True)
    steam.sort(key=lambda e: (e["name"] != "Proton - Experimental", [-x if isinstance(x, int) else 0 for x in natural(e["name"])]))
    rest.sort(key=lambda e: natural(e["name"]), reverse=True)
    return ge + steam + rest


def choice(game):
    """the Proton picked for a game ("" = Default)"""
    return (config.get("proton", {}) or {}).get(game.id, "")


def set_choice(game, name):
    d = dict(config.get("proton", {}) or {})
    if name:
        d[game.id] = name
    else:
        d.pop(game.id, None)
    config.save_conf(proton=d)


def folder_for(game):
    """the folder of the Proton picked for a game, or None (Default, or it's gone)"""
    name = choice(game)
    if not name:
        return None
    return next((e["path"] for e in installed() if e["name"] == name), None)


def script_for(game):
    d = folder_for(game)
    return os.path.join(d, "proton") if d else None


# ---- a newer GE-Proton --------------------------------------------------------------------------

def ge_latest():
    """the newest GE-Proton release: {name, url, sums, size, have (it's installed already)}"""
    rel = get_json(GE_API, headers={"Accept": "application/vnd.github+json"})
    tag = rel.get("tag_name", "")
    if not re.fullmatch(r"GE-Proton[\w.-]+", tag):
        raise NetError("GitHub's answer didn't name a GE-Proton release")
    assets = {a["name"]: a for a in rel.get("assets", [])}
    # (newer releases have one archive per CPU, named for it; older ones a single x86_64 one)
    cpu = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())
    name = next((n for n in (f"{tag}-{cpu}", tag if cpu == "x86_64" else "") if n and n + ".tar.gz" in assets), "")
    tar, sums = assets.get(name + ".tar.gz"), assets.get(name + ".sha512sum")
    if not name or not tar or not sums:
        raise NetError(f"{tag} has no download with a checksum for this PC yet")
    return {"name": name, "url": tar["browser_download_url"], "sums": sums["browser_download_url"],
            "size": tar.get("size") or 0, "have": any(e["name"] in (name, tag) for e in installed())}


def install_dir():
    root = games.steam_root()
    return os.path.join(root, "compatibilitytools.d") if root else OWN


def ge_install(info, progress=None):
    """download a GE-Proton, check it, unpack it next to the others: its folder"""
    name = info["name"]
    dest_dir = install_dir()
    dest = os.path.join(dest_dir, name)
    if os.path.isfile(os.path.join(dest, "proton")):
        return dest
    os.makedirs(dest_dir, exist_ok=True)
    raw, _ = request(info["sums"])
    want = None
    for line in raw.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name + ".tar.gz" and re.fullmatch(r"[0-9a-fA-F]{128}", parts[0]):
            want = parts[0].lower()
    if not want:
        raise NetError("The release's checksum file doesn't list the download")
    work = os.path.join(dest_dir, f".armory-{name}")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    tar = os.path.join(work, name + ".tar.gz")
    try:
        download(info["url"], tar, progress)
        h = hashlib.sha512()
        with open(tar, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != want:
            raise NetError("The download doesn't match the release's checksum; nothing was installed")
        out = os.path.join(work, "out")
        with tarfile.open(tar, "r:gz") as t:
            t.extractall(out, filter="data")        # (no paths out of the folder, no devices, no setuid)
        got = os.path.join(out, name)
        if not os.path.isfile(os.path.join(got, "proton")):
            raise NetError("The download isn't laid out like a Proton; nothing was installed")
        os.replace(got, dest)
        return dest
    except (OSError, tarfile.TarError) as e:
        raise NetError(f"Couldn't install {name}: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
