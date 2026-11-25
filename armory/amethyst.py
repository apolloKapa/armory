"""Amethyst Mod Manager exports (.amethyst): a list of Nexus mods, their order, and settings files.

An export is a zip with
  manifest.json         {"AmethystManifest": true, "info": {"domainName", "appVersion"},
                         "mods": [{"name", "version", "optional", "domainName",
                                   "source": {"modId", "fileId", "logicalFilename", "fileSize"}}]}
  profile/modlist.txt   "+name" on, "-name" off, the top line wins (like Mod Organizer 2)
  profile/plugins.txt   the plugin load order (Bethesda games)
  overwrite/...         what the game and its mods wrote while playing: settings, and a lot of logs
The mods themselves aren't in it: each is a file on Nexus Mods, downloaded again here. (Worked out
from export files; nothing here comes from Amethyst's code.)
"""
import json
import os
import re
import stat
import zipfile

from .layout import matches, plain

EXT = ".amethyst"


class ParseError(ValueError):
    """the file isn't an export Armory can use (the message is shown as it is)"""


MAX_FILES = 20000
MAX_SETTINGS = 1024 * 1024 * 1024
# logs, crash reports and caches in overwrite/: every run makes new ones, none of it is a setting
JUNK_DIRS = {"logs", "log", "cache", "caches", "crashes", "crashdumps", "crash_reports", "dumps"}
JUNK_NAME = re.compile(r"(\.(log|dmp|mdmp|tmp|bak|old)\W*$)|(\.log\.\d+$)|(\.exe-[\d-]+\.txt$)|(^\.mm_)", re.IGNORECASE)


def is_export(path):
    return str(path).lower().endswith(EXT)


def is_junk(rel):
    parts = rel.split("/")
    return any(p.lower() in JUNK_DIRS for p in parts[:-1]) or bool(JUNK_NAME.search(parts[-1]))


def key(mod_id, file_id):
    """how a profile's import order names a Nexus file"""
    return f"{int(mod_id or 0)}:{int(file_id or 0)}"


def title(path):
    name = os.path.basename(path)
    name = name[:-len(EXT)] if is_export(name) else name
    name = re.sub(r"[ _-]*export$", "", name, flags=re.IGNORECASE).strip()
    return name or "Amethyst import"


def _lines(z, name):
    try:
        return z.read(name).decode("utf-8-sig", "replace").splitlines()
    except KeyError:
        return []


def _safe(name):
    """a zip entry's path as plain parts, or None for anything that could leave the folder"""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or ".." in parts or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return None
    return parts


