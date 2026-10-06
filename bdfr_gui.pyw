"""BDFR GUI: a small Tkinter front end for the Bulk Downloader for Reddit.

What it does
    Keeps a list of Reddit usernames, lets you tick which ones to download,
    set the limit / sort / time window and a few common flags, save all of
    that as named presets, and then runs

        python -m bdfr download <output folder> --user <name> ... [flags]

    as a subprocess, streaming its output into a log pane with a Stop button.
    Nothing here talks to Reddit directly; bdfr does all of the work.

Requirements
    * Python 3.9 or newer with Tkinter. The python.org installers for Windows
      and macOS include it; on Debian/Ubuntu install the ``python3-tk``
      package.
    * The ``bdfr`` package installed into the *same* Python interpreter that
      runs this script:  ``python -m pip install bdfr``
      The GUI launches bdfr with the interpreter it is running under, so a
      bdfr installed into some other Python or virtualenv will not be found.
    * Nothing else; only the standard library is used here.

Running it
    Windows:  double-click ``bdfr_gui.pyw``. The ``.pyw`` extension makes
              Windows start it with ``pythonw.exe``, so no console window
              opens. The GUI then runs bdfr with the ``python.exe`` that sits
              next to it, hidden, and captures its output in the log pane.
    Anywhere: ``python bdfr_gui.pyw``

Settings (the user list, presets and the last preset used) are stored in
``bdfr_gui_config.json`` next to this file, or wherever the ``BDFR_GUI_CONFIG``
environment variable points. Delete that file to start from scratch.
"""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Iterable


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

def _app_dir() -> str:
    """Folder this script lives in (symlinks resolved).

    Falls back to the working directory in the rare case ``__file__`` is not
    defined, e.g. when the source is exec'd by an embedding host.
    """
    try:
        return os.path.dirname(os.path.realpath(__file__))
    except NameError:
        return os.getcwd()


APP_DIR = _app_dir()

# Where the user list, presets and last-used preset are saved. Can be moved
# with the BDFR_GUI_CONFIG environment variable, e.g. when the script folder
# is read-only.
CONFIG_PATH = os.environ.get("BDFR_GUI_CONFIG") or os.path.join(APP_DIR, "bdfr_gui_config.json")

# Choices in the Sort and Time drop-downs. The empty string means "do not pass
# the flag", so bdfr uses its own default. bdfr only honours --time together
# with --sort top or --sort controversial.
SORTS = ["", "hot", "new", "top", "rising", "controversial", "relevance"]
TIMES = ["", "all", "hour", "day", "week", "month", "year"]

# Checkbox flags: config key -> (bdfr command-line flag, checkbox label).
# The config key is also the name the setting is stored under in presets.
FLAGS: dict[str, tuple[str, str]] = {
    "submitted": ("--submitted", "Submitted posts (--submitted)"),
    "authenticate": ("--authenticate", "Authenticate (--authenticate)"),
    "no_dupes": ("--no-dupes", "Skip duplicates (--no-dupes)"),
    "search_existing": ("--search-existing", "Search existing files (--search-existing)"),
    "verbose": ("--verbose", "Verbose log (--verbose)"),
}

# Settings used when no preset is loaded, and as fallbacks for any key that a
# saved preset is missing. Output defaults to ~/Downloads/bdfr.
DEFAULT: dict[str, Any] = {
    "output": os.path.join(os.path.expanduser("~"), "Downloads", "bdfr"),
    "selected_users": [], "limit": "", "sort": "new", "time": "",
    "extra": "", "submitted": True, "authenticate": False,
    "no_dupes": True, "search_existing": False, "verbose": False,
}

POLL_MS = 100            # how often the GUI drains the subprocess output queue
STOP_TIMEOUT_S = 5       # grace period after terminate() before kill()
WINDOW_GEOMETRY = "920x680"
WINDOW_MIN_SIZE = (760, 560)
LOG_FONT = ("Consolas", 9)  # Tk substitutes a default monospace font if absent


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def python_exe() -> str:
    """Interpreter used to run bdfr.

    Under pythonw.exe (what Windows uses for .pyw files) prefer the python.exe
    beside it; the child's output is captured through pipes and its console
    window is suppressed, so nothing is visible either way, but a console
    interpreter behaves more predictably for libraries that touch sys.stdout.
    Both executables share one installation, so bdfr is found in both.
    """
    exe = sys.executable or "python"
    if exe.lower().endswith("pythonw.exe"):
        alt = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(alt):
            return alt
    return exe


