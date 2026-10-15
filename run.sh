#!/bin/bash
# Convenience launcher for the Sims 4 Tray & CC Health Checker.
#
# Why this exists: on this Mac, the system's own libexpat
# (/usr/lib/libexpat.1.dylib) is missing a symbol that Homebrew's
# Python bottles expect, which breaks plistlib -> platform.mac_ver()
# -> anything that depends on it (Tk's startup check, pip, etc).
# Homebrew's own newer expat (installed via `brew install expat`) has
# what's needed - this script just points the dynamic linker at that
# copy before launching Python, without needing to change anything
# system-wide or set this permanently in your shell profile.
#
# Usage: ./run.sh   (from this folder), or double-click it in Finder
# after the one-time `chmod +x run.sh` (already done for you here).

cd "$(dirname "$0")"

if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew not found on PATH - can't locate its expat install." >&2
    echo "Falling back to a plain launch; this may fail with the libexpat error." >&2
    /opt/homebrew/bin/python3.13 app.py
    exit $?
fi

export DYLD_LIBRARY_PATH="$(brew --prefix expat)/lib"
exec /opt/homebrew/bin/python3.13 app.py
