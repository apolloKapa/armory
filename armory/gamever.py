"""Which update of the game a mod is made for (Blade & Sorcery: U11, U12.3, 1.0 ...).

A B&S mod's manifest.json says it exactly (GameVersion "0.12.3.0" = U12.3, "1.0.0.0" = 1.0). Before it's
downloaded, the Nexus file's name and description usually say it: "(U12)", "BaS 1.0", "for U11.3"."""
import re

# games whose mods are tied to one update of the game, and the newest update Armory knows of
KNOWN = {"bladeandsorcery": "1.0"}

_TAG = re.compile(
    r"(?<![\w.])(?:"
    r"U\s?(?P<u>\d{1,2}(?:\.\d{1,2})?)"                                  # U12, U 11.3
    r"|(?:BaS|B&S|B\s?and\s?S|Blade\s?(?:&|and)\s?Sorcery)\s*(?:U|v|Update\s*)?\s*(?P<b>\d{1,2}(?:\.\d{1,2})?)"
    r"|Update\s*(?P<up>\d{1,2}(?:\.\d{1,2})?)"                           # Update 12
    r"|\(\s*(?P<p>1\.\d{1,2})\s*\)"                                      # Mod Name (1.0)
    r"|(?:for|to|on)\s+(?P<f>1\.\d{1,2})(?![\d.])"                       # updated for 1.0
    r")(?![\w])", re.I)


def supported(gid):
    return gid in KNOWN


def _norm(major, minor, patch=0):
    """U12.3 for the early-access updates (0.x), 1.0 / 1.0.2 after"""
    if major == 0:
        return f"U{minor}" + (f".{patch}" if patch else "")
    return f"{major}.{minor}" + (f".{patch}" if patch else "")


def from_manifest(raw):
    """'0.12.3.0' -> 'U12.3', '1.0.0.0' -> '1.0', 'U12' stays"""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s[:1] in "uU":
        return from_text(s) or s
    nums = re.findall(r"\d+", s)
    if not nums:
        return ""
    n = [int(x) for x in nums[:3]] + [0, 0]
    return _norm(n[0], n[1], n[2])


def from_text(*texts):
    """the newest game update named in a file's name / description / the mod's name"""
    best = None
    for t in texts:
        for m in _TAG.finditer(str(t or "")):
            g = m.groupdict()
            if g["u"] or g["up"]:
                parts = (g["u"] or g["up"]).split(".")
                major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                if not 4 <= major <= 12:
                    continue
                v = (0, major, minor)
            else:
                parts = (g["b"] or g["p"] or g["f"]).split(".")
                a, b = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                if a >= 4 and g["b"]:
                    # "BaS U12" / "BaS 12": an early-access update
                    if a > 12:
                        continue
                    v = (0, a, b)
                elif a in (1, 2):
                    v = (a, b, 0)
                else:
                    continue
            if best is None or v > best:
                best = v
        if best:
            # the first text that names one wins (the file's own name beats the mod's)
            break
    return _norm(best[0], best[1], best[2]) if best else ""


def key(tag):
    """sortable: U12.3 -> (0, 12, 3), 1.0 -> (1, 0, 0)"""
    t = str(tag or "")
    nums = [int(x) for x in re.findall(r"\d+", t)] + [0, 0, 0]
    if t[:1] in "uU":
        return (0, nums[0], nums[1])
    return (nums[0], nums[1], nums[2])


def current(gid, installed=()):
    """the game's newest update: what Armory knows, or newer if installed mods say so"""
    best = KNOWN.get(gid, "")
    for t in installed:
        if t and key(t) > key(best):
            best = t
    return best


def is_old(tag, now):
    """made for an older update than the game's (only the first two numbers count: 1.0.2 is fine on 1.0)"""
    if not tag or not now:
        return False
    return key(tag)[:2] < key(now)[:2]


def note(tag, now, game_name):
    if is_old(tag, now):
        return (f"Made for {tag}. {game_name} is on {now} now: mods made for an older update usually don't "
                "load, or break the game. Look for a newer file.")
    return f"Made for {game_name} {tag}"
