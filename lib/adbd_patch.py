"""Derive no-auth ARM64 adbd from the donor, without altering its signed APEX.

Only the auth_required byte and instructions storing that byte may change.
The executable's privilege dropping, dependencies and APEX namespace stay intact.
Unsupported ELF layouts fail before any files in the port tree are modified.
"""
import hashlib
import io
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def patch_auth(data):
    if data[:6] != b'\x7fELF\x02\x01' or len(data) < 64:
        raise ValueError('adbd must be an ELF64 little-endian executable')
    if struct.unpack_from('<H', data, 18)[0] != 183:
        raise ValueError('adbd must target ARM64')
    shoff = struct.unpack_from('<Q', data, 40)[0]
    entsize, count = struct.unpack_from('<HH', data, 58)
    if entsize != 64 or not count or shoff + count * entsize > len(data):
        raise ValueError('adbd has no usable ELF section table')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, shoff + i * 64) for i in range(count)]
    symbols = {}
    for s in sections:
        if s[1] not in (2, 11):
            continue
        if s[9] != 24 or s[6] >= count or s[4] + s[5] > len(data):
            raise ValueError('Invalid adbd symbol table')
        strings = sections[s[6]]
        names = data[strings[4]:strings[4] + strings[5]]
        for offset in range(s[4], s[4] + s[5], 24):
            name, info, _, ndx, addr, size = struct.unpack_from('<IBBHQQ', data, offset)
            label = names[name:].split(b'\0', 1)[0]
            if label in (b'auth_required', b'main', b'_Z9adbd_maini') and ndx:
                symbols[label] = (info, ndx, addr, size)
    if b'auth_required' not in symbols or b'main' not in symbols:
        raise ValueError('Unsupported donor adbd: auth_required/main symbols missing; '
                         'no foreign daemon will be substituted')
    info, ndx, target, size = symbols[b'auth_required']
    section = sections[ndx]
    if info & 15 != 1 or size != 1 or section[1] != 1 or not section[2] & 1:
        raise ValueError('Unsupported auth_required storage')
    auth_offset = section[4] + target - section[3]
    if not 0 <= auth_offset < len(data) or data[auth_offset] not in (0, 1):
        raise ValueError('Invalid auth_required initializer')

    def signed(value, bits):
        return value - (1 << bits) if value & (1 << (bits - 1)) else value

    # PIE builds may access auth_required through the GOT. Resolve only
    # explicit R_AARCH64_RELATIVE relocations, not arbitrary pointer bytes.
    relocations = {}
    def virtual_u64(addr):
        for s in sections:
            if s[1] != 8 and s[3] <= addr and addr + 8 <= s[3] + s[5]:
                return struct.unpack_from('<Q', data, s[4] + addr - s[3])[0]
        raise ValueError('Invalid adbd relocation target')

    for sec in sections:
        if sec[1] == 4 and sec[9] == 24:
            for offset in range(sec[4], sec[4] + sec[5], 24):
                addr, info, addend = struct.unpack_from('<QQq', data, offset)
                if info == 1027:
                    relocations[addr] = addend
        elif sec[1] in (19, 0x6fffff00) and sec[9] == 8:
            cursor = 0
            for offset in range(sec[4], sec[4] + sec[5], 8):
                entry = struct.unpack_from('<Q', data, offset)[0]
                if entry & 1:
                    for bit in range(1, 64):
                        if entry & (1 << bit):
                            addr = cursor + (bit - 1) * 8
                            relocations[addr] = virtual_u64(addr)
                    cursor += 63 * 8
                else:
                    relocations[entry] = virtual_u64(entry)
                    cursor = entry + 8

    stores = []
    # Resolve only direct ADR/ADRP + optional ADD addresses. Abort at other
    # writes to the base register; never guess an address from a byte pattern.
    for sec in sections:
        if sec[1] != 1 or not sec[2] & 4:
            continue
        start, length, address = sec[4], sec[5], sec[3]
        if start + length > len(data):
            raise ValueError('Invalid executable section')
        words = struct.unpack_from('<' + 'I' * (length // 4), data, start)

        def resolve(index, reg, depth=0):
            if depth > 4 or reg == 31:
                return None
            for j in range(index - 1, max(-1, index - 129), -1):
                w = words[j]
                if w & 0xfc000000 == 0x94000000 and reg < 19:
                    return None  # BL clobbers caller-saved registers.
                if w & 31 != reg:
                    continue
                kind = w & 0x9f000000
                if kind in (0x90000000, 0x10000000):
                    imm = signed(((w >> 5) & 0x7ffff) << 2 | ((w >> 29) & 3), 21)
                    pc = address + j * 4
                    return (pc & ~4095) + (imm << 12) if kind == 0x90000000 else pc + imm
                if w & 0xff000000 == 0x91000000:
                    base = resolve(j, (w >> 5) & 31, depth + 1)
                    return None if base is None else base + (((w >> 10) & 4095) << (12 if w & (1 << 22) else 0))
                if w & 0xffc00000 == 0xf9400000:
                    base = resolve(j, (w >> 5) & 31, depth + 1)
                    return None if base is None else relocations.get(base + (((w >> 10) & 4095) * 8))
                # Stores, conditional branches and comparisons do not write Rd.
                if w & 0x3b000000 == 0x39000000 and not w & (1 << 22):
                    continue
                if w & 0x7c000000 in (0x14000000, 0x34000000):
                    continue
                return None
            return None

        for i, w in enumerate(words):
            if w & 0xffc00000 == 0x39000000:
                displacement = (w >> 10) & 4095
            elif w & 0xffe00c00 == 0x38000000:
                displacement = signed((w >> 12) & 511, 9)
            else:
                continue
            base = resolve(i, (w >> 5) & 31)
            if base is not None and base + displacement == target:
                stores.append((start + i * 4, w, address + i * 4))
    functions = [v for k, v in symbols.items() if k in (b'main', b'_Z9adbd_maini')]
    if not stores or not any(fn[2] <= addr < fn[2] + fn[3] for _, _, addr in stores for fn in functions):
        raise ValueError('Unsupported donor adbd: no verified main auth_required store')
    patched = bytearray(data)
    patched[auth_offset] = 0
    edits = [{'offset': auth_offset, 'before': data[auth_offset:auth_offset + 1].hex(), 'after': '00'}]
    for offset, word, _ in stores:
        replacement = struct.pack('<I', word | 31)  # STRB WZR, same target.
        patched[offset:offset + 4] = replacement
        edits.append({'offset': offset, 'before': data[offset:offset + 4].hex(), 'after': replacement.hex()})
    return bytes(patched), {'method': 'donor-auth-required-zero-v1',
                           'original_sha256': digest(data), 'patched_sha256': digest(patched),
                           'edits': edits, 'uid_drop_unchanged': True,
                           'runtime_boot_verified': False}


def extract_adbd(package):
    """Read exact ZIP members, never extract arbitrary archive paths."""
    with zipfile.ZipFile(package) as archive:
        if 'original_apex' in archive.namelist():
            with zipfile.ZipFile(io.BytesIO(archive.read('original_apex'))) as inner:
                payload = inner.read('apex_payload.img')
        else:
            payload = archive.read('apex_payload.img')
    with tempfile.TemporaryDirectory(prefix='port-adbd-') as tmp:
        folder = Path(tmp)
        image, daemon = folder / 'payload.img', folder / 'adbd'
        image.write_bytes(payload)
        if payload[1080:1082] == b'\x53\xef':
            subprocess.run(['debugfs', '-R', f'dump -p /bin/adbd {daemon}', str(image)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        elif payload[1024:1028] == b'\xe2\xe1\xf5\xe0':
            subprocess.run(['fsck.erofs', '--extract=' + str(folder / 'unpacked'), str(image)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            daemon = folder / 'unpacked/bin/adbd'
        else:
            raise ValueError('Unsupported donor adbd APEX filesystem')
        if not daemon.is_file() or daemon.is_symlink():
            raise ValueError('Donor adbd APEX has no regular /bin/adbd')
        return daemon.read_bytes()


def derive_from_donor(root):
    apex_dir = root / 'system/system/apex'
    candidates = sorted(p for p in apex_dir.glob('*adbd*')
                        if not p.is_symlink() and
                        (p.suffix in ('.apex', '.capex') or (p / 'bin/adbd').is_file()))
    if len(candidates) != 1:
        raise ValueError('Expected exactly one donor adbd APEX; found: ' +
                         ', '.join(p.name for p in candidates))
    package = candidates[0]
    flattened = package.is_dir()
    if flattened and (package / 'bin/adbd').is_symlink():
        raise ValueError('Flattened donor adbd must be a regular executable')
    raw = (package / 'bin/adbd').read_bytes() if flattened else extract_adbd(package)
    patched, report = patch_auth(raw)
    report.update(donor_file=package.name,
                  donor_sha256=digest(raw if flattened else package.read_bytes()),
                  profile='derived-from-donor', flattened_apex=flattened)
    return patched, report
