"""Project Manager: Premiere Pro's "Collect Files and Copy to New Location",
without opening Premiere, for one project or a batch. The project logic lives
in projectmanager/prproj.py."""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from projectmanager import prproj
from projectmanager.prproj import human_size
from projectmanager.windrop import NativeWindowsFileDrop

APP_TITLE = "Project Manager"
APP_VERSION = "1.1"

# Premiere's own dialog colours.
BG = "#232323"
PANEL = "#1D1D1D"
FIELD = "#161616"
BORDER = "#3A3A3A"
TEXT = "#D6D6D6"
TEXT2 = "#9A9A9A"
TEXT3 = "#6A6A6A"
BLUE = "#2D8CEB"
WARN = "#E8B04B"
ERROR = "#F07171"
GOOD = "#6FD08C"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")


def default_destination() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return desktop if os.path.isdir(desktop) else os.path.expanduser("~")


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class Group(tk.Frame):
    """A titled box like the ones in Premiere's dialog."""

    def __init__(self, parent, title: str):
        super().__init__(parent, bg=BG)
        self.title = tk.Label(self, text=title, bg=BG, fg=TEXT, font=FONT)
        self.title.pack(anchor="w", pady=(0, 4))
        self.body = tk.Frame(self, bg=BG, highlightthickness=1, highlightbackground=BORDER)
        self.body.pack(fill="both", expand=True)


