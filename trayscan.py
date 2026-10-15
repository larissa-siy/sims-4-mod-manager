"""
trayscan.py
Scans a Sims 4 "Tray" folder and groups the cryptically-named files into
per-creation bundles (households, lots, rooms), extracts embedded
thumbnail images, and does a best-effort match of which installed CC
each creation references.

Two different confidence levels are used deliberately:

1. FILE GROUPING (solid): Tray files are named
   "<prefix hex>!<instance hex>.<ext>", and files that belong to the same
   saved household/lot/room share the same <instance hex> suffix. This
   naming convention is public knowledge documented across the Sims 4
   modding community and is not something EA has tried to hide.

2. CC REFERENCE MATCHING (best-effort, empirically confirmed approach):
   The internal binary layout of .trayitem/.householdbinary files is NOT
   publicly documented at the byte level. Testing against real files
   (see diagnose_encoding.py) showed that a naive fixed-width
   (type, group, instance) match finds nothing, but the resource's
   64-bit instance ID, encoded as a Protocol-Buffers-style varint,
   DOES appear directly in the file bytes - consistent with Sims 4's
   own tooling being Protobuf-based. Matching is therefore done on the
   varint-encoded instance ID alone (not the full type+group+instance
   triple). Instance IDs are large, effectively-random values, so this
   still has a very low false-positive rate, but it's a slightly
   weaker guarantee than a full triple match, and it still can't
   confirm CC that's missing entirely (nothing to match against) -
   that limitation is surfaced in the UI rather than hidden.
"""

import os
import re
from collections import defaultdict

TRAY_EXTENSIONS = {
    ".trayitem", ".householdbinary", ".hhi", ".sgi",
    ".blueprint", ".bpi", ".room",
}

# Files named like: 0x00000001!0x001302d262860048.trayitem
NAME_PATTERN = re.compile(
    r"^(?P<prefix>0x[0-9a-fA-F]{8})!(?P<instance>0x[0-9a-fA-F]{16})\.(?P<ext>\w+)$"
)

JPEG_SOI = b"\xff\xd8\xff"
JPEG_EOI = b"\xff\xd9"
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def varint_encode(n):
    """Protobuf-style base-128 varint encoding. Confirmed empirically
    (via diagnose_encoding.py against real Sims 4 Tray files) to be how
    resource instance IDs are actually stored in .householdbinary files -
    a plain fixed-width 32/32/64-bit layout was tried first and found
    nothing real, which makes sense once you know Sims 4's own tooling
    is Protocol-Buffers-based (varints, not fixed-width ints)."""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _kind_for_extensions(exts):
    exts = set(exts)
    if "blueprint" in exts or "bpi" in exts:
        return "Lot"
    if "room" in exts:
        return "Room"
    if "householdbinary" in exts or "hhi" in exts or "sgi" in exts:
        return "Household / Sim"
    return "Unknown"


def group_tray_folder(tray_folder):
    """
    Returns a list of dicts, one per detected creation:
      {
        "instance": "0x...",
        "kind": "Household / Sim" | "Lot" | "Room" | "Unknown",
        "files": [absolute paths],
        "mtime": latest modification time among its files,
      }
    Files that don't match the naming convention are returned separately
    under key "unmatched".
    """
    groups = defaultdict(list)
    unmatched = []

    for name in os.listdir(tray_folder):
        full = os.path.join(tray_folder, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in TRAY_EXTENSIONS:
            continue
        m = NAME_PATTERN.match(name)
        if m:
            groups[m.group("instance").lower()].append(full)
        else:
            unmatched.append(full)

    creations = []
    for instance, paths in groups.items():
        exts = {os.path.splitext(p)[1].lower().lstrip(".") for p in paths}
        try:
            mtime = max(os.path.getmtime(p) for p in paths)
        except OSError:
            mtime = 0
        creations.append({
            "instance": instance,
            "kind": _kind_for_extensions(exts),
            "files": sorted(paths),
            "mtime": mtime,
        })

    creations.sort(key=lambda c: c["mtime"], reverse=True)
    return creations, unmatched


def filter_creations(creations, query):
    """Filter creations by a free-text query matched against kind,
    instance id, extracted name, and extracted creator (case-insensitive
    substring match). Empty/whitespace query returns everything
    unchanged."""
    q = (query or "").strip().lower()
    if not q:
        return list(creations)
    return [
        c for c in creations
        if q in c["kind"].lower()
        or q in c["instance"].lower()
        or q in (c.get("name") or "").lower()
        or q in (c.get("creator") or "").lower()
    ]


KIND_FILTER_OPTIONS = ["All", "Household / Sim", "Lot", "Room", "Unknown"]

_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e]{3,40}")
_TRAILING_PUNCT = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
_NUMERIC_BLOB = re.compile(r"^[\d.,\s]+$")


