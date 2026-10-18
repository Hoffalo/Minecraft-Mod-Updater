"""Minecraft-style window for mod_updater.py.

Runs mod_updater.py in the background and shows its output in a colored log.
The settings you enter are remembered in .gui-settings.json next to this file.

For the authentic look, install a Minecraft-like font such as Monocraft
(https://github.com/IdreesInc/Monocraft); otherwise Consolas is used.

Only the Python standard library is used.
"""

import json
import math
import os
import queue
import random
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "mod_updater.py"
SETTINGS_FILE = HERE / ".gui-settings.json"

WIDTH, HEIGHT = 780, 660
LOADERS = ["auto", "fabric", "quilt", "forge", "neoforge"]
SPLASHES = ["Now with buttons!", "100% stdlib!", "Also try Modrinth!",
            "Hashes all the way down!", "Backs up your jars!", "Not a creeper!"]

# Minecraft chat colors
WHITE, GRAY, DARK_GRAY = "#FFFFFF", "#AAAAAA", "#555555"
GREEN, RED, YELLOW, GOLD, AQUA = "#55FF55", "#FF5555", "#FFFF55", "#FFAA00", "#55FFFF"

BUTTON_STYLES = {
    #            fill       light      dark       text       shadow
    "normal":   ("#6F6F6F", "#A8A8A8", "#4A4A4A", "#E0E0E0", "#383838"),
    "hover":    ("#7E89BF", "#BDC6FF", "#5A64A0", "#FFFFA0", "#3F3F28"),
    "disabled": ("#2E2E2E", "#3A3A3A", "#232323", "#A0A0A0", "#282828"),
}


def pick_font(root, candidates):
    families = set(tkfont.families(root))
    return next((f for f in candidates if f in families), "TkFixedFont")


def dirt_tile(root):
    """A darkened 16x16 dirt texture, zoomed to 64x64 like the options screen."""
    rng = random.Random(7)
    palette = ["#3A2A1C"] * 5 + ["#33251A"] * 5 + ["#2C2016"] * 3 + ["#433121"] * 2 + ["#3B3631", "#2E2A26"]
    img = tk.PhotoImage(master=root, width=16, height=16)
    rows = " ".join("{" + " ".join(rng.choice(palette) for _ in range(16)) + "}" for _ in range(16))
    img.put(rows)
    return img.zoom(4)


