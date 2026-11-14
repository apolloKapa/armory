"""The activity card: what's downloading and installing right now, in the corner of the window.

Each mod gets a row from the moment Armory starts on it (asking Nexus for the link, downloading, checking,
unpacking, installing) until a few seconds after it's in, or a bit longer when it failed."""
import time

from PyQt6.QtCore import QObject, QPoint, QPropertyAnimation, QEasingCurve, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QProgressBar, QToolButton, QVBoxLayout

from .. import config
from ..config import human
from .theme import button, label, restyle, tip

WIDTH = 360
SHOW_ROWS = 5
KEEP_OK = 4000
KEEP_BAD = 9000


def _eta(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec} s left"
    if sec < 3600:
        return f"{sec // 60} min left"
    return f"{sec // 3600} h {sec % 3600 // 60} min left"


class Row(QFrame):
    def __init__(self, card, key):
        super().__init__()
        self.setObjectName("activityRow")
        self.key = key
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 6, 0, 6)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.name = label("", "actName")
        top.addWidget(self.name, 1)
        self.tag = label("", "badgeOk")
        self.tag.hide()
        top.addWidget(self.tag)
        self.stop = QToolButton()
        self.stop.setObjectName("actStop")
        self.stop.setText("✕")
        self.stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop.setToolTip("Stop this download")
        self.stop.clicked.connect(lambda: card.cancel(key))
        self.stop.hide()
        top.addWidget(self.stop)
        v.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setObjectName("actBar")
        self.bar.setFixedHeight(5)
        self.bar.setTextVisible(False)
        v.addWidget(self.bar)
        self.info = label("", "actInfo")
        v.addWidget(self.info)

    def show_entry(self, e):
        fm = QFontMetrics(self.name.font())
        w = WIDTH - 60 - (self.tag.sizeHint().width() if e.get("tag") else 0)
        self.name.setText(fm.elidedText(e["name"], Qt.TextElideMode.ElideRight, max(80, w)))
        self.name.setToolTip(tip(e["name"]))
        if e.get("tag"):
            self.tag.setText(e["tag"])
            self.tag.setToolTip(tip(e.get("tag_note", "")))
            if self.tag.objectName() != ("badgeOld" if e.get("tag_old") else "badgeOk"):
                restyle(self.tag, "badgeOld" if e.get("tag_old") else "badgeOk")
            self.tag.show()
        else:
            self.tag.hide()
        st = e["state"]
        self.stop.setVisible(st == "run" and e.get("cancel") is not None)
        if st in ("ok", "bad", "off"):
            self.bar.setRange(0, 1)
            self.bar.setValue(1 if st == "ok" else 0)
            self.bar.setVisible(st == "ok")
        elif e.get("value") is None:
            self.bar.setRange(0, 0)
            self.bar.show()
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(1.0, e["value"])) * 1000))
            self.bar.show()
        name = {"ok": "actOk", "bad": "actBad"}.get(st, "actInfo")
        if self.info.objectName() != name:
            restyle(self.info, name)
        self.info.setText(e.get("text", ""))
        self.info.setToolTip(tip(e.get("text", "")) if len(e.get("text", "")) > 50 else "")
        self.info.setWordWrap(st == "bad")


