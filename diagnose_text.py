"""
diagnose_text.py

Standalone diagnostic - everything happens on your machine only, nothing
is sent anywhere. Looks for embedded readable text (household names,
sim names, creator usernames, etc.) inside a tray creation's files,
either as plain UTF-8 or as UTF-16LE (the common Windows/EA-game text
encoding, where each ASCII character is followed by a 0x00 byte).

Two modes:
  1. General scan: prints every readable text run found above a minimum
     length, so you can eyeball which one looks like a household name
     or username.
  2. Targeted search: if you already know a real name/username that
     SHOULD be in this creation (e.g. from the actual Tray Importer
     app), pass --find "that text" to search for it directly in both
     encodings - a hit conclusively proves where/how it's stored.

Usage:
    python3 diagnose_text.py "/path/to/Tray" --instance 0x00a0170f49b80050
    python3 diagnose_text.py "/path/to/Tray" --instance 0x00a0170f49b80050 --find "Madden"
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trayscan

# A run of 4+ printable ASCII characters, as plain UTF-8/ASCII bytes.
_UTF8_RUN = re.compile(rb"[\x20-\x7e]{4,}")


def find_utf8_strings(data, min_len=4):
    hits = []
    for m in _UTF8_RUN.finditer(data):
        s = m.group()
        if len(s) >= min_len:
            hits.append((m.start(), s.decode("ascii")))
    return hits


def find_utf16le_strings(data, min_len=4):
    """UTF-16LE text shows up as: char, 0x00, char, 0x00, ... To find
    it without a known starting point, decode the whole buffer as
    UTF-16LE (ignoring errors) and pull out printable runs from that."""
    hits = []
    try:
        text = data.decode("utf-16-le", errors="ignore")
    except Exception:
        return hits

    run_start = None
    run_chars = []
    for i, ch in enumerate(text):
        printable = ch.isprintable() and ch not in "\x00"
        if printable:
            if run_start is None:
                run_start = i
            run_chars.append(ch)
        else:
            if run_start is not None and len(run_chars) >= min_len:
                byte_offset = run_start * 2
                hits.append((byte_offset, "".join(run_chars)))
            run_start = None
            run_chars = []
    if run_start is not None and len(run_chars) >= min_len:
        hits.append((run_start * 2, "".join(run_chars)))
    return hits


def search_for(data, needle):
    """Search for a specific known string in both UTF-8 and UTF-16LE
    encodings, returning a list of (encoding, byte_offset) hits."""
    hits = []
    utf8_needle = needle.encode("utf-8")
    utf16_needle = needle.encode("utf-16-le")
    start = 0
    while True:
        i = data.find(utf8_needle, start)
        if i == -1:
            break
        hits.append(("UTF-8", i))
        start = i + 1
    start = 0
    while True:
        i = data.find(utf16_needle, start)
        if i == -1:
            break
        hits.append(("UTF-16LE", i))
        start = i + 1
    # also try case-insensitive as a fallback
    if not hits:
        lower_data_utf8 = data.lower()
        if utf8_needle.lower() in lower_data_utf8:
            hits.append(("UTF-8 (case-insensitive)", lower_data_utf8.find(utf8_needle.lower())))
    return hits


def looks_interesting(s):
    """Filter out obvious noise (pure hex-looking runs, single repeated
    characters) so the general scan output is easier to skim."""
    if len(set(s)) <= 1:
        return False
    if re.fullmatch(r"[0-9a-fA-F]{6,}", s):
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tray_folder")
    ap.add_argument("--instance", help="target a specific creation's instance id")
    ap.add_argument("--find", help="search for this exact text (e.g. a known household/creator name)")
    ap.add_argument("--min-len", type=int, default=4)
    args = ap.parse_args()

    creations, _unmatched = trayscan.group_tray_folder(args.tray_folder)
    if not creations:
        print("No creations found.")
        return

    if args.instance:
        target = args.instance.lower()
        matches = [c for c in creations if c["instance"] == target]
        if not matches:
            print(f"No creation with instance {target} found. Available:")
            for c in creations:
                print(f"  {c['instance']}  ({c['kind']}, {len(c['files'])} files)")
            return
        creation = matches[0]
    else:
        creation = creations[0]
        print(f"No --instance given, using the first creation found: "
              f"{creation['instance']} ({creation['kind']})\n")

    for path in creation["files"]:
        with open(path, "rb") as f:
            data = f.read()
        print("=" * 70)
        print(f"{os.path.basename(path)}  ({len(data)} bytes)")
        print("=" * 70)

        if args.find:
            hits = search_for(data, args.find)
            if hits:
                for encoding, offset in hits:
                    print(f"  [MATCH] found {args.find!r} as {encoding} at offset {offset}")
            else:
                print(f"  {args.find!r} not found in this file")
        else:
            utf8_hits = [(o, s) for o, s in find_utf8_strings(data, args.min_len) if looks_interesting(s)]
            utf16_hits = [(o, s) for o, s in find_utf16le_strings(data, args.min_len) if looks_interesting(s)]

            print(f"  UTF-8 text runs ({len(utf8_hits)}):")
            for offset, s in utf8_hits[:40]:
                print(f"    offset {offset}: {s!r}")
            if len(utf8_hits) > 40:
                print(f"    ...and {len(utf8_hits) - 40} more")

            print(f"  UTF-16LE text runs ({len(utf16_hits)}):")
            for offset, s in utf16_hits[:40]:
                print(f"    offset {offset}: {s!r}")
            if len(utf16_hits) > 40:
                print(f"    ...and {len(utf16_hits) - 40} more")
        print()


if __name__ == "__main__":
    main()