def shadow_text(canvas, x, y, text, color, font, offset=2, **kw):
    """Draw text with the Minecraft drop shadow; returns (shadow, text) item ids."""
    r, g, b = (int(color[i:i + 2], 16) // 4 for i in (1, 3, 5))
    shadow = canvas.create_text(x + offset, y + offset, text=text, font=font,
                                fill=f"#{r:02X}{g:02X}{b:02X}", **kw)
    main = canvas.create_text(x, y, text=text, font=font, fill=color, **kw)
    return shadow, main


class MCButton:
    """A Minecraft menu button drawn on a canvas."""

    def __init__(self, canvas, x, y, w, h, text, command, font):
        self.canvas, self.command = canvas, command
        self.enabled, self.hover = True, False
        tag = f"button{id(self)}"
        c = canvas
        c.create_rectangle(x, y, x + w, y + h, fill="#000000", width=0, tags=tag)
        self.fill = c.create_rectangle(x + 2, y + 2, x + w - 2, y + h - 2, width=0, tags=tag)
        self.light = [c.create_rectangle(x + 2, y + 2, x + w - 2, y + 4, width=0, tags=tag),
                      c.create_rectangle(x + 2, y + 2, x + 4, y + h - 2, width=0, tags=tag)]
        self.dark = c.create_rectangle(x + 2, y + h - 6, x + w - 2, y + h - 2, width=0, tags=tag)
        cx, cy = x + w / 2, y + h / 2 - 1
        self.shadow = c.create_text(cx + 2, cy + 2, text=text, font=font, tags=tag)
        self.label = c.create_text(cx, cy, text=text, font=font, tags=tag)
        c.tag_bind(tag, "<Enter>", lambda e: self._set_hover(True))
        c.tag_bind(tag, "<Leave>", lambda e: self._set_hover(False))
        c.tag_bind(tag, "<ButtonRelease-1>", self._click)
        self._paint()

    def set_text(self, text):
        self.canvas.itemconfig(self.shadow, text=text)
        self.canvas.itemconfig(self.label, text=text)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self._paint()

    def _set_hover(self, hover):
        self.hover = hover
        self.canvas.config(cursor="hand2" if hover and self.enabled else "")
        self._paint()

    def _click(self, _event):
        if self.enabled and self.hover:
            self.command()

    def _paint(self):
        style = "disabled" if not self.enabled else "hover" if self.hover else "normal"
        fill, light, dark, text, shadow = BUTTON_STYLES[style]
        c = self.canvas
        c.itemconfig(self.fill, fill=fill)
        for item in self.light:
            c.itemconfig(item, fill=light)
        c.itemconfig(self.dark, fill=dark)
        c.itemconfig(self.label, fill=text)
        c.itemconfig(self.shadow, fill=shadow)


class App:
    def __init__(self, root):
        self.root = root
        self.proc = None
        self.lines = queue.Queue()
        settings = self._load_settings()
        self.loader = settings["loader"]
        self.prerelease = settings["prerelease"]

        body = pick_font(root, ["Minecraft", "Minecraftia", "Monocraft", "Consolas", "Courier New"])
        title = pick_font(root, ["Minecraft Ten", "Minecrafter", "Minecraft", "Monocraft", body])
        self.font = (body, 11)
        self.small = (body, 10)
        self.splash_fonts = [tkfont.Font(root, family=body, size=s, weight="bold") for s in (12, 13, 14, 15)]

        root.title("Mod Updater")
        root.resizable(False, False)
        root.configure(bg="#000000")
        c = self.canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, highlightthickness=0, bg="#2C2016")
        c.pack()

        self.tile = dirt_tile(root)
        for tx in range(0, WIDTH, 64):
            for ty in range(0, HEIGHT, 64):
                c.create_image(tx, ty, image=self.tile, anchor="nw")

        # Title and splash text
        shadow_text(c, WIDTH / 2, 52, "MOD UPDATER", WHITE, (title, 34, "bold"), offset=4)
        self.splash = shadow_text(c, WIDTH / 2 + 215, 78, random.choice(SPLASHES), YELLOW,
                                  self.splash_fonts[0], angle=18)
        self.splash_phase = 0.0

        # Row 1: version, loader, pre-releases
        shadow_text(c, 40, 118, "Minecraft Version", GRAY, self.small, anchor="w")
        self.version = self._entry(40, 132, 200, settings["mc_version"])
        self.loader_button = MCButton(c, 260, 130, 220, 34, "", self._cycle_loader, self.font)
        self.pre_button = MCButton(c, 500, 130, 240, 34, "", self._toggle_prerelease, self.font)
        self._refresh_toggles()

        # Row 2: mods folder
        shadow_text(c, 40, 186, "Mods Folder", GRAY, self.small, anchor="w")
        self.mods_dir = self._entry(40, 200, 580, settings["mods_dir"])
        MCButton(c, 630, 198, 110, 34, "Browse...", self._browse, self.font)

        # Row 3: actions
        self.actions = [
            MCButton(c, 40, 256, 220, 42, "Check for Updates", lambda: self._run("check"), self.font),
            MCButton(c, 270, 256, 150, 42, "Dry Run", lambda: self._run("check", "--dry-run"), self.font),
            MCButton(c, 430, 256, 150, 42, "Status", lambda: self._run("status"), self.font),
        ]
        MCButton(c, 590, 256, 150, 42, "Open Folder", self._open_folder, self.font)

        # Log
        frame = tk.Frame(c, bg="#A0A0A0", padx=2, pady=2)
        self.log = tk.Text(frame, width=1, height=1, bg="#000000", fg=WHITE, font=self.small,
                           relief="flat", wrap="word", padx=8, pady=6, state="disabled",
                           insertbackground=WHITE, selectbackground="#3A3A8A", cursor="arrow")
        self.log.pack(fill="both", expand=True)
        c.create_window(40, 316, window=frame, anchor="nw", width=700, height=296)
        for name, color in [("update", GREEN), ("missing", RED), ("ok", GRAY), ("unknown", YELLOW),
                            ("warning", GOLD), ("info", AQUA), ("error", RED), ("dim", DARK_GRAY),
                            ("plain", WHITE), ("done", GREEN)]:
            self.log.tag_configure(name, foreground=color)

        self.status = shadow_text(c, 40, 634, "Ready.", GRAY, self.small, anchor="w")
        self._write("Set your Minecraft version and press Check for Updates.", "dim")

        root.protocol("WM_DELETE_WINDOW", self._close)
        self._poll()
        self._animate_splash()

    # ---------------------------------------------------------------- widgets

    def _entry(self, x, y, w, value):
        entry = tk.Entry(self.canvas, font=self.font, bg="#000000", fg=WHITE, insertbackground=WHITE,
                         relief="flat", highlightthickness=2, highlightbackground="#A0A0A0",
                         highlightcolor=WHITE, selectbackground="#3A3A8A")
        entry.insert(0, value)
        self.canvas.create_window(x, y, window=entry, anchor="nw", width=w, height=30)
        return entry

    def _refresh_toggles(self):
        self.loader_button.set_text(f"Loader: {self.loader.capitalize()}")
        self.pre_button.set_text(f"Pre-releases: {'ON' if self.prerelease else 'OFF'}")

    def _cycle_loader(self):
        self.loader = LOADERS[(LOADERS.index(self.loader) + 1) % len(LOADERS)]
        self._refresh_toggles()

    def _toggle_prerelease(self):
        self.prerelease = not self.prerelease
        self._refresh_toggles()

    def _browse(self):
        start = self.mods_dir.get() if Path(self.mods_dir.get()).is_dir() else str(Path.home())
        folder = filedialog.askdirectory(parent=self.root, initialdir=start, title="Choose your mods folder")
        if folder:
            self.mods_dir.delete(0, "end")
            self.mods_dir.insert(0, str(Path(folder)))

    def _open_folder(self):
        folder = Path(self.mods_dir.get())
        if not folder.is_dir():
            self._write(f"Mods folder not found: {folder}", "error")
        elif os.name == "nt":
            os.startfile(folder)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])

    def _set_status(self, text, color=GRAY):
        r, g, b = (int(color[i:i + 2], 16) // 4 for i in (1, 3, 5))
        self.canvas.itemconfig(self.status[0], text=text, fill=f"#{r:02X}{g:02X}{b:02X}")
        self.canvas.itemconfig(self.status[1], text=text, fill=color)

    def _animate_splash(self):
        # The splash text pulses like the one on the title screen.
        self.splash_phase += 0.25
        size = int((math.sin(self.splash_phase) + 1) / 2 * (len(self.splash_fonts) - 0.01))
        for item in self.splash:
            self.canvas.itemconfig(item, font=self.splash_fonts[size])
        self.root.after(60, self._animate_splash)

    # ---------------------------------------------------------------- log

    def _write(self, line, tag=None):
        if tag is None:
            stripped = line.strip()
            tag = ("update" if stripped.startswith("UPDATE") else
                   "missing" if stripped.startswith("MISSING") else
                   "ok" if stripped.startswith("ok ") else
                   "unknown" if stripped.startswith("? ") else
                   "warning" if stripped.startswith(("Warning", "Left untouched")) else
                   "info" if stripped.startswith(("downloading", "***", "Old jars")) else
                   "dim" if stripped.startswith("(dry run") else "plain")
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    # ---------------------------------------------------------------- running

    def _run(self, command, *extra):
        version = self.version.get().strip()
        if not version:
            self._write("Enter a Minecraft version first, e.g. 26.4", "error")
            return
        args = [command, "--mc", version, "--mods-dir", self.mods_dir.get().strip(), *extra]
        if self.loader != "auto":
            args += ["--loader", self.loader]
        if self.prerelease:
            args.append("--prerelease")
        self._save_settings()

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._write("> mod_updater.py " + " ".join(f'"{a}"' if " " in a else a for a in args), "dim")

        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.proc = subprocess.Popen([sys.executable, "-u", str(SCRIPT), *args],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, encoding="utf-8", errors="replace",
                                         env=env, cwd=HERE, creationflags=flags)
        except OSError as e:
            self._write(f"Could not start mod_updater.py: {e}", "error")
            return
        for button in self.actions:
            button.set_enabled(False)
        self._set_status("Working...", YELLOW)
        threading.Thread(target=self._wait, args=(self.proc,), daemon=True).start()

    def _wait(self, proc):
        pumps = [threading.Thread(target=self._pump, args=(stream, tag), daemon=True)
                 for stream, tag in ((proc.stdout, None), (proc.stderr, "error"))]
        for t in pumps:
            t.start()
        for t in pumps:
            t.join()
        self.lines.put(("exit", proc.wait()))

    def _pump(self, stream, tag):
        for line in stream:
            self.lines.put((tag, line.rstrip("\n").replace("\a", "")))

    def _poll(self):
        try:
            while True:
                tag, item = self.lines.get_nowait()
                if tag == "exit":
                    self.proc = None
                    for button in self.actions:
                        button.set_enabled(True)
                    if item == 0:
                        self._set_status("Done.", GREEN)
                    else:
                        self._set_status(f"Stopped (exit code {item}).", RED)
                elif item.strip():
                    self._write(item, tag)
                else:
                    self._write("", "plain")
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    # ---------------------------------------------------------------- settings

    def _load_settings(self):
        default_mods = Path(os.environ.get("APPDATA", Path.home())) / ".minecraft" / "mods"
        settings = {
            "mc_version": os.environ.get("MC_VERSION", ""),
            "mods_dir": os.environ.get("MC_MODS_DIR") or str(default_mods),
            "loader": (os.environ.get("MC_LOADER") or "auto").lower(),
            "prerelease": os.environ.get("MC_ALLOW_PRERELEASE") == "1",
        }
        try:
            settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        if settings["loader"] not in LOADERS:
            settings["loader"] = "auto"
        return settings

    def _save_settings(self):
        settings = {"mc_version": self.version.get().strip(), "mods_dir": self.mods_dir.get().strip(),
                    "loader": self.loader, "prerelease": self.prerelease}
        try:
            SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _close(self):
        # Never kill a run: it could be halfway through swapping jars.
        if self.proc and self.proc.poll() is None:
            self._set_status("Still working; close the window once it's done.", GOLD)
            return
        self._save_settings()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