def read(path, dest):
    """an export -> what's in it, shaped like a Nexus collection (see nexus.collection), plus
    "order" (import keys, lowest priority first), "plugins" [(name, on)], "skipped" (names it can't
    get) and "settings" [(abs, rel)]: the settings files, unpacked into dest"""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ParseError(f"That isn't an Amethyst export ({e})") from None
    with z:
        try:
            man = json.loads(z.read("manifest.json").decode("utf-8-sig"))
        except (KeyError, ValueError, UnicodeDecodeError):
            man = None
        if not isinstance(man, dict) or not isinstance(man.get("mods"), list):
            raise ParseError("That isn't an Amethyst export (it has no mod list)")
        info = man.get("info") if isinstance(man.get("info"), dict) else {}
        domain = str(info.get("domainName") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9]{1,64}", domain):
            raise ParseError("The export doesn't say which game it's for")
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise ParseError(f"That export is too big (more than {MAX_FILES} files)")

        # on/off and order: the profile's list, top line = highest priority
        state, listed = {}, []
        for line in _lines(z, "profile/modlist.txt"):
            if len(line) > 1 and line[0] in "+-" and not line.endswith("_separator"):
                state[line[1:]] = line[0] == "+"
                listed.append(line[1:])
        files, skipped, seen = [], [], set()
        per_mod = {}
        for m in man["mods"]:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").strip()
            src = m.get("source") if isinstance(m.get("source"), dict) else {}
            try:
                mod_id, file_id = int(src.get("modId") or 0), int(src.get("fileId") or 0)
            except (TypeError, ValueError):
                mod_id = file_id = 0
            own = str(m.get("domainName") or domain).strip().lower()
            if mod_id <= 0 or file_id <= 0 or own != domain:
                skipped.append(name or "a mod with no name")
                continue
            if (mod_id, file_id) in seen:
                continue
            seen.add((mod_id, file_id))
            per_mod[mod_id] = per_mod.get(mod_id, 0) + 1
            file_name = str(src.get("logicalFilename") or name)
            files.append({"mod_id": mod_id, "file_id": file_id, "file_name": file_name, "listed": name,
                          "version": str(m.get("version") or ""), "name": name or file_name, "summary": "",
                          "picture": "", "author": "", "adult": False, "optional": bool(m.get("optional")),
                          "disabled": state.get(name) is False, "size": int(src.get("fileSize") or 0)
                          if str(src.get("fileSize") or "").isdigit() else 0})
        for f in files:
            # several files of one mod: each is installed, none replaces another
            f["several"] = per_mod[f["mod_id"]] > 1
        known = {f["listed"] for f in files}
        skipped += [n for n in listed if n not in known and n not in skipped]
        # the manifest runs lowest priority first; the profile's list (when it names them) decides
        rank = {n: i for i, n in enumerate(reversed(listed))}
        ordered = sorted(files, key=lambda f: rank.get(f["listed"], len(rank)))
        plugins = []
        for line in _lines(z, "profile/plugins.txt"):
            line = line.strip()
            if line and not line.startswith("#"):
                plugins.append((line.lstrip("*"), line.startswith("*")))

        settings, total = [], 0
        root = os.path.abspath(dest)
        for i in infos:
            parts = _safe(i.filename)
            if i.is_dir() or not parts or parts[0] != "overwrite" or len(parts) < 2:
                continue
            if stat.S_ISLNK(i.external_attr >> 16):
                continue
            rel = "/".join(parts[1:])
            if is_junk(rel):
                continue
            total += i.file_size
            if total > MAX_SETTINGS:
                raise ParseError("That export's settings files are too big (more than 1 GB)")
            target = os.path.normpath(os.path.join(root, *parts[1:]))
            if not target.startswith(root + os.sep):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(i) as src, open(target, "wb") as out:
                left = i.file_size
                while left > 0:
                    chunk = src.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)
            settings.append((target, rel))
    version = str(info.get("appVersion") or "").strip()
    return {"kind": "amethyst", "name": title(path), "domain": domain, "files": files,
            "summary": "A mod list exported from Amethyst Mod Manager" + (f" {version}" if version else ""),
            "order": [key(f["mod_id"], f["file_id"]) for f in ordered], "plugins": plugins,
            "skipped": skipped, "settings": settings, "settings_size": total}


def describe(c, details):
    """fill in what Nexus says about each mod (details: nexus.details): its own name, summary, picture"""
    for f in c["files"]:
        d = details.get(f["mod_id"])
        if d:
            f.update(name=d["name"] or f["name"], summary=d["summary"], picture=d["picture"],
                     author=d["author"], adult=d["adult"])
    return c


def settings_files(c, game):
    """[(abs, staged rel)] for the export's settings files. overwrite/ mirrors the game folder; for
    games whose mods are laid out like their Data folder it may mirror that instead"""
    data = game.layout.get("data")
    base = ""
    if data:
        tops = {rel.split("/", 1)[0] for _, rel in c["settings"] if "/" in rel}
        marks = [data["dir"]] + list(game.layout.get("root_markers", []))
        if not any(matches(t, marks) for t in tops):
            base = data["dir"]
    return [(src, f"{base}/{plain(rel)}" if base else plain(rel)) for src, rel in c["settings"]]
