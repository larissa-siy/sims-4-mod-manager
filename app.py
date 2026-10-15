"""
Sims 4 Tray & CC Health Checker (Mac)
--------------------------------------
A from-scratch personal tool for browsing your Sims 4 Tray folder and
checking your Mods folder for corrupt/duplicate custom content. Built
against publicly documented file formats - see dbpf.py and trayscan.py
for notes on what's solid vs. best-effort.

Run with:  python3 app.py
Requires:  Python 3.9+, Pillow (pip3 install pillow) for thumbnail
           display. The app still runs without Pillow, just with plain
           colored placeholder tiles instead of real thumbnails.
"""

import os
import sys
import threading
import traceback
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbpf
import trayscan
import thumbnails
import exception_log

print(f"[startup] Pillow available (thumbnails.HAVE_PIL): {thumbnails.HAVE_PIL}")

# Tile width (image + horizontal padding) used to work out how many
# gallery columns fit in the current pane width.
GALLERY_THUMB_SUBSAMPLE = 2  # shrink the stored 140x100 thumbnail for compact list rows

# --- hand-rolled dark theme, no extra dependency required ---
DARK_BG = "#1e1e1e"
DARK_FIELD_BG = "#2b2b2b"
DARK_FG = "#e6e6e6"
DARK_MUTED_FG = "#9a9a9a"
ACCENT = "#3a7ca5"
BUTTON_BG = "#333333"