class ProjectManagerApp(tk.Tk):
    def __init__(self, initial: Optional[list[str]] = None):
        super().__init__()
        self.title(f"{APP_TITLE} {APP_VERSION}")
        self.geometry("900x960")
        self.minsize(780, 820)
        self.configure(bg=BG)
        self.projects: list[prproj.PremiereProject] = []
        self.plans: Optional[list[prproj.CollectPlan]] = None
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.cancel_flag = threading.Event()
        self.last_output: Optional[str] = None
        self.running: Optional[prproj.CollectPlan] = None

        self.exclude_unused = tk.BooleanVar(value=False)
        self.include_conform = tk.BooleanVar(value=True)
        self.include_previews = tk.BooleanVar(value=True)
        self.rename_media = tk.BooleanVar(value=False)
        self.copy_aroll = tk.BooleanVar(value=True)
        self.make_zip = tk.BooleanVar(value=False)
        self.keep_folder = tk.BooleanVar(value=True)
        self.destination = tk.StringVar(value=default_destination())

        self._style()
        self._build()
        for var in (self.exclude_unused, self.include_conform, self.include_previews,
                    self.rename_media, self.copy_aroll, self.make_zip, self.keep_folder,
                    self.destination):
            var.trace_add("write", lambda *_: self.invalidate())
        self.after(100, self._pump)
        try:
            self.native_drop = NativeWindowsFileDrop(self, self.on_drop)
        except Exception:
            self.native_drop = None
        if initial:
            self.after(50, lambda: self.add_projects(initial))

    # ── layout ───────────────────────────────────────────────────────────────
    def _style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                        bordercolor=BORDER, rowheight=24, font=FONT)
        style.map("Treeview", background=[("selected", "#2B3A4D")], foreground=[("selected", TEXT)])
        style.configure("Treeview.Heading", background=PANEL, foreground=TEXT2, relief="flat")
        style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=FIELD,
                        bordercolor=BORDER, arrowcolor=TEXT2)
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor=FIELD,
                        bordercolor=BORDER, lightcolor=BLUE, darkcolor=BLUE)

    def _button(self, parent, text, command, primary=False, **kw):
        return tk.Button(parent, text=text, command=command, font=FONT, relief="flat", bd=0,
                         padx=16, pady=4, cursor="hand2",
                         bg=BLUE if primary else "#3A3A3A", fg="white" if primary else TEXT,
                         activebackground="#4A9CF5" if primary else "#4A4A4A",
                         activeforeground="white", disabledforeground=TEXT3, **kw)

    def _check(self, parent, text, var):
        return tk.Checkbutton(parent, text=text, variable=var, bg=BG, fg=TEXT, font=FONT,
                              selectcolor=FIELD, activebackground=BG, activeforeground=TEXT,
                              anchor="w", highlightthickness=0)

    def _build(self):
        outer = tk.Frame(self, bg=BG, padx=16, pady=12)
        outer.pack(fill="both", expand=True)

        # Projects: one or many; each is copied to its own Copied_<name>.
        projects = Group(outer, "Projects")
        projects.pack(fill="x")
        box = tk.Frame(projects.body, bg=FIELD)
        box.pack(side="left", fill="both", expand=True)
        self.project_list = tk.Listbox(box, height=4, bg=FIELD, fg=TEXT, font=FONT, relief="flat",
                                       highlightthickness=0, selectbackground="#2B3A4D",
                                       selectforeground=TEXT, activestyle="none", exportselection=False)
        list_scroll = ttk.Scrollbar(box, orient="vertical", command=self.project_list.yview)
        self.project_list.configure(yscrollcommand=list_scroll.set)
        self.project_list.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        list_scroll.pack(side="right", fill="y")
        self.project_list.bind("<<ListboxSelect>>", lambda _: self.show_selected())
        self.project_hint = tk.Label(box, text="Add .prproj files, or drop them on this window",
                                     bg=FIELD, fg=TEXT3, font=FONT)
        side = tk.Frame(projects.body, bg=BG, padx=8, pady=6)
        side.pack(side="right", fill="y")
        self.add_button = self._button(side, "Add...", self.choose_projects)
        self.add_button.pack(fill="x")
        self.remove_button = self._button(side, "Remove", self.remove_selected)
        self.remove_button.pack(fill="x", pady=(6, 0))

        # Sequences: every one is copied; the list shows what is in the selected project.
        # Everything under the list is pinned to the bottom, so the list takes what is left.
        lower = tk.Frame(outer, bg=BG)
        lower.pack(side="bottom", fill="x")
        self.seq_group = Group(outer, "Sequences (all are copied)")
        self.seq_group.pack(fill="both", expand=True, pady=(12, 0))
        self.seq_count = tk.Label(self.seq_group.body, text="", bg=PANEL, fg=TEXT2, font=FONT,
                                  anchor="w", padx=8)
        self.seq_count.pack(side="bottom", fill="x")
        tree_box = tk.Frame(self.seq_group.body, bg=FIELD)
        tree_box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_box, show="tree", selectmode="none", height=6)
        scroll = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Resulting project / options
        mid = tk.Frame(lower, bg=BG)
        mid.pack(fill="x", pady=(12, 0))
        result = Group(mid, "Resulting Project")
        result.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.mode = tk.IntVar(value=1)  # the only mode; kept so the dialog reads like Premiere's
        tk.Radiobutton(result.body, text="Collect Files and Copy to New Location", value=1,
                       variable=self.mode, bg=BG, fg=TEXT, selectcolor=FIELD,
                       activebackground=BG, activeforeground=TEXT, font=FONT,
                       highlightthickness=0).pack(anchor="w", padx=8, pady=(8, 2))
        tk.Label(result.body, text="Copies whole media files and writes a new project\n"
                                   "that points at the copies. Each project gets its\n"
                                   "own Copied_<name> folder.",
                 bg=BG, fg=TEXT3, font=FONT, justify="left").pack(anchor="w", padx=30, pady=(0, 8))

        opts = Group(mid, "Options")
        opts.pack(side="left", fill="both", expand=True)
        for text, var in (("Exclude Unused Clips", self.exclude_unused),
                          ("Include Audio Conform Files", self.include_conform),
                          ("Include Preview Files", self.include_previews),
                          ("Rename Media Files to Match Clip Names", self.rename_media),
                          ("Copy A-roll Footage", self.copy_aroll)):
            self._check(opts.body, text, var).pack(anchor="w", padx=8, pady=1)
        tk.Label(opts.body, text="Unticked: clips in an Aroll bin or folder stay linked\n"
                                 "to the original files instead of being copied.",
                 bg=BG, fg=TEXT3, font=FONT, justify="left").pack(anchor="w", padx=30, pady=(0, 6))
        self._check(opts.body, "Zip the Copied Project", self.make_zip).pack(anchor="w", padx=8, pady=1)
        self.keep_folder_box = self._check(opts.body, "Keep Folder After Zipping", self.keep_folder)
        self.keep_folder_box.pack(anchor="w", padx=30, pady=(1, 8))
        self.make_zip.trace_add("write", lambda *_: self._sync_zip_options())
        self._sync_zip_options()

        # Destination
        dest = Group(lower, "Destination Path")
        dest.pack(fill="x", pady=(12, 0))
        row = tk.Frame(dest.body, bg=BG, padx=8, pady=8)
        row.pack(fill="x")
        tk.Entry(row, textvariable=self.destination, bg=FIELD, fg=TEXT, insertbackground=TEXT,
                 relief="flat", font=FONT).pack(side="left", fill="x", expand=True, ipady=4)
        self._button(row, "Browse...", self.choose_destination).pack(side="left", padx=(8, 0))

        # Disk space
        disk = Group(lower, "Disk Space")
        disk.pack(fill="x", pady=(12, 0))
        grid = tk.Frame(disk.body, bg=BG, padx=8, pady=8)
        grid.pack(fill="x")
        self.free_label = self._kv(grid, 0, "Disk Space Available:")
        self.size_label = self._kv(grid, 1, "Resulting Size (est.):")
        self._button(grid, "Calculate", self.calculate).grid(row=1, column=2, sticky="e", padx=8)
        grid.columnconfigure(1, weight=1)

        # Status + buttons
        self.status = tk.Label(lower, text="", bg=BG, fg=TEXT2, font=FONT, anchor="w", justify="left")
        self.status.pack(fill="x", pady=(10, 2))
        self.progress = ttk.Progressbar(lower, mode="determinate", maximum=1000)
        self.progress.pack(fill="x")
        buttons = tk.Frame(lower, bg=BG)
        buttons.pack(fill="x", pady=(10, 0))
        self.missing_button = self._button(buttons, "Missing files...", self.show_missing)
        self.open_button = self._button(buttons, "Open folder", self.open_output)
        self.reset_button = self._button(buttons, "Reset", self.reset)
        self.cancel_button = self._button(buttons, "Cancel", self.on_cancel)
        self.cancel_button.pack(side="right")
        self.ok_button = self._button(buttons, "OK", self.start_copy, primary=True)
        self.ok_button.pack(side="right", padx=8)
        self.refresh_projects()
        self.update_free_space()

    def _sync_zip_options(self):
        """Keep Folder only means something when a zip is made."""
        self.keep_folder_box.configure(state="normal" if self.make_zip.get() else "disabled")

    def _kv(self, grid, row, label) -> tk.Label:
        tk.Label(grid, text=label, bg=BG, fg=TEXT2, font=FONT).grid(row=row, column=0, sticky="e", pady=2)
        value = tk.Label(grid, text="", bg=BG, fg=TEXT, font=FONT, anchor="w")
        value.grid(row=row, column=1, sticky="w", padx=8)
        return value

    # ── project list ─────────────────────────────────────────────────────────
    def choose_projects(self):
        paths = filedialog.askopenfilenames(title="Add Premiere Pro projects",
                                            filetypes=[("Premiere Pro project", "*.prproj"),
                                                       ("All files", "*.*")])
        if paths:
            self.add_projects(list(paths))

    def on_drop(self, paths: list[str], *_):
        projects = [p for p in paths if p.lower().endswith(".prproj")]
        if projects:
            self.add_projects(projects)

    def add_projects(self, paths: list[str]):
        if self.busy:
            return
        have = {os.path.normcase(p.path) for p in self.projects}
        failed = []
        for path in paths:
            if os.path.normcase(os.path.abspath(path)) in have:
                continue
            try:
                project = prproj.PremiereProject(path)
            except prproj.ProjectError as exc:
                failed.append(f"{os.path.basename(path)}: {exc}")
                continue
            self.projects.append(project)
            have.add(os.path.normcase(project.path))
        if failed:
            messagebox.showerror(APP_TITLE, "Could not open:\n\n" + "\n\n".join(failed), parent=self)
        self.refresh_projects(select=len(self.projects) - 1)
        self.invalidate()
        if self.projects:
            self.set_status("Choose where to copy " + ("it" if len(self.projects) == 1 else "them")
                            + ", then press OK.")

    def remove_selected(self):
        index = self._selected()
        if self.busy or index is None:
            return
        del self.projects[index]
        self.refresh_projects(select=min(index, len(self.projects) - 1))
        self.invalidate()

    def refresh_projects(self, select: Optional[int] = None):
        self.project_list.delete(0, "end")
        for project in self.projects:
            self.project_list.insert("end", f"{project.name}      {project.path}")
        if self.projects:
            self.project_hint.place_forget()
            index = select if select is not None and 0 <= select < len(self.projects) else 0
            self.project_list.selection_set(index)
            self.project_list.see(index)
        else:
            self.project_hint.place(relx=0.5, rely=0.5, anchor="center")
        self.show_selected()

    def _selected(self) -> Optional[int]:
        chosen = self.project_list.curselection()
        return chosen[0] if chosen else None

    def show_selected(self):
        """Fill the sequence list from the selected project."""
        self.tree.delete(*self.tree.get_children())
        index = self._selected()
        if index is None:
            self.seq_group.title.configure(text="Sequences (all are copied)")
            self.seq_count.configure(text="")
            return
        project = self.projects[index]
        self.seq_group.title.configure(text=f"Sequences in {project.name} (all are copied)")
        bins: dict[str, str] = {"": ""}
        for entry in project.sequences:
            self.tree.insert(self._bin_node(bins, entry.bin_path), "end", text=entry.name)
        clips = sum(1 for e in project.entries if not e.is_sequence)
        self.seq_count.configure(text=f"{plural(len(project.sequences), 'sequence')} and {plural(clips, 'clip')}")

    def _bin_node(self, bins: dict[str, str], bin_path: str) -> str:
        if bin_path in bins:
            return bins[bin_path]
        parent_path, _, name = bin_path.rpartition("/")
        parent = self._bin_node(bins, parent_path)
        node = self.tree.insert(parent, "end", text=f"\U0001F4C1  {name}", open=True)
        bins[bin_path] = node
        return node

    # ── destination + size ───────────────────────────────────────────────────
    def choose_destination(self):
        path = filedialog.askdirectory(title="Copy the projects to", initialdir=self.destination.get())
        if path:
            self.destination.set(os.path.normpath(path))

    def update_free_space(self):
        dest = self.destination.get()
        probe = dest
        while probe and not os.path.isdir(probe):
            parent = os.path.dirname(probe)
            probe = parent if parent != probe else ""
        try:
            free = shutil.disk_usage(probe).free if probe else None
        except OSError:
            free = None
        self.free_label.configure(text=human_size(free) if free is not None else "Unknown")
        return free

    def invalidate(self):
        self.plans = None
        if not self.busy:
            self.size_label.configure(text="")
            self.missing_button.pack_forget()
            self.update_free_space()

    def build_plans(self) -> Optional[list[prproj.CollectPlan]]:
        if not self.projects:
            messagebox.showinfo(APP_TITLE, "Add a Premiere Pro project first.", parent=self)
            return None
        dest = self.destination.get().strip()
        if not dest or not os.path.isabs(dest):
            messagebox.showinfo(APP_TITLE, "Choose a destination folder.", parent=self)
            return None
        options = prproj.CollectOptions(
            exclude_unused=self.exclude_unused.get(),
            include_conform=self.include_conform.get(),
            include_previews=self.include_previews.get(),
            rename_to_clip_names=self.rename_media.get(),
            copy_aroll=self.copy_aroll.get(),
            make_zip=self.make_zip.get(),
            keep_folder=self.keep_folder.get(),
        )
        plans, reserved = [], set()
        for project in self.projects:
            try:
                plan = project.plan(dest, options, reserved=reserved)
            except prproj.ProjectError as exc:
                messagebox.showerror(APP_TITLE, f"{project.name}:\n{exc}", parent=self)
                return None
            reserved.add(os.path.normcase(plan.out_dir))
            plans.append(plan)
        self.plans = plans
        return plans

    def calculate(self):
        if self.busy:
            return
        plans = self.plans or self.build_plans()
        if plans:
            self.show_plans(plans)

    def show_plans(self, plans: list[prproj.CollectPlan]):
        total = sum(p.total_bytes for p in plans)
        files = sum(len(p.jobs) for p in plans)
        size = f"{human_size(total)}  ({plural(files, 'file')})"
        if any(p.zip_path for p in plans):
            size += f", {human_size(sum(p.needed_bytes for p in plans))} free space needed while zipping"
        self.size_label.configure(text=size)
        text = self._describe(plans[0]) if len(plans) == 1 else "\n".join(
            [f"{plural(len(plans), 'project')} will be copied to {self.destination.get()}:"]
            + [f"  {p.project_name}: {plural(len(p.kept_sequences), 'sequence')}, "
               f"{human_size(p.total_bytes)} -> {os.path.basename(self._target(p))}" for p in plans])
        missing = sum(len(p.missing) for p in plans)
        color = TEXT2
        if missing:
            text += f"\nOffline media: {plural(missing, 'file')} that cannot be copied stay offline."
            color = WARN
            self.missing_button.pack(side="left", padx=(0, 8))
        else:
            self.missing_button.pack_forget()
        self.set_status(text, color)

    @staticmethod
    def _target(plan: prproj.CollectPlan) -> str:
        return plan.out_dir if plan.keep_folder else plan.zip_path

    def _describe(self, plan: prproj.CollectPlan) -> str:
        target = self._target(plan)
        if plan.zip_path and plan.keep_folder:
            target += "\nand zipped to " + os.path.basename(plan.zip_path)
        text = (f"{plural(len(plan.kept_sequences), 'sequence')} and "
                f"{plural(plan.kept_clips, 'clip')} will be copied to\n{target}")
        if plan.relinked:
            text += f"\nThe project was moved: {plural(len(plan.relinked), 'file')} found at their new location."
        if plan.skipped:
            size = human_size(sum(j.size for j in plan.skipped))
            text += f"\nA-roll not copied: {plural(len(plan.skipped), 'file')}, {size}. They stay linked where they are."
        return text

    def show_missing(self):
        if not self.plans or not any(p.missing for p in self.plans):
            return
        win = tk.Toplevel(self, bg=BG)
        win.title("Offline media")
        win.geometry("700x320")
        tk.Label(win, text="These files could not be found, so they were not copied. "
                           "They stay offline in the new project.",
                 bg=BG, fg=TEXT, font=FONT, wraplength=660, justify="left").pack(anchor="w", padx=12, pady=8)
        box = tk.Text(win, bg=FIELD, fg=TEXT, font=("Consolas", 9), relief="flat")
        box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        lines = []
        for plan in self.plans:
            if plan.missing:
                lines += [f"{plan.project_name}:"] + [f"  {path}" for path in plan.missing] + [""]
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")

    # ── copy ─────────────────────────────────────────────────────────────────
    def start_copy(self):
        if self.busy:
            return
        plans = self.plans or self.build_plans()
        if not plans:
            return
        self.show_plans(plans)
        free = self.update_free_space()
        needed = sum(p.needed_bytes for p in plans)
        if free is not None and needed > free:
            messagebox.showerror(APP_TITLE, f"Not enough disk space.\n\nNeeded: {human_size(needed)}"
                                            f"\nAvailable: {human_size(free)}", parent=self)
            return
        missing = sum(len(p.missing) for p in plans)
        if missing and not messagebox.askyesno(
                APP_TITLE, f"{plural(missing, 'media file')} offline and cannot be copied.\n\n"
                           "Copy anyway?", parent=self):
            return
        self.busy = True
        self.cancel_flag.clear()
        for widget in (self.ok_button, self.add_button, self.remove_button):
            widget.configure(state="disabled")
        self.open_button.pack_forget()
        self.reset_button.pack_forget()
        self.progress.configure(value=0)
        threading.Thread(target=self._copy_worker, args=(plans,), daemon=True).start()

    def _copy_worker(self, plans: list[prproj.CollectPlan]):
        """Copy each project in turn; one failing does not stop the others."""
        weights = [p.total_bytes * (2 if p.zip_path else 1) for p in plans]
        grand = sum(weights) or 1
        results = []  # (plan, errors or None when the project failed, failure text)
        offset = 0
        for number, (plan, weight) in enumerate(zip(plans, weights), start=1):
            self.running = plan

            def progress(done, _total, current, number=number, offset=offset):
                self.events.put(("progress", offset + done, grand, current, number, len(plans)))
            try:
                results.append((plan, prproj.execute(plan, progress, self.cancel_flag.is_set), ""))
            except prproj.Cancelled:
                self.events.put(("cancelled", plan, results))
                return
            except Exception:
                results.append((plan, None, traceback.format_exc()))
            offset += weight
        self.events.put(("done", results))

    def _pump(self):
        try:
            while True:
                event = self.events.get_nowait()
                getattr(self, f"_on_{event[0]}")(*event[1:])
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _on_progress(self, done, total, current, number, count):
        self.progress.configure(value=1000 * min(done, total) / total if total else 1000)
        zipping = self.running and os.path.normcase(current).startswith(
            os.path.normcase(self.running.out_dir + os.sep))
        head = f"Project {number} of {count} ({self.running.project_name}): " if count > 1 else ""
        self.set_status(f"{head}{'Zipping' if zipping else 'Copying'} {os.path.basename(current)}\n"
                        f"{human_size(done)} of {human_size(total)}")

    def _finish(self):
        self.busy = False
        for widget in (self.ok_button, self.add_button, self.remove_button):
            widget.configure(state="normal")
        self.plans = None  # the next run gets fresh Copied_..._001 folders
        self.reset_button.pack(side="left", padx=(0, 8))
        self.missing_button.pack_forget()  # it belonged to the batch that just ran

    def _on_done(self, results):
        self._finish()
        self.progress.configure(value=1000)
        finished = [plan for plan, errors, _ in results if errors is not None]
        if len(results) == 1 and finished:
            plan = finished[0]
            self.last_output = self._target(plan)
        else:
            self.last_output = self.destination.get()
        if finished:
            self.open_button.pack(side="left", padx=(0, 8))
        batch = len(results) > 1
        lines, color, problems = [], GOOD, []
        for plan, errors, failure in results:
            result = plan.out_project if plan.keep_folder else plan.zip_path
            # In a batch, show each result relative to the destination so lines stay short.
            shown = os.path.relpath(result, self.destination.get()) if batch else result
            if errors is None:
                lines.append(f"  {plan.project_name}: failed")
                problems.append(f"{plan.project_name} failed:\n{failure.strip().splitlines()[-1]}")
                color = ERROR
            elif errors:
                lines.append(f"  {plan.project_name}: {plural(len(errors), 'error')} -> {shown}")
                problems.append(f"{plan.project_name}:\n" + "\n".join(errors[:10]))
                color = WARN if color == GOOD else color
            else:
                lines.append(f"  {plan.project_name} -> {shown}" if batch else f"New project:\n{result}")
                if plan.zip_path and plan.keep_folder and not batch:
                    lines.append("Zip: " + plan.zip_path)
        head = "Done." if color == GOOD else "Finished with problems."
        if batch:
            head += f" Copied to {self.destination.get()}:"
        self.set_status(head + "\n" + "\n".join(lines), color)
        if problems:
            messagebox.showwarning(APP_TITLE, "\n\n".join(problems)[-2000:], parent=self)

    def _on_cancelled(self, plan: prproj.CollectPlan, results):
        self._finish()
        self.progress.configure(value=0)
        done = f"{plural(len(results), 'project')} finished before stopping. " if results else ""
        self.set_status(f"Cancelled. {done}Files already copied for {plan.project_name} are in\n"
                        f"{plan.out_dir}", WARN)

    def reset(self):
        """Clear the batch for the next one; the destination and options stay."""
        if self.busy:
            return
        self.projects = []
        self.refresh_projects()
        self.progress.configure(value=0)
        self.set_status("")
        self.last_output = None
        for widget in (self.open_button, self.reset_button, self.missing_button):
            widget.pack_forget()
        self.invalidate()

    def on_cancel(self):
        if self.busy:
            if messagebox.askyesno(APP_TITLE, "Stop copying?", parent=self):
                self.cancel_flag.set()
        else:
            self.destroy()

    def open_output(self):
        """Open the copied folder, or show the zip in Explorer when only the zip is left."""
        if not self.last_output or not os.path.exists(self.last_output):
            return
        if os.path.isfile(self.last_output) and os.name == "nt":
            subprocess.Popen(["explorer", "/select,", self.last_output])
        elif os.name == "nt":
            os.startfile(self.last_output)
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(self.last_output)
                              if os.path.isfile(self.last_output) else self.last_output])

    def set_status(self, text: str, color: str = TEXT2):
        self.status.configure(text=text, fg=color)


def main():
    initial = [a for a in sys.argv[1:] if a.lower().endswith(".prproj")]
    app = ProjectManagerApp(initial)
    app.protocol("WM_DELETE_WINDOW", app.on_cancel)
    app.mainloop()


if __name__ == "__main__":
    main()
