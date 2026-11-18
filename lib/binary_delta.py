"""Bounded BSDIFF40 decoding for verified ROM compatibility deltas."""
import bz2


def _signed(data):
    if len(data) != 8:
        raise ValueError('Truncated BSDIFF integer')
    value = int.from_bytes(data, 'little')
    return -(value & ((1 << 63) - 1)) if value >> 63 else value


def _decompress(data, limit):
    stream = bz2.BZ2Decompressor()
    result = stream.decompress(data, max_length=limit + 1)
    if len(result) > limit or not stream.eof or stream.unused_data:
        raise ValueError('Invalid or oversized BSDIFF stream')
    return result


def apply_delta(old, patch, expected_size):
    """Decode bounded BSDIFF40, preserving the original beyond explicit deltas."""
    if len(patch) < 32 or patch[:8] != b'BSDIFF40':
        raise ValueError('Invalid BSDIFF header')
    control_size, diff_size, size = [_signed(patch[i:i + 8]) for i in (8, 16, 24)]
    if size != expected_size or min(control_size, diff_size, size) < 0:
        raise ValueError('Invalid BSDIFF sizes')
    a, b = 32 + control_size, 32 + control_size + diff_size
    if b > len(patch):
        raise ValueError('Truncated BSDIFF blocks')
    control = _decompress(patch[32:a], (size + 1) * 24)
    diff = _decompress(patch[a:b], size)
    extra = _decompress(patch[b:], size)
    result = bytearray()
    pos = dp = ep = cp = 0
    while len(result) < size:
        if cp + 24 > len(control):
            raise ValueError('Truncated BSDIFF control')
        x, y, seek = [_signed(control[cp + i:cp + i + 8]) for i in (0, 8, 16)]
        cp += 24
        # BSDIFF may emit a seek-only control tuple. cp always advances, so
        # these remain bounded by the decompressed control stream.
        if min(x, y) < 0 or len(result) + x + y > size:
            raise ValueError('Invalid BSDIFF control range')
        if dp + x > len(diff) or ep + y > len(extra):
            raise ValueError('Truncated BSDIFF data')
        block = bytearray(diff[dp:dp + x])
        for i in range(max(0, -pos), min(x, len(old) - pos)):
            block[i] = (block[i] + old[pos + i]) & 255
        result.extend(block)
        result.extend(extra[ep:ep + y])
        dp += x
        ep += y
        pos += x + seek
    if cp != len(control) or dp != len(diff) or ep != len(extra):
        raise ValueError('Unused BSDIFF data')
    return bytes(result)
