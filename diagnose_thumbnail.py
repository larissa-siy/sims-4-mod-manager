"""
diagnose_thumbnail.py

Standalone diagnostic - everything happens on your machine only, nothing
is sent anywhere. Scans a tray creation's files for embedded image data
using several different hypotheses about how it might be stored, and
reports what it finds so the real extraction logic can be built
correctly into trayscan.py.

Usage:
    python3 diagnose_thumbnail.py "/path/to/Tray"
    python3 diagnose_thumbnail.py "/path/to/Tray" --instance 0x00a0170f49b80050
"""

import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trayscan

JPEG_SOI = b"\xff\xd8\xff"
JPEG_EOI = b"\xff\xd9"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
DDS_SIG = b"DDS "
BMP_SIG = b"BM"
RIFF_SIG = b"RIFF"
WEBP_SIG = b"WEBP"

# zlib streams almost always start with one of these two bytes followed
# by a second byte from a small known set (compression method/level).
ZLIB_MAGIC_FIRST = 0x78
ZLIB_MAGIC_SECOND = {0x01, 0x5E, 0x9C, 0xDA}

# EA/Maxis's own "QFS"/RefPack compression (used across Sims/SimCity DBPF
# resources) uses a 9-byte header: 4-byte compressed size, a 2-byte
# 0x10FB compression-ID marker, then a 3-byte uncompressed size - all
# big-endian. This is a DIFFERENT algorithm than zlib, so a plain
# zlib.decompress() would never find it even if present.
QFS_MAGIC = b"\x10\xfb"


def find_qfs_candidates(data):
    """Look for the QFS/RefPack header marker and report the declared
    compressed/uncompressed sizes for each candidate, without attempting
    a full decode yet - cheap way to check whether this compression
    scheme is even present before writing a full decoder for it."""
    hits = []
    start = 0
    while True:
        i = data.find(QFS_MAGIC, start)
        if i == -1:
            break
        header_start = i - 4
        if header_start >= 0 and header_start + 9 <= len(data):
            comp_size = int.from_bytes(data[header_start:header_start + 4], "big")
            uncomp_size = int.from_bytes(data[header_start + 6:header_start + 9], "big")
            plausible = 0 < uncomp_size < 5_000_000 and 0 < comp_size <= len(data) - header_start + 4
            hits.append((header_start, comp_size, uncomp_size, plausible))
        start = i + 2
    return hits


def find_raw_jpeg(data):
    hits = []
    start = 0
    while True:
        i = data.find(JPEG_SOI, start)
        if i == -1:
            break
        j = data.find(JPEG_EOI, i)
        if j != -1:
            hits.append((i, j + 2 - i))
            start = j + 2
        else:
            start = i + 1
    return hits


def find_raw_png(data):
    hits = []
    start = 0
    while True:
        i = data.find(PNG_SIG, start)
        if i == -1:
            break
        j = data.find(b"IEND", i)
        if j != -1:
            hits.append((i, j + 8 - i))
            start = j + 8
        else:
            start = i + 1
    return hits


def find_raw_dds(data):
    hits = []
    start = 0
    while True:
        i = data.find(DDS_SIG, start)
        if i == -1:
            break
        hits.append((i, None))  # DDS has no simple end marker
        start = i + 4
    return hits


def find_raw_bmp(data):
    hits = []
    start = 0
    while True:
        i = data.find(BMP_SIG, start)
        if i == -1:
            break
        if i + 6 <= len(data):
            size = struct.unpack_from("<I", data, i + 2)[0]
            if 100 < size < len(data):  # plausible file size, not a random "BM" byte pair
                hits.append((i, size))
        start = i + 2
    return hits


def find_raw_webp(data):
    """WebP files are a RIFF container: 'RIFF' + 4-byte size + 'WEBP'."""
    hits = []
    start = 0
    while True:
        i = data.find(RIFF_SIG, start)
        if i == -1:
            break
        if data[i + 8:i + 12] == WEBP_SIG:
            size = struct.unpack_from("<I", data, i + 4)[0]
            hits.append((i, size + 8))
        start = i + 4
    return hits


def find_zlib_streams(data, min_output_len=500):
    """Try inflating at every position that looks like a zlib header,
    and report ones that decompress to something that looks like an
    image (starts with a JPEG/PNG/DDS/BMP signature)."""
    hits = []
    n = len(data)
    for off in range(n - 1):
        if data[off] != ZLIB_MAGIC_FIRST or data[off + 1] not in ZLIB_MAGIC_SECOND:
            continue
        try:
            result = zlib.decompress(data[off:])
        except zlib.error:
            continue
        if len(result) < min_output_len:
            continue
        if (result[:3] == JPEG_SOI or result[:8] == PNG_SIG or result[:4] == DDS_SIG
                or (result[:4] == RIFF_SIG and result[8:12] == WEBP_SIG)):
            hits.append((off, len(result), result[:12]))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tray_folder")
    ap.add_argument("--instance", help="target a specific creation's instance id")
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

        jpg = find_raw_jpeg(data)
        png = find_raw_png(data)
        dds = find_raw_dds(data)
        bmp = find_raw_bmp(data)
        webp = find_raw_webp(data)

        print(f"  raw JPEG matches: {len(jpg)}  {jpg[:3]}")
        print(f"  raw PNG matches:  {len(png)}  {png[:3]}")
        print(f"  raw DDS matches:  {len(dds)}  {dds[:3]}")
        print(f"  raw BMP matches:  {len(bmp)}  {bmp[:3]}")
        print(f"  raw WebP matches: {len(webp)}  {webp[:3]}")

        print("  scanning for zlib-compressed image data (this can take a few seconds)...")
        zh = find_zlib_streams(data)
        if zh:
            for off, out_len, header in zh:
                kind = ("JPEG" if header[:3] == JPEG_SOI else
                        "PNG" if header[:8] == PNG_SIG else
                        "DDS" if header[:4] == DDS_SIG else
                        "WebP" if header[:4] == RIFF_SIG else "?")
                print(f"  [MATCH] zlib stream at offset {off} -> {out_len} bytes of {kind} data")
        else:
            print("  no zlib-compressed image data found")

        qfs = find_qfs_candidates(data)
        if qfs:
            print("  QFS/RefPack (0x10FB) header candidates found:")
            for header_start, comp_size, uncomp_size, plausible in qfs:
                flag = "PLAUSIBLE" if plausible else "unlikely"
                print(f"    at offset {header_start}: declared compressed={comp_size}, "
                      f"uncompressed={uncomp_size}  [{flag}]")
        else:
            print("  no QFS/RefPack header marker (0x10FB) found")
        print()

    print("If none of the file(s) above show a match under any method, paste this")
    print("whole output back - the image may be stored in a file type not currently")
    print("grouped with this creation (e.g. a separate thumbnail file), or in a")
    print("format/wrapper this diagnostic doesn't check yet.")


if __name__ == "__main__":
    main()
