"""
dbpf.py
Reader for The Sims 4 .package files (DBPF 2.1 format).

The DBPF format and header layout used here are taken from publicly
published, independent documentation of the file format (community wiki
documentation of the header bytes and index bit-field layout), not from
any specific application's source code. This module only reads the
resource *index* (the table of contents) - it does not need to fully
decompress every resource to tell whether a package is structurally
sound.

Header layout (96 bytes), all little-endian:
  0x00  4s   magic ("DBPF")
  0x04  I    major version (2 for Sims 4)
  0x08  I    minor version (1 for Sims 4)
  0x0C  24s  unknown/reserved
  0x24  I    index entry count
  0x28  4s   unknown
  0x2C  I    index size in bytes
  0x30  12s  unknown
  0x3C  I    index version (always 3 for Sims 4)
  0x40  I    absolute offset of the index table
  0x44  28s  unknown/reserved

At the index offset:
  I    index_type -- a bitfield (bits 0-7). Each set bit means that
       field's value is constant for every entry and stored ONCE, right
       here, as a header value. Each unset bit means that field is
       stored individually per-entry.

  Field bit meanings (in order):
    0 ResourceType
    1 ResourceGroup
    2 InstanceHi
    3 InstanceLo
    4 ChunkOffset
    5 FileSize (low 31 bits; high bit is a flag)
    6 MemSize (decompressed size)
    7 Compressed (low word) / unknown (high word)
"""

import struct
import os
from dataclasses import dataclass


DBPF_MAGIC = b"DBPF"
HEADER_SIZE = 96

# Compression flag values seen in Sims 4 packages
COMPRESSION_NONE = 0x0000
COMPRESSION_ZLIB = 0x5A42       # 'ZB' - standard zlib/deflate
COMPRESSION_STREAMABLE = 0xFFFE
COMPRESSION_INTERNAL = 0xFFFF


class CorruptPackageError(Exception):
    """Raised when a .package file doesn't parse as a valid DBPF file."""
    pass


@dataclass
class ResourceEntry:
    type_id: int
    group_id: int
    instance: int   # combined 64-bit instance (hi << 32 | lo)
    offset: int
    filesize: int
    memsize: int
    compressed: int

    @property
    def key(self):
        """The (type, group, instance) triple that uniquely identifies
        this resource - this is what tray files reference."""
        return (self.type_id, self.group_id, self.instance)


def _read_header(f, path):
    header = f.read(HEADER_SIZE)
    if len(header) < HEADER_SIZE:
        raise CorruptPackageError(f"{path}: file is smaller than a DBPF header")
    if header[0:4] != DBPF_MAGIC:
        raise CorruptPackageError(f"{path}: missing DBPF magic bytes (not a package file, or file is truncated/corrupt)")

    major, minor = struct.unpack_from("<II", header, 4)
    entry_count = struct.unpack_from("<I", header, 0x24)[0]
    index_size = struct.unpack_from("<I", header, 0x2C)[0]
    index_version = struct.unpack_from("<I", header, 0x3C)[0]
    index_offset = struct.unpack_from("<I", header, 0x40)[0]

    return {
        "major": major,
        "minor": minor,
        "entry_count": entry_count,
        "index_size": index_size,
        "index_version": index_version,
        "index_offset": index_offset,
    }


def read_index(path):
    """
    Read a .package file's resource index.
    Returns a list of ResourceEntry.
    Raises CorruptPackageError if the file is not a well-formed DBPF file.
    """
    try:
        size_on_disk = os.path.getsize(path)
    except OSError as e:
        raise CorruptPackageError(f"{path}: cannot stat file ({e})")

    if size_on_disk == 0:
        raise CorruptPackageError(f"{path}: file is empty (0 bytes)")

    with open(path, "rb") as f:
        h = _read_header(f, path)

        if h["entry_count"] == 0 or h["index_offset"] == 0:
            return []  # technically valid, just no resources

        if h["index_offset"] + h["index_size"] > size_on_disk:
            raise CorruptPackageError(
                f"{path}: index table claims to extend past end of file "
                f"(file is truncated or the header is damaged)"
            )

        f.seek(h["index_offset"])
        type_bytes = f.read(4)
        if len(type_bytes) < 4:
            raise CorruptPackageError(f"{path}: truncated index table")
        index_type = struct.unpack("<I", type_bytes)[0]

        header_fields = {}
        for bit in range(8):
            if index_type & (1 << bit):
                raw = f.read(4)
                if len(raw) < 4:
                    raise CorruptPackageError(f"{path}: truncated index header")
                header_fields[bit] = struct.unpack("<I", raw)[0]

        entries = []
        for i in range(h["entry_count"]):
            fields = {}
            for bit in range(8):
                if bit in header_fields:
                    fields[bit] = header_fields[bit]
                else:
                    raw = f.read(4)
                    if len(raw) < 4:
                        raise CorruptPackageError(
                            f"{path}: index table truncated at entry {i} of {h['entry_count']} "
                            f"(package is likely corrupt/incomplete)"
                        )
                    fields[bit] = struct.unpack("<I", raw)[0]

            instance = (fields[2] << 32) | fields[3]
            filesize = fields[5] & 0x7FFFFFFF
            compressed = fields[7] & 0xFFFF

            entries.append(ResourceEntry(
                type_id=fields[0],
                group_id=fields[1],
                instance=instance,
                offset=fields[4],
                filesize=filesize,
                memsize=fields[6],
                compressed=compressed,
            ))

        return entries


def scan_mods_folder(mods_folder, progress_callback=None):
    """
    Walk a Mods folder, parse every .package file found.

    Returns (good, broken, index) where:
      good   = list of (path, [ResourceEntry, ...])
      broken = list of (path, error_message)
      index  = dict mapping (type, group, instance) -> [paths] for every
               resource found in every readable package (a resource key
               appearing under 2+ paths is a conflict/duplicate).
    """
    good = []
    broken = []
    index = {}

    package_paths = []
    for root, _dirs, files in os.walk(mods_folder):
        for name in files:
            if name.lower().endswith(".package"):
                package_paths.append(os.path.join(root, name))

    total = len(package_paths)
    for i, path in enumerate(package_paths):
        if progress_callback:
            progress_callback(i + 1, total, path)
        try:
            entries = read_index(path)
            good.append((path, entries))
            for e in entries:
                index.setdefault(e.key, []).append(path)
        except CorruptPackageError as e:
            broken.append((path, str(e)))
        except Exception as e:  # noqa: BLE001 - be defensive, never crash the scan
            broken.append((path, f"unexpected error reading file: {e}"))

    return good, broken, index


def find_conflicts(index):
    """Return {key: [paths]} for every resource key defined by more than
    one package - these are a very common real-world cause of 'broken'
    CC/mod behaviour (whichever package loads last silently wins)."""
    return {key: paths for key, paths in index.items() if len(paths) > 1}