class Activity(QObject):
    """the rows (as data); the card below draws them"""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.entries = {}   # key -> {name, text, value, state, source, tag, cancel, phase, at}
        self.timers = {}

    def start(self, key, name, text, value=None, source=None, cancel=None, phase="", tag=None):
        e = self.entries.get(key) or {"at": time.time()}
        e.update(name=name or e.get("name") or "Mod", text=text, value=value, state="run", cancel=cancel,
                 phase=phase or e.get("phase", ""))
        if source is not None:
            e["source"] = source
        if tag is not None:
            e.update(tag)
        self.entries[key] = e
        self._unschedule(key)
        self.changed.emit()
        return e

    def update(self, key, **kw):
        e = self.entries.get(key)
        if not e:
            return
        e.update(kw)
        self.changed.emit()

    def rekey(self, old, new):
        if old in self.entries and old != new:
            self.entries[new] = self.entries.pop(old)
            self._unschedule(old)
            self.changed.emit()

    def finish(self, key, ok=True, text=None, state=None):
        e = self.entries.get(key)
        if not e:
            return
        e.update(state=state or ("ok" if ok else "bad"), text=text if text is not None else
                 ("Done" if ok else "Failed"), value=None, cancel=None)
        self._unschedule(key)
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(lambda: self.drop(key))
        t.start(KEEP_OK if e["state"] in ("ok", "off") else KEEP_BAD)
        self.timers[key] = t
        self.changed.emit()

    def drop(self, key):
        self._unschedule(key)
        if self.entries.pop(key, None) is not None:
            self.changed.emit()

    def _unschedule(self, key):
        t = self.timers.pop(key, None)
        if t:
            t.stop()
            t.deleteLater()

    def running(self):
        return [k for k, e in self.entries.items() if e["state"] == "run"]

    def busy_text(self, src_type, item_id):
        """what a Browse button says while that mod is on its way in, or None"""
        for e in self.entries.values():
            s = e.get("source") or {}
            if e["state"] != "run" or s.get("type") != src_type:
                continue
            same = str(s.get("mod_id")) == str(item_id) if src_type == "nexus" else \
                str(s.get("full_name", "")).lower() == str(item_id).lower()
            if not same:
                continue
            if e.get("phase") == "dl":
                return f"Downloading {int(e['value'] * 100)}%" if e.get("value") is not None else "Downloading..."
            return "Installing..."
        return None

    # ---- downloads ----
    def watch(self, dl):
        self.dl = dl
        self.speed = {}
        dl.changed.connect(self.sync)
        dl.progress.connect(self.on_progress)
        self.sync()

    def _dl_text(self, d):
        if d["status"] == "queued":
            return "Waiting for a free slot"
        done, total = d.get("done", 0), d.get("total", 0)
        now = time.time()
        t0, b0, rate = self.speed.get(d["id"], (now, done, 0.0))
        if now - t0 >= 0.8:
            inst = (done - b0) / max(0.001, now - t0)
            rate = inst if not rate else rate * 0.6 + inst * 0.4
            self.speed[d["id"]] = (now, done, rate)
        elif d["id"] not in self.speed:
            self.speed[d["id"]] = (now, done, 0.0)
        bits = [f"{human(done)} of {human(total)}" if total else human(done)]
        if rate > 0:
            bits.append(f"{human(rate)}/s")
            if total and done < total:
                bits.append(_eta((total - done) / rate))
        return "Downloading  ·  " + "  ·  ".join(bits)

    def _dl_entry(self, d):
        meta = d.get("meta") or {}
        value = d["done"] / d["total"] if d["status"] == "downloading" and d.get("total") else \
            (0.0 if d["status"] == "queued" else None)
        key = d["id"]
        e = self.entries.get(key)
        if e and e.get("phase") not in ("dl", "link"):
            return
        self.start(key, d["name"], self._dl_text(d), value, meta.get("source"),
                   cancel=lambda: self.dl.cancel(d["id"]), phase="dl", tag=meta.get("tag_info"))

    def sync(self):
        for d in self.dl.items:
            key = d["id"]
            e = self.entries.get(key)
            st = d["status"]
            if st in ("queued", "downloading"):
                self._dl_entry(d)
            elif e and e.get("phase") == "dl" and e["state"] == "run":
                self.speed.pop(key, None)
                if st == "done":
                    if d.get("install", True) and config.get("auto_install", True):
                        self.start(key, d["name"], "Downloaded  ·  getting it ready", None, phase="install")
                    else:
                        self.finish(key, True, "Downloaded: install it from Downloads")
                elif st == "failed":
                    self.finish(key, False, d.get("error") or "Download failed")
                elif st == "cancelled":
                    self.finish(key, text="Stopped", state="off")
        # rows of downloads that were removed from the list
        ids = {d["id"] for d in self.dl.items}
        for key in [k for k, e in self.entries.items() if e.get("phase") == "dl" and k not in ids]:
            self.drop(key)

    def on_progress(self, did):
        d = self.dl.get(did)
        if not d:
            return
        e = self.entries.get(did)
        if not e or e.get("phase") != "dl":
            self._dl_entry(d)
            return
        e["text"] = self._dl_text(d)
        e["value"] = d["done"] / d["total"] if d.get("total") else None
        self.changed.emit()

    def cancel(self, key):
        e = self.entries.get(key)
        if e and e.get("cancel"):
            e["cancel"]()


