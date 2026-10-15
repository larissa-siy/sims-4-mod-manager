"""
diagnose_encoding.py

Standalone diagnostic. Everything it does happens on your machine only -
it does not send anything anywhere. It prints counts and package
filenames to your Terminal, which you can paste back so the real
encoding can be built into trayscan.py properly.

What it does:
  1. Scans your Mods folder (same as the main app) to get every
     installed CC resource's (type, group, instance) key.
  2. Picks the Tray creation with the MOST files (as a reasonable guess
     at "probably has CC on it" - pass --instance to target a specific
     one instead).
  3. Tries several different plausible byte encodings of those keys
     (different field orders, endianness, and a basic varint scan) and
     reports how many hits each encoding gets against the real tray
     file bytes.

Usage:
    python3 diagnose_encoding.py "/path/to/Tray" "/path/to/Mods"
    python3 diagnose_encoding.py "/path/to/Tray" "/path/to/Mods" --instance 0x00a0170f49b80050
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbpf
import trayscan


def encode_le_type_group_inst(t, g, inst):
    return struct.pack("<IIQ", t, g, inst)


def encode_be_type_group_inst(t, g, inst):
    return struct.pack(">IIQ", t, g, inst)


def encode_le_inst_type_group(t, g, inst):
    return struct.pack("<QII", inst, t, g)


def encode_le_type_group_instlo_insthi(t, g, inst):
    lo = inst & 0xFFFFFFFF
    hi = (inst >> 32) & 0xFFFFFFFF
    return struct.pack("<IIII", t, g, lo, hi)


def encode_le_type_group_insthi_instlo(t, g, inst):
    lo = inst & 0xFFFFFFFF
    hi = (inst >> 32) & 0xFFFFFFFF
    return struct.pack("<IIII", t, g, hi, lo)


def encode_le_group_type_inst(t, g, inst):
    return struct.pack("<IIQ", g, t, inst)


CANDIDATES = {
    "LE type,group,instance(8)": encode_le_type_group_inst,
    "BE type,group,instance(8)": encode_be_type_group_inst,
    "LE instance(8),type,group": encode_le_inst_type_group,
    "LE type,group,inst_lo,inst_hi": encode_le_type_group_instlo_insthi,
    "LE type,group,inst_hi,inst_lo": encode_le_type_group_insthi_instlo,
    "LE group,type,instance(8)": encode_le_group_type_inst,
}


def varint_encode(n):
    """Protobuf-style base-128 varint encoding, in case fields are stored
    this way instead of fixed-width."""
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


def try_encoding(name, encode_fn, mods_index, haystacks):
    hits = {}
    for key in mods_index:
        t, g, inst = key
        needle = encode_fn(t, g, inst)
        for path, data in haystacks:
            if needle in data:
                hits.setdefault(key, []).append(path)
    return hits


def try_varint(mods_index, haystacks):
    """Looser check: encode just the instance as a varint and see if it
    shows up at all, ignoring type/group ordering questions."""
    hits = {}
    for key in mods_index:
        t, g, inst = key
        needle = varint_encode(inst)
        if len(needle) < 3:
            continue  # too short to be a meaningful signal on its own
        for path, data in haystacks:
            if needle in data:
                # store which of THIS creation's tray files it was found
                # in, separately from which package(s) actually own the
                # resource (mods_index[key]) - printed separately below.
                hits.setdefault(key, []).append(path)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tray_folder")
    ap.add_argument("mods_folder")
    ap.add_argument("--instance", help="target a specific creation's instance id, e.g. 0x00a0170f49b80050")
    args = ap.parse_args()

    print(f"Scanning Mods folder: {args.mods_folder}")
    good, broken, index = dbpf.scan_mods_folder(args.mods_folder)
    print(f"  {len(good)} packages read OK, {len(broken)} broken, {len(index)} unique resource keys\n")

    print(f"Scanning Tray folder: {args.tray_folder}")
    creations, _unmatched = trayscan.group_tray_folder(args.tray_folder)
    print(f"  {len(creations)} creations found\n")

    if not creations:
        print("No creations found - nothing to test against.")
        return

    if args.instance:
        target = args.instance.lower()
        matches = [c for c in creations if c["instance"] == target]
        if not matches:
            print(f"No creation with instance {target} found. Available instances:")
            for c in creations:
                print(f"  {c['instance']}  ({c['kind']}, {len(c['files'])} files)")
            return
        creation = matches[0]
    else:
        creation = max(creations, key=lambda c: len(c["files"]))
        print(f"No --instance given, defaulting to the creation with the most files: "
              f"{creation['instance']} ({creation['kind']}, {len(creation['files'])} files)\n")

    haystacks = []
    for path in creation["files"]:
        try:
            with open(path, "rb") as f:
                haystacks.append((path, f.read()))
        except OSError as e:
            print(f"  couldn't read {path}: {e}")

    total_bytes = sum(len(d) for _p, d in haystacks)
    print(f"Testing against {len(haystacks)} file(s), {total_bytes} bytes total.\n")

    print("=" * 70)
    print("Trying fixed-width encodings...")
    print("=" * 70)
    any_fixed_hit = False
    for name, fn in CANDIDATES.items():
        hits = try_encoding(name, fn, index, haystacks)
        if hits:
            any_fixed_hit = True
            print(f"\n[MATCH] Encoding '{name}' found {len(hits)} matching resource(s):")
            for key, paths in list(hits.items())[:10]:
                pkg_names = ", ".join(os.path.basename(p) for p in set(paths))
                print(f"    key={key}  ->  {pkg_names}")
            if len(hits) > 10:
                print(f"    ...and {len(hits) - 10} more")
        else:
            print(f"[no hits]  {name}")

    print()
    print("=" * 70)
    print("Trying a loose varint scan on just the instance ID...")
    print("=" * 70)
    varint_hits = try_varint(index, haystacks)
    if varint_hits:
        print(f"[MATCH] varint-encoded instance found {len(varint_hits)} matching resource(s):")
        for key, found_in_files in list(varint_hits.items())[:10]:
            owning_packages = ", ".join(os.path.basename(p) for p in set(index[key]))
            found_in = ", ".join(os.path.basename(p) for p in set(found_in_files))
            print(f"    instance={key[2]}  installed in: {owning_packages}   (found in tray file: {found_in})")
        if len(varint_hits) > 10:
            print(f"    ...and {len(varint_hits) - 10} more")
    else:
        print("[no hits]  varint instance scan")

    print()
    if not any_fixed_hit and not varint_hits:
        print("None of the tested encodings found anything. Possible reasons:")
        print("  - This particular creation may genuinely have no CC on it")
        print("    (try again with --instance pointed at a sim/lot you KNOW uses CC)")
        print("  - The real encoding is something not tried here (e.g. compressed data,")
        print("    a different varint scheme, or references stored indirectly via a")
        print("    string/hash table rather than raw resource keys)")
        print("  - Paste this whole output back and we can dig further.")


if __name__ == "__main__":
    main()
