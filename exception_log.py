"""
exception_log.py
Finds and parses the crash logs Sims 4 itself writes when a mod's script
throws an error: lastException.txt (gameplay scripts) and
lastUIException.txt (UI scripts). These live in the main Sims 4 folder
(the same parent folder as Tray/Mods), not inside Mods itself.

Unlike file-structure checks (dbpf.py), these logs are the game's own
authoritative account of which mod actually crashed something - this is
generic Python traceback parsing, not anything specific to Sims 4's file
formats.
"""

import os
import re

EXCEPTION_FILENAMES = ["lastException.txt", "lastUIException.txt"]

# A standard Python traceback frame line: File "<path>", line N, in <func>
_FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)')


def find_exception_logs(sims4_folder):
    """Returns a list of (path, mtime) for whichever exception log files
    exist directly in the Sims 4 folder (not Mods/Tray)."""
    found = []
    for name in EXCEPTION_FILENAMES:
        path = os.path.join(sims4_folder, name)
        if os.path.isfile(path):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            found.append((path, mtime))
    return found


def _looks_like_mod_path(path):
    lower = path.lower()
    return ".ts4script" in lower or "mods" in lower or "mod_data" in lower


def parse_exception_text(text):
    """
    Parses the raw text of a lastException.txt/lastUIException.txt file.

    Returns a dict:
      summary: the likely exception type/message (last non-blank line),
               or None if the file looks empty.
      mod_frames: list of (path, line_no, func) for every traceback frame
                  whose path looks like it points into a mod (.ts4script
                  or a Mods-folder path) - these are the actual "which
                  mod did this" candidates.
      all_frames: every traceback frame found, mod or not (game/engine
                  frames included), for full context.
      raw: the original text, unmodified.
    """
    lines = text.splitlines()
    non_blank = [ln for ln in lines if ln.strip()]
    summary = non_blank[-1].strip() if non_blank else None

    all_frames = []
    mod_frames = []
    for m in _FRAME_RE.finditer(text):
        path = m.group("path")
        line_no = int(m.group("line"))
        func = m.group("func")
        frame = (path, line_no, func)
        all_frames.append(frame)
        if _looks_like_mod_path(path):
            mod_frames.append(frame)

    return {
        "summary": summary,
        "mod_frames": mod_frames,
        "all_frames": all_frames,
        "raw": text,
    }


def read_and_parse(path):
    """Reads a log file and parses it. Returns None if it can't be read
    (rather than raising) since a missing/unreadable log is an expected,
    non-fatal situation for callers to handle gracefully."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    return parse_exception_text(text)


def guess_mod_filename(mod_frame_path):
    """From a traceback frame path, pull out just the .ts4script filename
    if present (the most user-recognizable part), else the last path
    component."""
    lower = mod_frame_path.lower()
    idx = lower.find(".ts4script")
    if idx != -1:
        # walk back to the last path separator before this point
        end = idx + len(".ts4script")
        start = max(mod_frame_path.rfind("/", 0, end), mod_frame_path.rfind("\\", 0, end))
        return mod_frame_path[start + 1:end]
    return os.path.basename(mod_frame_path)
