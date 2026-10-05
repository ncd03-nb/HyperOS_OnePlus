"""Narrow SDK34 fixes for the Ace 3V's Xiaomi framework clients."""
import hashlib
from pathlib import Path
import struct
import zipfile
import zlib


def _uleb(data, offset):
    value = 0
    for shift in range(0, 35, 7):
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError('Invalid DEX ULEB128')


def _dex_tables(data):
    if len(data) < 112:
        raise ValueError('Truncated DEX header')
    if data[:8] not in (b'dex\n035\0', b'dex\n037\0', b'dex\n038\0', b'dex\n039\0'):
        raise ValueError('Unsupported DEX format')
    if struct.unpack_from('<I', data, 32)[0] != len(data):
        raise ValueError('Invalid DEX size')
    if hashlib.sha1(data[32:]).digest() != data[12:32]:
        raise ValueError('Invalid DEX signature')
    if zlib.adler32(data[12:]) & 0xffffffff != struct.unpack_from('<I', data, 8)[0]:
        raise ValueError('Invalid DEX checksum')
    sc, so, tc, to, pc, po = struct.unpack_from('<6I', data, 56)
    strings = []
    for i in range(sc):
        offset = struct.unpack_from('<I', data, so + i * 4)[0]
        _, offset = _uleb(data, offset)
        # Descriptors/method names are ASCII; unrelated modified-UTF8 strings
        # need not be decoded to locate the method.
        strings.append(data[offset:data.index(0, offset)])
    types = [strings[struct.unpack_from('<I', data, to + i * 4)[0]] for i in range(tc)]
    protos = []
    for i in range(pc):
        _, ret, params = struct.unpack_from('<3I', data, po + i * 12)
        count = struct.unpack_from('<I', data, params)[0] if params else 0
        args = b''.join(types[struct.unpack_from('<H', data, params + 4 + j * 2)[0]]
                        for j in range(count))
        protos.append(b'(' + args + b')' + types[ret])
    mc, mo = struct.unpack_from('<2I', data, 88)
    methods = []
    for i in range(mc):
        cls, proto, name = struct.unpack_from('<HHI', data, mo + i * 8)
        methods.append((types[cls], strings[name], protos[proto]))
    return types, methods


def _method_code(data, types, methods, wanted):
    count, offset = struct.unpack_from('<2I', data, 96)
    for i in range(count):
        cls, _, _, _, _, _, class_data, _ = struct.unpack_from('<8I', data, offset + i * 32)
        if types[cls] != wanted[0] or not class_data:
            continue
        counts = []
        for _ in range(4):
            value, class_data = _uleb(data, class_data)
            counts.append(value)
        for _ in range(counts[0] + counts[1]):
            _, class_data = _uleb(data, class_data)
            _, class_data = _uleb(data, class_data)
        for total in counts[2:]:
            index = 0
            for _ in range(total):
                delta, class_data = _uleb(data, class_data)
                index += delta
                _, class_data = _uleb(data, class_data)
                code, class_data = _uleb(data, class_data)
                if methods[index] == wanted:
                    return code
    return None


def patch_vonr_dex(data):
    """Replace only the synchronous Xiaomi VoNR RPC with false.

    Preserve all offsets, code-item headers, branches, exception handlers,
    debug info and unrelated instructions. The wrapper's null/error paths
    already return false. This affects Xiaomi's UI query, not vendor IMS.
    """
    types, methods = _dex_tables(data)
    owner = (b'Lmiui/telephony/TelephonyManagerEx;', b'isVoNREnabled', b'(I)Z')
    rpc = (b'Lmiui/telephony/IMiuiTelephony;', b'isVoNREnabled', b'(I)Z')
    code = _method_code(data, types, methods, owner)
    if code is None:
        return None
    if not code or rpc not in methods:
        raise ValueError('Missing Xiaomi VoNR wrapper/RPC')
    target_index = methods.index(rpc)
    size = struct.unpack_from('<I', data, code + 12)[0]
    start, end = code + 16, code + 16 + size * 2
    replacement = bytes.fromhex('1200000000000000')  # const/4 v0,0; nop x3
    matches = []
    for offset in range(start, end - 7, 2):
        if (data[offset:offset + 2] == b'\x72\x20'
                and struct.unpack_from('<H', data, offset + 2)[0] == target_index
                and data[offset + 6:offset + 8] == b'\x0a\x00'):
            matches.append(offset)
    if not matches:
        if data[start:end].count(replacement) == 1:
            return data, {'already_patched': True, 'method': 'TelephonyManagerEx.isVoNREnabled(I)Z'}
        raise ValueError('Unknown Xiaomi VoNR wrapper instruction layout')
    if len(matches) != 1:
        raise ValueError('Ambiguous Xiaomi VoNR wrapper')
    offset = matches[0]
    patched = bytearray(data)
    patched[offset:offset + 8] = replacement
    patched[12:32] = hashlib.sha1(patched[32:]).digest()
    struct.pack_into('<I', patched, 8, zlib.adler32(patched[12:]) & 0xffffffff)
    assert patched[32:offset] == data[32:offset] and patched[offset + 8:] == data[offset + 8:]
    _dex_tables(patched)
    return bytes(patched), {'already_patched': False, 'code_offset': offset,
                           'before': data[offset:offset + 8].hex(), 'after': replacement.hex(),
                           'method': 'TelephonyManagerEx.isVoNREnabled(I)Z'}


def disable_xiaomi_vonr_query(jar):
    jar = Path(jar)
    if not jar.is_file():
        return {'installed': False, 'reason': 'No Xiaomi framework jar'}
    before = hashlib.sha256(jar.read_bytes()).hexdigest()
    with zipfile.ZipFile(jar) as archive:
        comment = archive.comment
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    if any(info.filename.startswith('META-INF/') and
           info.filename.upper().endswith(('.SF', '.RSA', '.DSA', '.EC'))
           for info, _ in entries):
        raise ValueError('Refusing to patch a signed framework jar')
    found = []
    for i, (info, data) in enumerate(entries):
        if info.filename.startswith('classes') and info.filename.endswith('.dex'):
            result = patch_vonr_dex(data)
            if result is not None:
                patched, detail = result
                found.append((i, patched, detail))
    if len(found) != 1:
        raise ValueError('Expected exactly one Xiaomi VoNR wrapper in framework jar')
    index, patched, detail = found[0]
    if not detail['already_patched']:
        temporary = jar.with_name(jar.name + '.vonr.partial')
        if temporary.exists():
            raise ValueError('Unfinished VoNR jar patch exists')
        with zipfile.ZipFile(temporary, 'w') as archive:
            archive.comment = comment
            for i, (info, data) in enumerate(entries):
                archive.writestr(info, patched if i == index else data)
        with zipfile.ZipFile(temporary) as archive:
            for i, (info, data) in enumerate(entries):
                assert archive.read(info.filename) == (patched if i == index else data)
        temporary.replace(jar)
    return {'installed': True, 'before_sha256': before,
            'sha256': hashlib.sha256(jar.read_bytes()).hexdigest(),
            'dex': entries[index][0].filename, **detail}
