"""Starting Armory: one window per user; a second start (a clicked nxm:// link, "Open with")
hands its arguments to the running one."""
import fcntl
import faulthandler
import os
import shutil
import socket
import struct
import sys
import threading
import time
import traceback

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from . import links
from .ui import theme

# a plain Unix socket (QtNetwork would pull in Kerberos libraries many desktops don't have)
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"{links.SOCKET}.sock")
LOCK = SOCK[:-5] + ".lock"
_window = None
_bridge = None
_log = None


class _Bridge(QObject):
    said = pyqtSignal(str)


def watch_window(w):
    """errors from now on are shown in w"""
    global _window, _bridge
    _window = w
    _bridge = _Bridge()
    _bridge.said.connect(w.say)


def open_log():
    """~/.local/state/armory/armory.log: errors and hard crashes (kept under 1 MB)"""
    global _log
    from .config import LOG
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 1 << 20:
            os.replace(LOG, LOG + ".old")
        _log = open(LOG, "a", encoding="utf-8", buffering=1)
        _log.write(f"\n==== Armory {__import__('armory').VERSION} started {time.strftime('%Y-%m-%d %H:%M:%S')} ====\n")
        # a crash inside Qt itself (no Python error to catch) still leaves its stack here
        faulthandler.enable(_log, all_threads=True)
    except OSError:
        _log = None
    return LOG


def log(text):
    for out in (_log, sys.stderr):
        try:
            if out:
                out.write(text if text.endswith("\n") else text + "\n")
                out.flush()
        except (OSError, ValueError, AttributeError):
            pass


def _excepthook(kind, value, tb):
    """a mistake in Armory shouldn't close it: PyQt6 aborts on an error nobody caught, so it's
    written to the log and shown, and Armory carries on"""
    log(f"[{time.strftime('%H:%M:%S')}] " + "".join(traceback.format_exception(kind, value, tb)))
    if kind is KeyboardInterrupt:
        os._exit(130)
    if _bridge is not None:
        try:
            # a signal, so it reaches the window from any thread
            _bridge.said.emit(f"Something went wrong ({kind.__name__}: {str(value)[:120]}). Armory kept going; "
                              "the details are in ~/.local/state/armory/armory.log")
        except Exception:  # noqa: BLE001 - never fail while reporting a failure
            pass


def _thread_hook(args):
    _excepthook(args.exc_type, args.exc_value, args.exc_traceback)


def take_lock():
    """only one Armory at a time: the open lock file, or None if another one holds it"""
    try:
        f = open(LOCK, "a")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        return None


def peer_uid(conn):
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


def send_to_running(args):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.5)
            s.connect(SOCK)
            if peer_uid(s) != os.getuid():
                # someone else's socket: links (with their download keys) aren't handed to it
                return False
            s.sendall("\0".join(args).encode())
        return True
    except OSError:
        return False


class Listener(QObject):
    got = pyqtSignal(list)

    def start(self):
        try:
            os.unlink(SOCK)
        except OSError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(SOCK)
        os.chmod(SOCK, 0o600)
        # many "Mod Manager Download" clicks in a row: they wait here instead of starting a second Armory
        self.srv.listen(64)
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with conn:
                try:
                    if peer_uid(conn) != os.getuid():
                        continue
                except OSError:
                    continue
                conn.settimeout(3)
                buf = b""
                try:
                    while True:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                except OSError:
                    pass
            self.got.emit([a for a in buf.decode(errors="replace").split("\0") if a])

    def stop(self):
        try:
            self.srv.close()
            os.unlink(SOCK)
        except OSError:
            pass


def main():
    if getattr(sys, "frozen", False):
        # the bundle's library path is only for Armory itself (the loader already read it): the
        # browser, Steam and tools Armory starts get yours back
        if "LD_LIBRARY_PATH_ORIG" in os.environ:
            os.environ["LD_LIBRARY_PATH"] = os.environ.pop("LD_LIBRARY_PATH_ORIG")
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
    if shutil.which("bsdtar") is None and shutil.which("7z") is None:
        print("armory: install bsdtar (libarchive) or 7-Zip to unpack mods", file=sys.stderr)
    args = sys.argv[1:]
    if args and args[0] in __import__("armory.cli", fromlist=["COMMANDS"]).COMMANDS:
        from . import cli
        cli.run()
        return
    if send_to_running(args or ["--show"]):
        return
    lock = take_lock()
    if lock is None:
        # another Armory is starting up (or too busy to answer yet): hand it the link once it listens,
        # never run two at once (they'd download the same files over each other)
        for _ in range(120):
            time.sleep(0.25)
            if send_to_running(args or ["--show"]):
                return
        print("armory: another Armory is running but doesn't answer; close it first", file=sys.stderr)
        sys.exit(1)
    open_log()
    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook
    app = QApplication(sys.argv[:1])
    app.setApplicationName("armory")
    app.setDesktopFileName("armory")
    app.setWindowIcon(QIcon(theme.ICON))
    from . import vault
    try:
        vault.migrate()
    except OSError:
        pass
    theme.apply(app)
    if getattr(sys, "frozen", False):
        # Qt has read what it needs: from here on, everything Armory starts (the keyring tool, the
        # browser behind every link, Steam) sees your system's environment, not the bundle's
        from . import config
        keep = {k: os.environ[k] for k in ("APPIMAGE", "APPDIR") if k in os.environ}
        clean = config.clean_env()
        os.environ.clear()
        os.environ.update(clean, **keep)
    from .ui.window import Window
    w = Window()
    watch_window(w)

    def incoming(got):
        w.showNormal()
        w.raise_()
        w.activateWindow()
        w.handle_args([a for a in got if a != "--show"])

    listener = Listener()
    listener.got.connect(incoming)
    try:
        listener.start()
    except OSError as e:
        print(f"armory: links won't reach this window ({e})", file=sys.stderr)
    w.show()
    if args:
        w.handle_args(args)
    code = app.exec()
    listener.stop()
    os._exit(code)


if __name__ == "__main__":
    main()