def bdfr_available() -> bool:
    """True if this interpreter can import ``bdfr``.

    python_exe() always points at the same installation as the running GUI,
    so checking here is equivalent to checking the subprocess interpreter and
    avoids a bare "No module named bdfr" traceback in the log.
    """
    try:
        return importlib.util.find_spec("bdfr") is not None
    except (ImportError, ValueError):
        return False


def clean_username(raw: str) -> str:
    """Reduce what was typed to a bare Reddit username.

    Accepts "name", "u/name", "/u/name", "user/name" and a profile URL such as
    "https://www.reddit.com/user/name/". Returns "" for empty input.
    """
    name = raw.strip()
    if "reddit.com/" in name:
        name = name.split("reddit.com/", 1)[1]
    name = name.strip("/")
    for prefix in ("user/", "u/"):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
            break
    return name.split("/", 1)[0].split("?", 1)[0].strip()


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------

class App(tk.Tk):
    """Main window. Widgets are built in build(); persistent state is self.cfg."""

    def __init__(self) -> None:
        super().__init__()
        self.title("BDFR Downloader")
        self.geometry(WINDOW_GEOMETRY)
        self.minsize(*WINDOW_MIN_SIZE)

        self.notes: list[str] = []  # messages for the log pane once it exists
        self.cfg = self.load_cfg()
        self.proc: subprocess.Popen[str] | None = None
        self.q: queue.Queue[str | tuple[str, int]] = queue.Queue()

        self.v_preset = tk.StringVar()
        self.v_output, self.v_limit = tk.StringVar(), tk.StringVar()
        self.v_sort, self.v_time, self.v_extra = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.v_new_user = tk.StringVar()
        self.v_cmd = tk.StringVar()
        self.flags: dict[str, tk.BooleanVar] = {k: tk.BooleanVar() for k in FLAGS}

        self.build()
        for v in [self.v_output, self.v_limit, self.v_sort, self.v_time, self.v_extra, *self.flags.values()]:
            v.trace_add("write", lambda *_: self.update_preview())

        self.refresh_users()
        self.refresh_presets()
        last = self.cfg.get("last_preset")
        if last in self.cfg["presets"]:
            self.v_preset.set(last)
            self.apply(self.cfg["presets"][last])
        else:
            self.apply(DEFAULT)
        for note in self.notes:
            self.write(note + "\n")
        self.after(POLL_MS, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def report_callback_exception(self, exc: Any, val: Any, tb: Any) -> None:
        """Tk calls this for exceptions raised inside event callbacks.

        The default prints to stderr, which does not exist under pythonw, so
        the error would vanish without trace. Show it in the log pane instead.
        """
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            self.write(f"\n--- Internal error ---\n{text}")
        except Exception:  # the log widget itself is unusable
            messagebox.showerror("Error", text)

    # ---------- config ----------
    def load_cfg(self) -> dict[str, Any]:
        """Read the JSON config, tolerating a missing, unreadable or corrupt file.

        A corrupt file is moved aside to ``*.bak`` so it is not silently
        overwritten on the next save. Shapes are checked so a hand-edited file
        cannot crash the GUI.
        """
        cfg: dict[str, Any] = {}
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, dict):
                raise ValueError("top level is not a JSON object")
            cfg = loaded
        except FileNotFoundError:
            pass
        except ValueError as ex:  # json.JSONDecodeError is a ValueError
            backup = CONFIG_PATH + ".bak"
            try:
                os.replace(CONFIG_PATH, backup)
                where = f"moved it to {os.path.basename(backup)}"
            except OSError:
                where = "left it in place"
            self.notes.append(f"Config file is not valid JSON ({ex}); {where} and started with defaults.")
        except OSError as ex:
            self.notes.append(f"Could not read config file {CONFIG_PATH}: {ex}")

        if not isinstance(cfg.get("users"), list):
            cfg["users"] = []
        cfg["users"] = [u for u in cfg["users"] if isinstance(u, str) and u]
        if not isinstance(cfg.get("presets"), dict):
            cfg["presets"] = {}
        cfg["presets"] = {k: v for k, v in cfg["presets"].items() if isinstance(v, dict)}
        return cfg

    def save_cfg(self) -> None:
        """Write the config atomically (temp file, then os.replace).

        A crash or power loss mid-write therefore cannot leave a half-written
        file. Failures are shown in a dialog rather than raised, because under
        pythonw an exception would be invisible.
        """
        tmp = CONFIG_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
            os.replace(tmp, CONFIG_PATH)
        except OSError as ex:
            messagebox.showerror("Save failed", f"Could not save settings to:\n{CONFIG_PATH}\n\n{ex}")

    # ---------- UI ----------
    def build(self) -> None:
        pad = {"padx": 6, "pady": 4}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="Preset:").pack(side="left")
        self.cb_preset = ttk.Combobox(top, textvariable=self.v_preset, state="readonly", width=30)
        self.cb_preset.pack(side="left", padx=4)
        self.cb_preset.bind("<<ComboboxSelected>>",
                            lambda e: self.apply(self.cfg["presets"][self.v_preset.get()]))
        ttk.Button(top, text="Save", command=self.save_preset).pack(side="left", padx=2)
        ttk.Button(top, text="Save As...", command=self.save_preset_as).pack(side="left", padx=2)
        ttk.Button(top, text="Delete", command=self.delete_preset).pack(side="left", padx=2)

        mid = ttk.Frame(self)
        mid.pack(fill="x", **pad)

        # Users
        uf = ttk.LabelFrame(mid, text="Users (click to select which to download)")
        uf.pack(side="left", fill="both", expand=True, padx=(0, 6))
        lbf = ttk.Frame(uf)
        lbf.pack(fill="both", expand=True, padx=4, pady=4)
        self.lb = tk.Listbox(lbf, selectmode=tk.MULTIPLE, exportselection=False, height=12)
        sb = ttk.Scrollbar(lbf, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lb.bind("<<ListboxSelect>>", lambda e: self.update_preview())
        addf = ttk.Frame(uf)
        addf.pack(fill="x", padx=4, pady=2)
        e = ttk.Entry(addf, textvariable=self.v_new_user)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.add_user())
        ttk.Button(addf, text="Add", command=self.add_user).pack(side="left", padx=2)
        bf = ttk.Frame(uf)
        bf.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(bf, text="Remove", command=self.remove_users).pack(side="left", padx=2)
        ttk.Button(bf, text="All", command=lambda: self.select_all(True)).pack(side="left", padx=2)
        ttk.Button(bf, text="None", command=lambda: self.select_all(False)).pack(side="left", padx=2)

        # Options
        of = ttk.LabelFrame(mid, text="Options")
        of.pack(side="left", fill="both", expand=True)
        of.columnconfigure(1, weight=1)
        ttk.Label(of, text="Output folder:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(of, textvariable=self.v_output).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(of, text="Browse", command=self.browse).grid(row=0, column=2, **pad)
        ttk.Label(of, text="Limit:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(of, textvariable=self.v_limit, width=8).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(of, text="Sort:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Combobox(of, textvariable=self.v_sort, values=SORTS, state="readonly",
                     width=14).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(of, text="Time:").grid(row=3, column=0, sticky="w", **pad)
        ttk.Combobox(of, textvariable=self.v_time, values=TIMES, state="readonly",
                     width=14).grid(row=3, column=1, sticky="w", **pad)
        r = 4
        for k, (_, label) in FLAGS.items():
            ttk.Checkbutton(of, text=label, variable=self.flags[k]).grid(
                row=r, column=0, columnspan=3, sticky="w", padx=6)
            r += 1
        ttk.Label(of, text="Extra args:").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(of, textvariable=self.v_extra).grid(row=r, column=1, columnspan=2, sticky="ew", **pad)

        # Command + run
        cf = ttk.Frame(self)
        cf.pack(fill="x", **pad)
        ttk.Label(cf, text="Command:").pack(side="left")
        ttk.Entry(cf, textvariable=self.v_cmd, state="readonly").pack(side="left", fill="x", expand=True, padx=4)
        self.btn_run = ttk.Button(cf, text="▶ Run", command=self.run)
        self.btn_run.pack(side="left", padx=2)
        self.btn_stop = ttk.Button(cf, text="■ Stop", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=2)
        ttk.Button(cf, text="Clear log", command=self.clear_log).pack(side="left", padx=2)

        # Log
        lf = ttk.Frame(self)
        lf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(lf, wrap="word", state="disabled", font=LOG_FONT)
        lsb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.config(yscrollcommand=lsb.set)
        self.log.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")

    # ---------- users ----------
    def refresh_users(self, keep: Iterable[str] | None = None) -> None:
        """Reload the listbox from cfg, re-selecting ``keep`` (default: current selection)."""
        keep = set(keep if keep is not None else self.selected_users())
        self.lb.delete(0, "end")
        for i, u in enumerate(self.cfg["users"]):
            self.lb.insert("end", u)
            if u in keep:
                self.lb.selection_set(i)
        self.update_preview()

    def selected_users(self) -> list[str]:
        return [self.lb.get(i) for i in self.lb.curselection()]

    def add_user(self) -> None:
        name = clean_username(self.v_new_user.get())
        self.v_new_user.set("")
        if not name or name.lower() in {u.lower() for u in self.cfg["users"]}:
            return  # Reddit usernames are case-insensitive, so so is the duplicate check
        sel = self.selected_users() + [name]
        self.cfg["users"].append(name)
        self.cfg["users"].sort(key=str.lower)
        self.save_cfg()
        self.refresh_users(sel)

    def remove_users(self) -> None:
        sel = self.selected_users()
        if sel and messagebox.askyesno("Remove", f"Remove {len(sel)} user(s) from list?"):
            self.cfg["users"] = [u for u in self.cfg["users"] if u not in sel]
            self.save_cfg()
            self.refresh_users([])

    def select_all(self, on: bool) -> None:
        (self.lb.selection_set if on else self.lb.selection_clear)(0, "end")
        self.update_preview()

    # ---------- presets ----------
    def current(self) -> dict[str, Any]:
        """Snapshot of every option as stored in a preset."""
        d: dict[str, Any] = {
            "output": self.v_output.get(), "selected_users": self.selected_users(),
            "limit": self.v_limit.get(), "sort": self.v_sort.get(),
            "time": self.v_time.get(), "extra": self.v_extra.get(),
        }
        d.update({k: v.get() for k, v in self.flags.items()})
        return d

    def apply(self, p: dict[str, Any]) -> None:
        """Load a preset into the widgets; missing keys fall back to DEFAULT."""
        p = {**DEFAULT, **p}
        self.v_output.set(str(p["output"]))
        self.v_limit.set(str(p["limit"]))
        self.v_sort.set(str(p["sort"]))
        self.v_time.set(str(p["time"]))
        self.v_extra.set(str(p["extra"]))
        for k, v in self.flags.items():
            v.set(bool(p[k]))
        users = p["selected_users"]
        self.refresh_users(users if isinstance(users, list) else [])

    def refresh_presets(self) -> None:
        self.cb_preset["values"] = sorted(self.cfg["presets"], key=str.lower)

    def save_preset(self) -> None:
        name = self.v_preset.get()
        if not name:
            self.save_preset_as()
            return
        self.cfg["presets"][name] = self.current()
        self.cfg["last_preset"] = name
        self.save_cfg()
        messagebox.showinfo("Saved", f"Preset '{name}' saved.")

    def save_preset_as(self) -> None:
        name = simpledialog.askstring("Save preset", "Preset name:", parent=self)
        if name and name.strip():
            name = name.strip()
            self.cfg["presets"][name] = self.current()
            self.cfg["last_preset"] = name
            self.save_cfg()
            self.refresh_presets()
            self.v_preset.set(name)

    def delete_preset(self) -> None:
        name = self.v_preset.get()
        if name and messagebox.askyesno("Delete", f"Delete preset '{name}'?"):
            self.cfg["presets"].pop(name, None)
            if self.cfg.get("last_preset") == name:
                self.cfg.pop("last_preset", None)
            self.save_cfg()
            self.refresh_presets()
            self.v_preset.set("")

    # ---------- command ----------
    def browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.v_output.get() or os.path.expanduser("~"))
        if d:
            self.v_output.set(os.path.normpath(d))

    def extra_args(self) -> list[str] | None:
        """Split the Extra args field shell-style; None if quotes are unbalanced."""
        extra = self.v_extra.get().strip()
        if not extra:
            return []
        try:
            return [a.strip('"') for a in shlex.split(extra, posix=False)]
        except ValueError:
            return None

    def build_args(self) -> list[str]:
        """Everything after ``python -m bdfr``; shared by the preview and run()."""
        args = ["download", self.v_output.get().strip() or "."]
        for u in self.selected_users():
            args += ["--user", u]
        for k, (flag, _) in FLAGS.items():
            if self.flags[k].get():
                args.append(flag)
        limit = self.v_limit.get().strip()
        if limit.isdigit():
            args += ["--limit", limit]
        if self.v_sort.get():
            args += ["--sort", self.v_sort.get()]
        if self.v_time.get():
            args += ["--time", self.v_time.get()]
        args += self.extra_args() or []
        return args

    def build_cmd(self) -> list[str]:
        return [python_exe(), "-m", "bdfr", *self.build_args()]

    def update_preview(self) -> None:
        preview = subprocess.list2cmdline(["bdfr", *self.build_args()])
        if self.extra_args() is None:
            preview += "   [extra args ignored: unbalanced quotes]"
        self.v_cmd.set(preview)

    # ---------- running ----------
    def run(self) -> None:
        if self.proc:
            return
        if not self.selected_users():
            messagebox.showwarning("No users", "Select at least one user.")
            return
        out = self.v_output.get().strip()
        if not out:
            messagebox.showwarning("No folder", "Choose an output folder.")
            return
        if self.extra_args() is None:
            messagebox.showwarning("Extra args", "Extra args have unbalanced quotes. Fix or clear them.")
            return
        if not bdfr_available():
            self.write("bdfr is not installed for this Python interpreter.\n"
                       f"Install it with:  {python_exe()} -m pip install bdfr\n")
            return
        try:
            os.makedirs(out, exist_ok=True)
        except OSError as ex:
            self.write(f"Cannot create output folder {out}: {ex}\n")
            return

        cmd = self.build_cmd()
        self.write(f"\n> {self.v_cmd.get()}\n")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, ValueError) as ex:
            self.proc = None
            self.write(f"Failed to start: {ex}\n")
            return
        self.btn_run.config(state="disabled")
        self.btn_stop.config(state="normal")
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()

    def reader(self, proc: subprocess.Popen[str]) -> None:
        """Background thread: forward output lines to the queue, then a
        ("done", returncode) tuple once the process has exited."""
        if proc.stdout is not None:
            for line in proc.stdout:
                self.q.put(line)
        proc.wait()
        self.q.put(("done", proc.returncode))

    def poll(self) -> None:
        """Drain the queue on the Tk thread; the only place the log is written from a run."""
        try:
            while True:
                item = self.q.get_nowait()
                if isinstance(item, tuple):
                    self.finished(item[1])
                else:
                    self.write(item)
        except queue.Empty:
            pass
        self.after(POLL_MS, self.poll)

    def finished(self, code: int) -> None:
        self.write(f"--- Finished (exit code {code}) ---\n")
        self.proc = None
        self.btn_run.config(state="normal")
        self.btn_stop.config(state="disabled")

    def stop(self) -> None:
        proc = self.proc
        if not proc:
            return
        self.write("--- Stopping... ---\n")
        proc.terminate()
        # Escalate if it ignores the request; poll() reports the exit either way.
        self.after(STOP_TIMEOUT_S * 1000, lambda: self.kill_if_running(proc))

    @staticmethod
    def kill_if_running(proc: subprocess.Popen[str]) -> None:
        if proc.poll() is None:
            proc.kill()

    def end_process(self) -> None:
        """Terminate the running bdfr (if any) and reap it before the window
        goes away, so closing never leaves an orphaned bdfr or a zombie."""
        proc = self.proc
        if not proc:
            return
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self.proc = None

    def write(self, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")

    def clear_log(self) -> None:
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def on_close(self) -> None:
        if self.proc and not messagebox.askyesno("Quit", "A download is running. Stop it and quit?"):
            return
        self.end_process()
        self.cfg["last_preset"] = self.v_preset.get()
        self.save_cfg()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