def apply_dark_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # ships with Tcl/Tk everywhere, needed as a base to recolor
    except tk.TclError:
        pass

    root.configure(bg=DARK_BG)

    style.configure(".", background=DARK_BG, foreground=DARK_FG,
                     fieldbackground=DARK_FIELD_BG, bordercolor=DARK_FIELD_BG)
    style.configure("TFrame", background=DARK_BG)
    style.configure("TLabel", background=DARK_BG, foreground=DARK_FG)
    style.configure("Muted.TLabel", background=DARK_BG, foreground=DARK_MUTED_FG)
    style.configure("TButton", background=BUTTON_BG, foreground=DARK_FG, padding=6, borderwidth=0)
    style.map("TButton", background=[("active", ACCENT)])
    style.configure("TEntry", fieldbackground=DARK_FIELD_BG, foreground=DARK_FG, insertcolor=DARK_FG)
    style.configure("TNotebook", background=DARK_BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=DARK_FIELD_BG, foreground=DARK_FG, padding=(12, 6))
    style.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=DARK_FIELD_BG, borderwidth=0)
    style.configure("Vertical.TScrollbar", background=BUTTON_BG, troughcolor=DARK_BG, arrowcolor=DARK_FG)

    style.configure("TCombobox", fieldbackground=DARK_FIELD_BG, background=BUTTON_BG,
                     foreground=DARK_FG, arrowcolor=DARK_FG, selectbackground=DARK_FIELD_BG,
                     selectforeground=DARK_FG)
    style.map("TCombobox",
              fieldbackground=[("readonly", DARK_FIELD_BG)],
              foreground=[("readonly", DARK_FG)],
              background=[("readonly", BUTTON_BG)])
    # the dropdown popup list is a plain Tk listbox under the hood, not
    # reachable through ttk.Style - set it via the option database instead
    root.option_add("*TCombobox*Listbox.background", DARK_FIELD_BG)
    root.option_add("*TCombobox*Listbox.foreground", DARK_FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    return style


def style_classic_listbox(listbox):
    """tk.Listbox (and Canvas) predate ttk and aren't touched by
    ttk.Style - they need their colors set directly."""
    listbox.configure(
        bg=DARK_FIELD_BG, fg=DARK_FG,
        selectbackground=ACCENT, selectforeground="#ffffff",
        highlightthickness=0, borderwidth=0,
    )


def default_sims4_folder():
    home = os.path.expanduser("~")
    return os.path.join(home, "Documents", "Electronic Arts", "The Sims 4")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sims 4 Tray & CC Health Checker")
        self.geometry("1100x700")
        apply_dark_theme(self)
        self._set_window_icon()

        base = default_sims4_folder()
        self.tray_folder = tk.StringVar(value=os.path.join(base, "Tray"))
        self.mods_folder = tk.StringVar(value=os.path.join(base, "Mods"))
        self.tray_filter_var = tk.StringVar()
        self.kind_filter_var = tk.StringVar(value=trayscan.KIND_FILTER_OPTIONS[0])
        self.sort_var = tk.StringVar(value=trayscan.SORT_OPTIONS[0])
        self.mods_filter_var = tk.StringVar()

        self.creations = []
        self.creation_thumbs = {}   # instance -> PNG bytes (or None)
        self.mods_index = {}
        self.mods_good = []
        self.mods_broken = []
        self._conflict_lines = []   # precomputed "pkg, pkg, pkg" strings for filtering
        self.selected_creation = None
        self._selected_tile = None
        self._thumb_cache = {}      # keep PhotoImage refs alive (gallery tiles + big preview)
        self._last_filtered = []    # last-rendered gallery contents, for re-render on resize

        self._build_folder_bar()
        self._build_progress_row()
        self._build_notebook()
        self._build_statusbar()
        self.after_idle(self._set_initial_sash_position)

    # ---------- layout ----------

    def _set_window_icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app_icon_256.png")
        try:
            icon_img = tk.PhotoImage(file=icon_path)
            self.iconphoto(True, icon_img)
            self._icon_ref = icon_img  # keep alive - PhotoImage is GC'd otherwise
        except Exception:
            pass  # fine to run without a custom icon if this ever fails

    def _build_folder_bar(self):
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")

        ttk.Label(bar, text="Tray folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(bar, textvariable=self.tray_folder, width=60).grid(row=0, column=1, padx=4)
        ttk.Button(bar, text="Browse...", command=self._pick_tray).grid(row=0, column=2)
        ttk.Button(bar, text="Scan Tray", command=self._scan_tray_clicked).grid(row=0, column=3, padx=(8, 0))

        ttk.Label(bar, text="Mods folder:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(bar, textvariable=self.mods_folder, width=60).grid(row=1, column=1, padx=4, pady=(6, 0))
        ttk.Button(bar, text="Browse...", command=self._pick_mods).grid(row=1, column=2, pady=(6, 0))
        ttk.Button(bar, text="Scan Mods", command=self._scan_mods_clicked).grid(row=1, column=3, padx=(8, 0), pady=(6, 0))

    def _build_progress_row(self):
        frame = ttk.Frame(self, padding=(8, 0, 8, 4))
        frame.pack(fill="x")
        self.progress_bar = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.progress_bar.pack(fill="x")

    def _build_notebook(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)

        # --- Tray tab ---
        self.tray_tab = ttk.Frame(self.nb)
        self.nb.add(self.tray_tab, text="Tray Creations")

        # A resizable split (drag the divider to see more of either side),
        # instead of a fixed-width side-by-side layout.
        self.tray_paned = ttk.PanedWindow(self.tray_tab, orient="horizontal")
        self.tray_paned.pack(fill="both", expand=True)

        left = ttk.Frame(self.tray_paned)
        self.tray_paned.add(left, weight=1)

        controls = ttk.Frame(left)
        controls.pack(fill="x", pady=(0, 4), padx=(0, 8))

        search_row = ttk.Frame(controls)
        search_row.pack(fill="x")
        ttk.Label(search_row, text="Search:").pack(side="left")
        search_entry = ttk.Entry(search_row, textvariable=self.tray_filter_var, width=30)
        search_entry.pack(side="left", padx=(4, 0), fill="x", expand=True)
        self.tray_filter_var.trace_add("write", lambda *_: self._apply_tray_filter())

        filter_sort_row = ttk.Frame(controls)
        filter_sort_row.pack(fill="x", pady=(6, 0))

        ttk.Label(filter_sort_row, text="Type:").pack(side="left")
        kind_combo = ttk.Combobox(filter_sort_row, textvariable=self.kind_filter_var,
                                   values=trayscan.KIND_FILTER_OPTIONS, state="readonly", width=14)
        kind_combo.pack(side="left", padx=(4, 14))
        kind_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_tray_filter())

        ttk.Label(filter_sort_row, text="Sort:").pack(side="left")
        sort_combo = ttk.Combobox(filter_sort_row, textvariable=self.sort_var,
                                   values=trayscan.SORT_OPTIONS, state="readonly", width=10)
        sort_combo.pack(side="left", padx=(4, 0))
        sort_combo.bind("<<ComboboxSelected>>", lambda e: self._apply_tray_filter())

        gallery_holder = ttk.Frame(left)
        gallery_holder.pack(fill="both", expand=True, padx=(0, 8))

        self.gallery_canvas = tk.Canvas(gallery_holder, bg=DARK_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(gallery_holder, orient="vertical", command=self.gallery_canvas.yview)
        self.gallery_canvas.configure(yscrollcommand=vsb.set)
        self.gallery_canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.gallery_inner = ttk.Frame(self.gallery_canvas)
        self.gallery_window = self.gallery_canvas.create_window((0, 0), window=self.gallery_inner, anchor="nw")
        self.gallery_inner.bind("<Configure>", lambda e: self.gallery_canvas.configure(
            scrollregion=self.gallery_canvas.bbox("all")))
        self.gallery_canvas.bind("<MouseWheel>", self._on_gallery_mousewheel)
        self.gallery_canvas.bind("<Configure>", self._on_gallery_canvas_resize)

        right = ttk.Frame(self.tray_paned)
        self.tray_paned.add(right, weight=2)

        details_header = ttk.Frame(right)
        details_header.pack(fill="x", anchor="nw", pady=(0, 8))

        self.thumb_label = tk.Label(details_header, text="(select a creation)", bg=DARK_BG, fg=DARK_FG)
        self.thumb_label.pack(side="left", padx=(0, 12))

        header_text = tk.Frame(details_header, bg=DARK_BG)
        header_text.pack(side="left", fill="y", anchor="n", pady=4)
        self.detail_name_label = tk.Label(header_text, text="", font=("TkDefaultFont", 14, "bold"),
                                           bg=DARK_BG, fg=DARK_FG, anchor="w")
        self.detail_name_label.pack(fill="x", anchor="w")
        self.detail_creator_label = tk.Label(header_text, text="", font=("TkDefaultFont", 11),
                                              bg=DARK_BG, fg=ACCENT, anchor="w")
        self.detail_creator_label.pack(fill="x", anchor="w")
        self.detail_date_label = tk.Label(header_text, text="", font=("TkDefaultFont", 10),
                                           bg=DARK_BG, fg=DARK_MUTED_FG, anchor="w")
        self.detail_date_label.pack(fill="x", anchor="w")

        ttk.Label(right, text="Files in this creation:").pack(anchor="w")
        self.files_box = tk.Listbox(right, height=8)
        style_classic_listbox(self.files_box)
        self.files_box.pack(fill="x")

        ttk.Label(right, text="Custom content likely present (matched against your Mods folder):").pack(
            anchor="w", pady=(10, 0))
        self.cc_box = tk.Listbox(right, height=10)
        style_classic_listbox(self.cc_box)
        self.cc_box.pack(fill="both", expand=True)

        note = ("Note: matches are found via each resource's instance ID (a large, "
                "effectively-random 64-bit number), not the full type+group+instance key - "
                "see trayscan.py for why. False positives are very unlikely but not "
                "mathematically impossible. This also only shows CC that IS installed; it "
                "can't fully guarantee-detect CC that's missing entirely - use the Mods "
                "Health tab to catch corrupt/duplicate packages, the more common cause of breakage.")
        ttk.Label(right, text=note, wraplength=550, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        # --- Mods health tab ---
        self.mods_tab = ttk.Frame(self.nb)
        self.nb.add(self.mods_tab, text="Mods Health")

        summary = ttk.Frame(self.mods_tab)
        summary.pack(fill="x", pady=(4, 4))
        self.mods_summary_label = ttk.Label(summary, text="Run 'Scan Mods' to check your Mods folder.")
        self.mods_summary_label.pack(anchor="w")

        filter_row = ttk.Frame(self.mods_tab)
        filter_row.pack(fill="x", pady=(0, 8))
        ttk.Label(filter_row, text="Filter (by filename):").pack(side="left")
        filter_entry = ttk.Entry(filter_row, textvariable=self.mods_filter_var, width=40)
        filter_entry.pack(side="left", padx=(4, 0))
        self.mods_filter_var.trace_add("write", lambda *_: self._apply_mods_filter())

        ttk.Label(self.mods_tab, text="Broken / unreadable packages:").pack(anchor="w")
        self.broken_box = tk.Listbox(self.mods_tab, height=10)
        style_classic_listbox(self.broken_box)
        self.broken_box.pack(fill="x")

        ttk.Label(self.mods_tab, text="Duplicate/conflicting resources (2+ packages define the same content):").pack(
            anchor="w", pady=(10, 0))
        self.conflict_box = tk.Listbox(self.mods_tab, height=10)
        style_classic_listbox(self.conflict_box)
        self.conflict_box.pack(fill="both", expand=True)

        # --- Crash Log tab ---
        self.crash_tab = ttk.Frame(self.nb)
        self.nb.add(self.crash_tab, text="Crash Log")

        crash_intro = ("Structural checks (like the Mods Health tab) can't tell whether a "
                        "well-formed mod is actually incompatible with your current game version - "
                        "that's the far more common cause of crashes/lots not loading after a patch. "
                        "This reads the crash log the game itself writes when a mod's script errors "
                        "out, which names the actual culprit directly.")
        ttk.Label(self.crash_tab, text=crash_intro, wraplength=900, style="Muted.TLabel").pack(
            anchor="w", pady=(4, 8))

        ttk.Button(self.crash_tab, text="Check Crash Log", command=self._check_crash_log_clicked).pack(anchor="w")

        self.crash_summary_label = ttk.Label(self.crash_tab, text="Not checked yet.", wraplength=900)
        self.crash_summary_label.pack(anchor="w", pady=(10, 4))

        ttk.Label(self.crash_tab, text="Likely culprit mod(s):").pack(anchor="w", pady=(6, 0))
        self.crash_culprit_box = tk.Listbox(self.crash_tab, height=5)
        style_classic_listbox(self.crash_culprit_box)
        self.crash_culprit_box.pack(fill="x")

        ttk.Label(self.crash_tab, text="Full log (for manual inspection):").pack(anchor="w", pady=(10, 0))
        crash_text_holder = ttk.Frame(self.crash_tab)
        crash_text_holder.pack(fill="both", expand=True)
        self.crash_text = tk.Text(crash_text_holder, wrap="word", height=12)
        style_classic_listbox(self.crash_text)
        crash_vsb = ttk.Scrollbar(crash_text_holder, orient="vertical", command=self.crash_text.yview)
        self.crash_text.configure(yscrollcommand=crash_vsb.set)
        self.crash_text.pack(side="left", fill="both", expand=True)
        crash_vsb.pack(side="right", fill="y")

    def _build_statusbar(self):
        self.status = tk.StringVar(value="Ready.")
        bar = tk.Label(self, textvariable=self.status, relief="sunken", anchor="w",
                        bg=DARK_FIELD_BG, fg=DARK_MUTED_FG)
        bar.pack(fill="x", side="bottom")

    # ---------- progress helpers ----------

    def _progress_determinate(self, value, maximum):
        self.progress_bar.config(mode="determinate", maximum=max(maximum, 1))
        self.progress_bar["value"] = value

    def _progress_indeterminate(self, running):
        if running:
            self.progress_bar.config(mode="indeterminate")
            self.progress_bar.start(12)
        else:
            self.progress_bar.stop()
            self.progress_bar.config(mode="determinate")
            self.progress_bar["value"] = 0

    # ---------- folder pickers ----------

    def _pick_tray(self):
        d = filedialog.askdirectory(initialdir=self.tray_folder.get() or os.path.expanduser("~"))
        if d:
            self.tray_folder.set(d)

    def _pick_mods(self):
        d = filedialog.askdirectory(initialdir=self.mods_folder.get() or os.path.expanduser("~"))
        if d:
            self.mods_folder.set(d)

    # ---------- tray scan ----------

    def _scan_tray_clicked(self):
        folder = self.tray_folder.get()
        if not os.path.isdir(folder):
            messagebox.showerror("Not found", f"Tray folder not found:\n{folder}")
            return
        self.status.set("Scanning Tray folder...")
        self._progress_indeterminate(True)
        threading.Thread(target=self._scan_tray_worker, args=(folder,), daemon=True).start()

    def _scan_tray_worker(self, folder):
        try:
            creations, unmatched = trayscan.group_tray_folder(folder)

            self.after(0, self._progress_indeterminate, False)
            self.after(0, self.status.set, f"Generating thumbnails for {len(creations)} creations...")

            thumbs = {}
            total = len(creations)
            for i, c in enumerate(creations):
                raw = trayscan.extract_thumbnail_bytes(c["files"])
                pil_img = thumbnails.thumbnail_or_placeholder(raw, c["kind"])
                thumbs[c["instance"]] = thumbnails.image_to_png_bytes(pil_img) if pil_img is not None else None
                name, creator = trayscan.extract_name_and_creator(c)
                c["name"] = name
                c["creator"] = creator
                self.after(0, self._progress_determinate, i + 1, total)

            self.after(0, self._scan_tray_done, creations, unmatched, thumbs)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see _report_thread_error
            self.after(0, self._progress_indeterminate, False)
            self.after(0, self._report_thread_error, "Tray scan", exc)

    def _scan_tray_done(self, creations, unmatched, thumbs):
        self.creations = creations
        self.creation_thumbs = thumbs
        self.selected_creation = None
        self._selected_tile = None
        self.tray_filter_var.set("")  # triggers _apply_tray_filter via its trace -> renders gallery
        extra = f"  ({len(unmatched)} files didn't match the expected naming pattern)" if unmatched else ""
        self.status.set(f"Found {len(creations)} creations in Tray folder.{extra}")
        self.progress_bar["value"] = 0

    def _apply_tray_filter(self):
        filtered = trayscan.filter_creations(self.creations, self.tray_filter_var.get())
        filtered = trayscan.filter_by_kind(filtered, self.kind_filter_var.get())
        filtered = trayscan.sort_creations(filtered, self.sort_var.get())
        self._last_filtered = filtered
        self._render_gallery(filtered)

    def _on_gallery_mousewheel(self, event):
        delta = event.delta
        if delta == 0:
            return
        self.gallery_canvas.yview_scroll(-1 if delta > 0 else 1, "units")

    def _on_gallery_canvas_resize(self, event):
        # Rows are single-column and stretch full width - keep the inner
        # frame's width matched to the canvas so row highlights/backgrounds
        # extend edge-to-edge instead of only as wide as their content.
        self.gallery_canvas.itemconfig(self.gallery_window, width=event.width)

    def _set_initial_sash_position(self):
        try:
            self.tray_paned.sashpos(0, 480)
        except tk.TclError:
            pass  # window not fully realized yet on some platforms; not critical

    def _render_gallery(self, filtered_creations):
        for child in self.gallery_inner.winfo_children():
            child.destroy()
        self._selected_tile = None

        for creation in filtered_creations:
            self._build_row(self.gallery_inner, creation)

    def _make_photo_image(self, instance):
        """Builds a tk.PhotoImage from this creation's stored PNG bytes,
        or None if there's nothing to show. Uses Tk's own native PNG
        decoder (tk.PhotoImage(data=...)) rather than PIL.ImageTk - see
        thumbnails.py's docstring for why."""
        png_bytes = self.creation_thumbs.get(instance)
        if not png_bytes:
            return None
        try:
            return tk.PhotoImage(data=png_bytes)
        except tk.TclError as e:
            print(f"[gallery] couldn't decode thumbnail for {instance}: {e}", file=sys.stderr)
            return None

    def _format_date(self, creation):
        mtime = creation.get("mtime")
        if not mtime:
            return ""
        try:
            return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            return ""

    def _build_row(self, parent, creation):
        row = tk.Frame(parent, bg=DARK_BG, highlightthickness=2, highlightbackground=DARK_BG)
        row.pack(fill="x", padx=4, pady=2)

        photo = self._make_photo_image(creation["instance"])
        if photo is not None:
            small = photo.subsample(GALLERY_THUMB_SUBSAMPLE, GALLERY_THUMB_SUBSAMPLE)
            cache_key = f"row-{creation['instance']}"
            self._thumb_cache[cache_key] = small
            img_widget = tk.Label(row, image=small, bg=DARK_FIELD_BG, bd=0)
        else:
            img_widget = tk.Label(row, text=creation["kind"][:4].upper(), width=8, height=3,
                                   bg=BUTTON_BG, fg=DARK_FG)
        img_widget.pack(side="left", padx=(4, 10), pady=4)

        text_frame = tk.Frame(row, bg=DARK_BG)
        text_frame.pack(side="left", fill="x", expand=True, pady=6)

        display_name = creation.get("name") or creation["kind"]
        name_label = tk.Label(text_frame, text=display_name, font=("TkDefaultFont", 12, "bold"),
                               bg=DARK_BG, fg=DARK_FG, anchor="w")
        name_label.pack(fill="x", anchor="w")

        row_widgets = [row, img_widget, text_frame, name_label]

        creator = creation.get("creator")
        if creator:
            creator_label = tk.Label(text_frame, text=creator, font=("TkDefaultFont", 10),
                                      bg=DARK_BG, fg=ACCENT, anchor="w")
            creator_label.pack(fill="x", anchor="w")
            row_widgets.append(creator_label)

        date_str = self._format_date(creation)
        if date_str:
            date_label = tk.Label(text_frame, text=date_str, font=("TkDefaultFont", 9),
                                   bg=DARK_BG, fg=DARK_MUTED_FG, anchor="w")
            date_label.pack(fill="x", anchor="w")
            row_widgets.append(date_label)

        for widget in row_widgets:
            widget.bind("<Button-1>", lambda e, c=creation, r=row: self._on_gallery_tile_click(c, r))

        return row

    def _on_gallery_tile_click(self, creation, tile):
        if self._selected_tile is not None:
            self._selected_tile.config(highlightbackground=DARK_BG)
        tile.config(highlightbackground=ACCENT)
        self._selected_tile = tile
        self.selected_creation = creation

        self.detail_name_label.configure(text=creation.get("name") or creation["kind"])
        self.detail_creator_label.configure(text=creation.get("creator") or "")
        self.detail_date_label.configure(text=self._format_date(creation))

        self.files_box.delete(0, "end")
        for p in creation["files"]:
            self.files_box.insert("end", os.path.basename(p))

        self.cc_box.delete(0, "end")
        self.cc_box.insert("end", "(scanning...)")
        self._show_big_thumbnail(creation)

        threading.Thread(target=self._select_creation_worker, args=(creation,), daemon=True).start()

    def _show_big_thumbnail(self, creation):
        photo = self._make_photo_image(creation["instance"])
        if photo is not None:
            # thumbnails.TILE_SIZE is 140x100 - zoom(2, 2) doubles it to
            # 280x200 using Tk's own (nearest-neighbor) scaling, so this
            # never needs PIL/ImageTk at display time.
            big = photo.zoom(2, 2)
            self._thumb_cache["big_preview"] = big
            self.thumb_label.configure(image=big, text="")
        else:
            self.thumb_label.configure(text=f"[{creation['kind']}]", image="")

    def _select_creation_worker(self, creation):
        try:
            files = creation["files"]
            mods_folder = self.mods_folder.get()

            cc_lines = []
            if not self.mods_index:
                cc_lines.append("(scan your Mods folder first to match CC)")
            else:
                matches = trayscan.find_referenced_cc(files, self.mods_index)
                if not matches:
                    cc_lines.append("(no likely matches found)")
                else:
                    seen_paths = set()
                    for _key, paths in matches.items():
                        for p in paths:
                            if p not in seen_paths:
                                seen_paths.add(p)
                                try:
                                    cc_lines.append(os.path.relpath(p, mods_folder))
                                except ValueError:
                                    cc_lines.append(p)
        except Exception as exc:  # noqa: BLE001 - see _report_thread_error
            self.after(0, self._report_thread_error, "CC matching", exc)
            return

        self.after(0, self._select_creation_done, creation, cc_lines)

    def _select_creation_done(self, creation, cc_lines):
        if self.selected_creation is not creation:
            return  # user already clicked something else
        self.cc_box.delete(0, "end")
        for line in cc_lines:
            self.cc_box.insert("end", line)

    # ---------- error reporting ----------

    def _report_thread_error(self, context, exc):
        """Background threads can't raise into the GUI - without this, an
        exception in a worker thread just vanishes. This prints the full
        traceback to Terminal AND shows a dialog, so a silent failure
        becomes a loud, debuggable one."""
        tb_text = traceback.format_exc()
        print(f"\n--- ERROR during {context} ---\n{tb_text}", file=sys.stderr, flush=True)
        self.status.set(f"Error during {context} - see Terminal for details.")
        messagebox.showerror(f"Error during {context}", f"{exc}\n\n(Full details were printed to Terminal.)")

    # ---------- mods scan ----------

    def _scan_mods_clicked(self):
        folder = self.mods_folder.get()
        if not os.path.isdir(folder):
            messagebox.showerror("Not found", f"Mods folder not found:\n{folder}")
            return
        self.status.set("Scanning Mods folder... this can take a while for large CC collections.")
        threading.Thread(target=self._scan_mods_worker, args=(folder,), daemon=True).start()

    def _scan_mods_worker(self, folder):
        try:
            def progress(i, total, _path):
                self.after(0, self._progress_determinate, i, total)
                if i % 25 == 0 or i == total:
                    self.after(0, self.status.set, f"Scanning packages... {i}/{total}")

            good, broken, index = dbpf.scan_mods_folder(folder, progress_callback=progress)
            conflicts = dbpf.find_conflicts(index)
            self.after(0, self._scan_mods_done, good, broken, index, conflicts)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see _report_thread_error
            self.after(0, self._report_thread_error, "Mods scan", exc)

    def _scan_mods_done(self, good, broken, index, conflicts):
        self.mods_good = good
        self.mods_broken = broken
        self.mods_index = index
        self._conflict_lines = [
            ", ".join(os.path.basename(p) for p in paths) for paths in conflicts.values()
        ]

        self.mods_filter_var.set("")  # triggers _apply_mods_filter via its trace -> renders both lists

        self.mods_summary_label.configure(
            text=(f"{len(good)} packages read OK   |   "
                  f"{len(broken)} broken/unreadable   |   "
                  f"{len(conflicts)} conflicting resource(s) across packages")
        )
        self.status.set("Mods scan complete.")
        self.progress_bar["value"] = 0

    def _check_crash_log_clicked(self):
        mods_folder = self.mods_folder.get()
        sims4_folder = os.path.dirname(os.path.normpath(mods_folder)) if mods_folder else ""
        if not sims4_folder or not os.path.isdir(sims4_folder):
            messagebox.showerror("Not found", f"Couldn't find the Sims 4 folder near:\n{mods_folder}")
            return

        logs = exception_log.find_exception_logs(sims4_folder)
        if not logs:
            self.crash_summary_label.configure(
                text=f"No lastException.txt or lastUIException.txt found in:\n{sims4_folder}\n"
                     f"(That's a good sign - it means the game hasn't logged a mod script error there.)")
            self.crash_culprit_box.delete(0, "end")
            self.crash_text.delete("1.0", "end")
            return

        # if both exist, show whichever was written most recently
        logs.sort(key=lambda pair: pair[1], reverse=True)
        path, mtime = logs[0]
        parsed = exception_log.read_and_parse(path)
        if parsed is None:
            messagebox.showerror("Couldn't read file", f"Found {path} but couldn't read it.")
            return

        when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "unknown time"
        summary_lines = [f"{os.path.basename(path)}  (last written: {when})"]
        if parsed["summary"]:
            summary_lines.append(f"Exception: {parsed['summary']}")
        else:
            summary_lines.append("(file exists but appears empty)")
        self.crash_summary_label.configure(text="\n".join(summary_lines))

        self.crash_culprit_box.delete(0, "end")
        if parsed["mod_frames"]:
            seen = set()
            for frame_path, line_no, func in parsed["mod_frames"]:
                mod_name = exception_log.guess_mod_filename(frame_path)
                if mod_name not in seen:
                    seen.add(mod_name)
                    self.crash_culprit_box.insert("end", f"{mod_name}  (line {line_no}, in {func})")
        else:
            self.crash_culprit_box.insert(
                "end", "(no mod-looking file paths found in this traceback - may be a base-game/engine issue)")

        self.crash_text.delete("1.0", "end")
        self.crash_text.insert("1.0", parsed["raw"])

    def _apply_mods_filter(self):
        query = self.mods_filter_var.get().strip().lower()

        self.broken_box.delete(0, "end")
        for path, err in self.mods_broken:
            line = f"{os.path.basename(path)}  \u2014  {err}"
            if not query or query in line.lower():
                self.broken_box.insert("end", line)

        self.conflict_box.delete(0, "end")
        for line in self._conflict_lines:
            if not query or query in line.lower():
                self.conflict_box.insert("end", line)


if __name__ == "__main__":
    App().mainloop()
