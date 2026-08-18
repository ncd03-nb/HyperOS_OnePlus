#!/usr/bin/env python3
# Minimal, dependency-free extractor for Android A/B payload.bin (update_engine).
#
# Handles FULL payloads only (the kind inside stock / fastboot / OTA zips):
# operation types REPLACE, REPLACE_BZ, REPLACE_XZ and ZERO. Delta operations
# (BSDIFF / PUFFDIFF / SOURCE_*) are not needed for a full image and are
# rejected loudly instead of producing a corrupt file.
#
# The payload layout (chromeos_update_engine) is:
#   "CrAU" | version(u64 BE) | manifest_size(u64 BE) |
#   metadata_signature_size(u32 BE, v2+) | manifest | metadata_signature | data
# The manifest is a DeltaArchiveManifest protobuf. We decode just the fields we
# need with a tiny hand-rolled wire-format reader, so there is no protobuf
# dependency to install.

import bz2
import lzma
import struct
import sys

BRILLO_MAGIC = b"CrAU"


# ---------------------------------------------------------------------------
# tiny protobuf wire reader
# ---------------------------------------------------------------------------
def _read_varint(buf, pos):
    shift = 0
    result = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _iter_fields(buf):
    """Yield (field_number, wire_type, value) for every top-level field.
    value is: int (varint), bytes (length-delimited), or raw int (32/64 bit)."""
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = _read_varint(buf, pos)
        field = key >> 3
        wire = key & 0x7
        if wire == 0:  # varint
            val, pos = _read_varint(buf, pos)
            yield field, wire, val
        elif wire == 2:  # length-delimited
            length, pos = _read_varint(buf, pos)
            yield field, wire, buf[pos:pos + length]
            pos += length
        elif wire == 1:  # 64-bit
            yield field, wire, struct.unpack_from("<Q", buf, pos)[0]
            pos += 8
        elif wire == 5:  # 32-bit
            yield field, wire, struct.unpack_from("<I", buf, pos)[0]
            pos += 4
        else:
            raise ValueError("unsupported wire type %d (field %d)" % (wire, field))


# ---------------------------------------------------------------------------
# message decoders (only the fields we care about)
# ---------------------------------------------------------------------------
def _decode_extent(buf):
    start_block = num_blocks = 0
    for field, _w, val in _iter_fields(buf):
        if field == 1:
            start_block = val
        elif field == 2:
            num_blocks = val
    return start_block, num_blocks


def _decode_operation(buf):
    op = {"type": 0, "data_offset": 0, "data_length": 0, "dst_extents": []}
    for field, _w, val in _iter_fields(buf):
        if field == 1:
            op["type"] = val
        elif field == 2:
            op["data_offset"] = val
        elif field == 3:
            op["data_length"] = val
        elif field == 6:
            op["dst_extents"].append(_decode_extent(val))
    return op


def _decode_partition(buf):
    part = {"name": None, "operations": []}
    for field, _w, val in _iter_fields(buf):
        if field == 1:
            part["name"] = val.decode("utf-8")
        elif field == 8:
            part["operations"].append(_decode_operation(val))
    return part


def _decode_manifest(buf):
    block_size = 4096
    partitions = []
    for field, _w, val in _iter_fields(buf):
        if field == 3:
            block_size = val
        elif field == 13:
            partitions.append(_decode_partition(val))
    return block_size, partitions


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
# InstallOperation.Type
REPLACE = 0
REPLACE_BZ = 1
ZERO = 6
REPLACE_XZ = 8


def list_partitions(payload_path):
    _base, block_size, partitions = _parse_header(payload_path)
    return [p["name"] for p in partitions]


def _parse_header(payload_path):
    with open(payload_path, "rb") as f:
        magic = f.read(4)
        if magic != BRILLO_MAGIC:
            raise ValueError("not a payload.bin (bad magic %r)" % magic)
        version = struct.unpack(">Q", f.read(8))[0]
        manifest_size = struct.unpack(">Q", f.read(8))[0]
        metadata_sig_size = 0
        if version >= 2:
            metadata_sig_size = struct.unpack(">I", f.read(4))[0]
        manifest = f.read(manifest_size)
        header_len = f.tell() + metadata_sig_size  # data blobs start here
    block_size, partitions = _decode_manifest(manifest)
    return header_len, block_size, partitions


def extract(payload_path, out_dir, wanted=None, log=print):
    """Extract selected partitions from payload.bin to out_dir/<name>.img.
    wanted: iterable of partition names, or None for all. Returns list of names
    actually written."""
    import os
    base, block_size, partitions = _parse_header(payload_path)
    wanted_set = set(wanted) if wanted is not None else None
    written = []
    os.makedirs(out_dir, exist_ok=True)
    with open(payload_path, "rb") as f:
        for part in partitions:
            name = part["name"]
            if wanted_set is not None and name not in wanted_set:
                continue
            out_path = os.path.join(out_dir, name + ".img")
            log("  extracting %s -> %s" % (name, out_path))
            with open(out_path, "wb") as out:
                for op in part["operations"]:
                    _apply_op(f, out, op, base, block_size, name)
            written.append(name)
    if wanted_set is not None:
        missing = wanted_set - set(written)
        if missing:
            raise ValueError("partitions not in payload: %s" % ", ".join(sorted(missing)))
    return written


def _apply_op(f, out, op, base, block_size, name):
    t = op["type"]
    dst = op["dst_extents"]
    if not dst:
        return
    seek_to = dst[0][0] * block_size
    out.seek(seek_to)
    if t == ZERO:
        for start_block, num_blocks in dst:
            out.seek(start_block * block_size)
            out.write(b"\x00" * (num_blocks * block_size))
        return
    f.seek(base + op["data_offset"])
    raw = f.read(op["data_length"])
    if t == REPLACE:
        data = raw
    elif t == REPLACE_BZ:
        data = bz2.decompress(raw)
    elif t == REPLACE_XZ:
        data = lzma.decompress(raw)
    else:
        raise ValueError(
            "partition '%s' uses delta op type %d; only FULL payloads "
            "(REPLACE/REPLACE_BZ/REPLACE_XZ/ZERO) are supported" % (name, t))
    # a single non-zero op normally targets one contiguous extent
    written = 0
    for start_block, num_blocks in dst:
        out.seek(start_block * block_size)
        chunk = data[written:written + num_blocks * block_size]
        out.write(chunk)
        written += len(chunk)


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="extract Android A/B payload.bin")
    ap.add_argument("payload")
    ap.add_argument("-o", "--out", default=".", help="output directory")
    ap.add_argument("-p", "--partitions", default="",
                    help="comma-separated partition names (default: all)")
    ap.add_argument("-l", "--list", action="store_true",
                    help="list partitions and exit")
    args = ap.parse_args(argv)
    if args.list:
        for p in list_partitions(args.payload):
            print(p)
        return 0
    wanted = [x for x in args.partitions.split(",") if x] or None
    extract(args.payload, args.out, wanted)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