def _is_probable_name(s):
    """Filter for whether a printable-byte run plausibly represents a
    short name/username rather than noise or the game's own built-in
    trait/aspiration text (which is much longer prose)."""
    if not (2 <= len(s) <= 24):
        return False
    if _NUMERIC_BLOB.match(s):
        return False
    if not re.search(r"[A-Za-z]", s):
        return False
    return True


def extract_name_and_creator(creation):
    """
    Best-effort extraction of a creation's display name and creator
    username, based on empirical testing (see diagnose_text.py) against
    real Sims 4 tray files: short, plain-UTF-8 strings appear near the
    start of a .trayitem file, before the much longer built-in game
    trait/aspiration text starts - the first such short string is
    typically the household/sim name, and the next one the creator's
    username.

    Returns (name, creator) - either may be None if nothing plausible
    was found (e.g. a creation with no .trayitem file, like a lone
    .sgi entry). This is a heuristic based on one confirmed real
    example, not a documented/guaranteed-correct parser - Sims 4
    hasn't published this file format, so it may not generalize
    perfectly to every creation.
    """
    trayitem_paths = [p for p in creation["files"] if p.lower().endswith(".trayitem")]
    target = trayitem_paths[0] if trayitem_paths else (creation["files"][0] if creation["files"] else None)
    if target is None:
        return None, None

    try:
        with open(target, "rb") as f:
            data = f.read()
    except OSError:
        return None, None

    candidates = []
    for m in _PRINTABLE_RUN.finditer(data):
        s = m.group().decode("ascii").rstrip(_TRAILING_PUNCT)
        if _is_probable_name(s):
            candidates.append(s)
        if len(candidates) >= 2:
            break

    name = candidates[0] if len(candidates) >= 1 else None
    creator = candidates[1] if len(candidates) >= 2 else None
    return name, creator


def filter_by_kind(creations, kind):
    """Filter creations to an exact kind match. 'All' (or falsy) returns
    everything unchanged."""
    if not kind or kind == "All":
        return list(creations)
    return [c for c in creations if c["kind"] == kind]


SORT_OPTIONS = ["Newest", "Oldest", "Kind", "Instance"]


def sort_creations(creations, mode):
    """Sort creations by the given mode (see SORT_OPTIONS). Unrecognized
    modes return the list unchanged (stable no-op) rather than raising,
    since this is driven by a UI dropdown."""
    mode = (mode or "").strip().lower()
    if mode == "newest":
        return sorted(creations, key=lambda c: c["mtime"], reverse=True)
    if mode == "oldest":
        return sorted(creations, key=lambda c: c["mtime"])
    if mode == "kind":
        return sorted(creations, key=lambda c: (c["kind"], c["instance"]))
    if mode == "instance":
        return sorted(creations, key=lambda c: c["instance"])
    return list(creations)


def extract_thumbnail_bytes(paths):
    """
    Best-effort: scan the raw bytes of a creation's files for an embedded
    JPEG or PNG image (tray files commonly carry an embedded thumbnail
    alongside the binary data). Returns raw image bytes, or None.
    This works by looking for standard image file magic/end markers -
    it does not rely on knowing the surrounding tray file structure.
    """
    for path in paths:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            continue

        i = data.find(JPEG_SOI)
        if i != -1:
            j = data.find(JPEG_EOI, i)
            if j != -1:
                return data[i:j + 2]

        i = data.find(PNG_SIG)
        if i != -1:
            # PNG ends with an IEND chunk: length(4) + 'IEND' + crc(4)
            iend = data.find(b"IEND", i)
            if iend != -1:
                return data[i:iend + 8]

    return None


def find_referenced_cc(paths, mods_index, min_varint_len=5):
    """
    For a creation's tray files, check - for every resource actually
    installed in your Mods folder - whether that resource's instance ID,
    encoded as a protobuf varint, appears anywhere in the raw bytes of
    the creation's files. Returns a dict:
        {(type, group, instance): [package paths]}
    for every match found.

    This checks the instance ID alone rather than the full
    (type, group, instance) triple, because empirical testing against
    real Tray files (see diagnose_encoding.py) showed the type/group
    fields are not necessarily stored contiguously with the instance in
    the underlying message layout, while the bare varint-encoded
    instance DOES show up directly. Instance IDs are large,
    effectively-random 64-bit values, so matching on instance alone
    still has a very low false-positive rate in practice (a
    min_varint_len of 5 bytes requires roughly a 1-in-4-billion chance
    coincidence per check) - but it is a slightly weaker guarantee than
    a full triple match would be, which is why results are described as
    "likely present" rather than certain.
    """
    if not mods_index:
        return {}

    blobs = []
    for path in paths:
        try:
            with open(path, "rb") as f:
                blobs.append(f.read())
        except OSError:
            continue

    found = {}
    for key, owner_paths in mods_index.items():
        _type_id, _group_id, instance = key
        needle = varint_encode(instance)
        if len(needle) < min_varint_len:
            continue  # too short on its own to be a trustworthy signal
        for data in blobs:
            if needle in data:
                found[key] = owner_paths
                break

    return found