class ActivityCard(QFrame):
    """the corner card that shows Activity's rows"""
    def __init__(self, parent, act, open_downloads):
        super().__init__(parent)
        self.setObjectName("activity")
        self.act = act
        self.rows = {}
        self.folded = False
        self.setFixedWidth(WIDTH)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 12, 12)
        v.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = label("", "actTitle")
        head.addWidget(self.title, 1)
        self.more = button("Downloads", open_downloads, "link")
        head.addWidget(self.more)
        self.fold = QToolButton()
        self.fold.setObjectName("actStop")
        self.fold.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fold.clicked.connect(self.toggle)
        head.addWidget(self.fold)
        v.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        v.addLayout(self.body)
        self.extra = label("", "actInfo")
        v.addWidget(self.extra)
        self.fx = QGraphicsOpacityEffect(self)
        self.fx.setOpacity(1.0)
        self.setGraphicsEffect(self.fx)
        self.anim = None
        self.hide()
        self.redraw = QTimer(self)
        self.redraw.setSingleShot(True)
        self.redraw.setInterval(120)
        self.redraw.timeout.connect(self.draw)
        act.changed.connect(lambda: self.redraw.isActive() or self.redraw.start())

    def toggle(self):
        self.folded = not self.folded
        self.draw()

    def draw(self):
        entries = self.act.entries
        order = sorted(entries, key=lambda k: ({"run": 0, "bad": 1, "ok": 2, "off": 3}[entries[k]["state"]],
                                               entries[k]["at"]))
        shown = [] if self.folded else order[:SHOW_ROWS]
        for k in list(self.rows):
            if k not in shown:
                r = self.rows.pop(k)
                r.hide()
                r.setParent(None)
                r.deleteLater()
        for i, k in enumerate(shown):
            r = self.rows.get(k)
            if not r:
                r = self.rows[k] = Row(self, k)
            self.body.insertWidget(i, r)
            r.show_entry(entries[k])
        n_dl = sum(1 for e in entries.values() if e["state"] == "run" and e.get("phase") in ("dl", "link"))
        n_in = sum(1 for e in entries.values() if e["state"] == "run" and e.get("phase") not in ("dl", "link"))
        bits = ([f"Downloading {n_dl}"] if n_dl else []) + ([f"Installing {n_in}"] if n_in else [])
        self.title.setText("  ·  ".join(bits) if bits else "All done")
        rest = len(order) - len(shown)
        self.extra.setText(f"and {rest} more" if rest and not self.folded else "")
        self.extra.setVisible(bool(self.extra.text()))
        self.fold.setText("▴" if self.folded else "▾")
        self.fold.setToolTip("Show them" if self.folded else "Fold it up")
        if entries:
            self.appear()
        else:
            self.vanish()
        self.place()

    def cancel(self, key):
        self.act.cancel(key)

    def place(self):
        p = self.parentWidget()
        if not p:
            return
        self.adjustSize()
        self.move(p.width() - self.width() - 22, p.height() - self.height() - 22)
        self.raise_()
        toast = getattr(self, "toast", None)
        if toast is not None and toast.isVisible():
            toast.place()

    def appear(self):
        from .motion import enabled
        if self.isVisible() and (self.anim is None or self.anim.endValue() == 1.0):
            return
        self.show()
        self.place()
        if not enabled():
            self.fx.setOpacity(1.0)
            return
        self._fade(1.0)
        end = self.pos()
        self.rise = QPropertyAnimation(self, b"pos", self)
        self.rise.setDuration(240)
        self.rise.setStartValue(end + QPoint(0, 16))
        self.rise.setEndValue(end)
        self.rise.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.rise.start()

    def vanish(self):
        from .motion import enabled
        if not self.isVisible() or (self.anim is not None and self.anim.endValue() == 0.0):
            return
        if not enabled():
            self.hide()
            return
        self._fade(0.0)

    def _fade(self, to):
        if self.anim:
            self.anim.stop()
        self.anim = QPropertyAnimation(self.fx, b"opacity", self)
        self.anim.setDuration(220)
        self.anim.setStartValue(self.fx.opacity())
        self.anim.setEndValue(to)
        if to == 0.0:
            self.anim.finished.connect(lambda: self.fx.opacity() < 0.05 and not self.act.entries and self.hide())
        self.anim.start()
