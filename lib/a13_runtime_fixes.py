"""Verified MIUI14 SDK33 / Ace 3V SDK34 hardware compatibility.

Binary changes are BSDIFF40 deltas from the tested donor, never offset patches
against an unknown ELF. All inputs/assets are checked before writing the tree.
Only Python's standard library is needed by the build runner.
"""
import bz2
import hashlib
import json
import lzma
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def inside(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Unsafe SDK33 profile path: ' + relative)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError('SDK33 profile path escapes root: ' + relative)
    return path


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


def preflight(root, profile, port):
    """Reject unknown libraries before installing any compatibility payload."""
    descriptor = json.loads((profile / 'profile.json').read_text('utf-8'))
    if port.sdk(root) != 33 or descriptor['sdk'] != 33:
        raise ValueError('MIUI14 compatibility requires SDK33')
    version = (root / 'vendor/etc/selinux/plat_sepolicy_vers.txt').read_text().strip()
    if version != descriptor['vendor_mapping']:
        raise ValueError('MIUI14 SDK33 profile requires SDK34 vendor/odm (mapping 34.0)')
    for name, expected in descriptor['binder_abi_guards'].items():
        if sha((root / 'system/system/lib64' / name).read_bytes()) != expected:
            raise ValueError('Unknown SDK33 Binder ABI: ' + name)
    planned = []
    for entry in descriptor['patches']:
        path = inside(root, entry['path'])
        current = path.read_bytes()
        digest = sha(current)
        if digest not in (entry['before_sha256'], entry['after_sha256']):
            raise ValueError('Unverified MIUI14 binary: ' + entry['path'] + ' sha256=' + digest)
        patch = inside(profile, entry['patch']).read_bytes()
        if sha(patch) != entry['patch_sha256']:
            raise ValueError('Corrupt SDK33 patch: ' + entry['patch'])
        data = current if digest == entry['after_sha256'] else apply_delta(
            current, patch, entry['after_bytes'])
        if len(data) != entry['after_bytes'] or sha(data) != entry['after_sha256']:
            raise ValueError('SDK33 patch output mismatch: ' + entry['path'])
        planned.append((entry['path'], data))
    for entry in descriptor['files']:
        data = inside(profile, entry['asset']).read_bytes()
        if sha(data) != entry['asset_sha256']:
            raise ValueError('Corrupt SDK33 asset: ' + entry['asset'])
        if entry['compression'] == 'xz':
            data = lzma.decompress(data)
        elif entry['compression'] is not None:
            raise ValueError('Unknown SDK33 asset encoding')
        if len(data) != entry['bytes'] or sha(data) != entry['sha256']:
            raise ValueError('SDK33 asset output mismatch: ' + entry['asset'])
        path = inside(root, entry['path'])
        # Never replace an unrelated VNDK package or platform mapping.
        if path.exists():
            if sha(path.read_bytes()) != entry['sha256']:
                raise ValueError('Conflicting SDK33 compatibility asset: ' + entry['path'])
        planned.append((entry['path'], data))
    for entry in descriptor['static_files']:
        data = inside(profile, entry['asset']).read_bytes()
        if sha(data) != entry['sha256']:
            raise ValueError('Corrupt SDK33 static file: ' + entry['asset'])
    gms = descriptor['gms']
    apk = root / 'product/priv-app/GmsCore/GmsCore.apk'
    if not apk.is_file() or sha(apk.read_bytes()) != gms['GmsCore_sha256']:
        raise ValueError('MIUI14 Google factory layout requires the verified donor GmsCore')
    with zipfile.ZipFile(apk) as archive:
        expected = {m['apk_entry'] for m in gms['factory_modules']}
        actual = {n for n in archive.namelist()
                  if n.startswith('assets/chimera-modules/') and n.endswith('.apk')}
        if actual != expected:
            raise ValueError('SDK33 Google embedded module list mismatch')
        for entry in gms['factory_modules']:
            if Path(entry['name']).name != entry['name']:
                raise ValueError('Invalid Google factory module name')
            data = archive.read(entry['apk_entry'])
            if len(data) != entry['bytes'] or sha(data) != entry['sha256']:
                raise ValueError('SDK33 Google embedded module hash mismatch')
            planned.append(('product/priv-app/GmsCore/m/container/' + entry['name'], data))
    # Derive/validate ADB before mutating the tree, including unknown donor checks.
    from adbd_patch import derive_from_donor
    daemon, _ = derive_from_donor(root)
    expected = descriptor.get('boot_adb_verified_sha256')
    if expected and sha(daemon) != expected:
        raise ValueError('Unverified SDK33 boot ADB daemon')
    return descriptor, planned


def install_file(root, relative, data, port, label=None, mode='0644'):
    part, name = relative.split('/', 1)
    path = inside(root, relative)
    meta = port.Metadata(root, part)
    parents = list(path.parents)
    for parent in reversed(parents[:parents.index(root / part)]):
        if not parent.exists():
            parent.mkdir()
            meta.pin(parent.relative_to(root / part).as_posix(), mode='0755')
    path.write_bytes(data)
    if relative not in meta.fs or label:
        meta.pin(name, label or 'system_file', mode)
    meta.save()


def append_once(path, marker, text, port):
    current = path.read_text('utf-8') if path.exists() else ''
    if marker not in current:
        port.write(path, current.rstrip() + '\n' + marker + '\n' + text.rstrip() + '\n')


def guard_dsu_verifier(text):
    """Split actions around verifier, preserving physical boot command order."""
    if 'property:sys.ace3v.run_update_verifier=1' in text:
        return text
    lines = text.splitlines(keepends=True)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == 'exec_start update_verifier_nonencrypted':
            header = next((s.rstrip() for s in reversed(lines[:i]) if s.startswith('on ')), '')
            if not header.startswith('on zygote-start &&'):
                raise ValueError('Unknown SDK33 update_verifier action')
            lines[i:i + 1] = ['\n' + header + ' && property:sys.ace3v.run_update_verifier=1\n',
                              lines[i], '\n' + header + '\n']
    return ''.join(lines)


def vintf_compat(root, port):
    changed = []
    for name in ('manifest_cliffs.xml', 'manifest_pineapple.xml'):
        path = root / 'vendor/etc/vintf' / name
        if not path.exists():
            continue
        data = path.read_text('utf-8')
        before = ET.fromstring(data)
        if before.get('version') == '5.0':
            continue
        if (before.attrib != {'version': '7.0', 'type': 'device', 'target-level': '8'}
                or {n.tag for n in before.iter()} -
                {'manifest', 'hal', 'name', 'transport', 'fqname', 'version', 'sepolicy'}):
            raise ValueError('Unsupported SDK34 VINTF schema: ' + name)
        after = re.sub(r'(<manifest\s+)version="7.0"', r'\g<1>version="5.0"', data, count=1)
        check = ET.fromstring(after)
        check.set('version', '7.0')
        if ET.tostring(check) != ET.tostring(before):
            raise ValueError('VINTF HAL contents changed')
        port.write(path, after)
        changed.append(path.relative_to(root).as_posix())
    path = root / 'system_ext/etc/vintf/manifest/atcmdfwd-saidl.xml'
    if path.exists():
        text = path.read_text('utf-8')
        match = re.fullmatch(r'/\*(.*?)\*/\s*(<manifest.*)', text, re.S)
        if match:
            if '--' in match[1]:
                raise ValueError('Invalid VINTF copyright comment')
            xml = ET.fromstring(match[2])
            if xml.tag != 'manifest' or xml.get('version') != '1.0':
                raise ValueError('Unknown atcmdfwd VINTF layout')
            port.write(path, '<!--\n' + match[1].strip() + '\n-->\n' + re.sub(
                r'(<manifest\s+)version="1.0"', r'\g<1>version="5.0"', match[2], count=1))
            changed.append(path.relative_to(root).as_posix())
    return changed


def add_linker_library(data, field):
    """Append one provideLibs(3)/requireLibs(4) entry without rewriting protobuf."""
    name = b'libace3v_binder_compat.so'
    encoded = bytes([(field << 3) | 2, len(name)]) + name
    # Walk protobuf wire fields; never search arbitrary substrings for presence.
    def varint(pos):
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                break
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, pos
        raise ValueError('Invalid linker config protobuf')
    pos = 0
    while pos < len(data):
        tag, pos = varint(pos)
        if tag & 7 == 0:
            _, pos = varint(pos)
        elif tag & 7 == 2:
            length, pos = varint(pos)
            if pos + length > len(data):
                raise ValueError('Truncated linker config protobuf')
            if tag >> 3 == field and data[pos:pos + length] == name:
                return data
            pos += length
        else:
            raise ValueError('Unsupported linker config wire type')
    return data + encoded


def remove_cache(root, port):
    removed = []
    for path in (root / 'system/system/framework').rglob('boot-miui-framework.*'):
        if path.suffix in ('.art', '.oat', '.vdex'):
            path.unlink()
            removed.append(path.relative_to(root).as_posix())
    for abi in ('arm', 'arm64'):
        folder = root / 'system_ext/framework/oat' / abi
        for pattern in ('miui-wifi-service.odex', 'miui-wifi-service.vdex',
                        'boot-miui-framework.art', 'boot-miui-framework.oat', 'boot-miui-framework.vdex'):
            path = folder / pattern
            if path.exists():
                path.unlink()
                removed.append(path.relative_to(root).as_posix())
    for part in port.PARTS:
        meta = port.Metadata(root, part)
        for name in removed:
            meta.fs.pop(name, None)
            meta.ctx.pop(name, None)
        meta.save()
    return removed


def deduplicate_vendor_property_contexts(root, port):
    """Drop equivalent repeated recipe entries; never choose conflicting labels."""
    path = root / 'vendor/etc/selinux/vendor_property_contexts'
    original = path.read_text('utf-8')
    seen, kept, removed = {}, [], []
    for line in original.splitlines(keepends=True):
        fields = line.partition('#')[0].split()
        if len(fields) >= 2:
            kind = fields[2] if len(fields) > 2 and fields[2] in ('exact', 'prefix') else 'prefix'
            key = fields[0], kind
            value = fields[1], tuple(fields[3:] if len(fields) > 2 and fields[2] in ('exact', 'prefix')
                                      else fields[2:])
            if key in seen:
                if seen[key] != value:
                    raise ValueError('Conflicting SDK33 vendor property context: ' + key[0])
                removed.append(key[0])
                continue
            seen[key] = value
        kept.append(line)
    if removed:
        port.write(path, ''.join(kept))
    return removed


def finish(root, assets, compiler, port, adb=False):
    profile = assets.parent / 'devices/OnePlusAce3V/android-33'
    descriptor, planned = preflight(root, profile, port)
    report = {'device': 'OnePlusAce3V', 'donor_sdk': 33, 'ace3v_android13': True,
              'profile': descriptor['name'], 'noauth_boot_adb': True,
              'force_adb': adb, 'secure_boot_adb': False, 'crypto_log_wrapper': False,
              'binary_patches': descriptor['patches']}
    for relative, data in planned:
        label = 'system_lib_file' if relative.endswith('/libace3v_binder_compat.so') else None
        install_file(root, relative, data, port, label)
    for entry in descriptor['static_files']:
        if entry['install']:
            install_file(root, entry['path'], (profile / entry['asset']).read_bytes(), port,
                         entry.get('label'))
    # Namespace only the conflicting old attribute, never the newer vendor type.
    for relative in ('system/system/etc/selinux/plat_sepolicy.cil',
                     'system_ext/etc/selinux/system_ext_sepolicy.cil',
                     'product/etc/selinux/product_sepolicy.cil'):
        path = root / relative
        if path.exists():
            port.write(path, re.sub(r'(?<![\w-])vendor_service(?![\w-])',
                                   'miui13_vendor_service_attr', path.read_text('utf-8')))
    mapping = 'system_ext/etc/selinux/mapping/34.0.cil'
    path = root / mapping
    if not path.exists():
        install_file(root, mapping, b'', port)
    append_once(path, '; Ace3V SDK33 PerfService mapping',
                '(typeattributeset vendor_perfservice_34_0 (vendor_perfservice))', port)
    append_once(root / 'system_ext/etc/selinux/system_ext_sepolicy.cil',
                '; Ace3V PerfService HIDL client', (profile / 'perfservice.cil').read_text(), port)
    append_once(root / 'vendor/etc/selinux/vendor_sepolicy.cil',
                '; Ace3V SDK33 system UID helper', (profile / 'port.cil').read_text(), port)
    fc = root / 'system/system/etc/selinux/plat_file_contexts'
    for entry in ('/system/lib64/libace3v_binder_compat\\.so u:object_r:system_lib_file:s0',
                  '/system/bin/ace3v-hardware -- u:object_r:ace3v_port_exec:s0'):
        append_once(fc, entry, '', port)
    install_file(root, 'system/system/bin/ace3v-hardware',
                 (root / 'system/system/bin/app_process64').read_bytes(), port, 'ace3v_port_exec', '0755')
    zygote = root / 'system/system/etc/init/hw/init.zygote64.rc'
    if not zygote.exists():
        source = root / 'system/system/etc/init/hw/init.zygote64_32.rc'
        primary = source.read_text().split('\nservice zygote_secondary ', 1)[0].rstrip() + '\n'
        if not primary.startswith('service zygote /system/bin/app_process64 ') or primary.count('service ') != 1:
            raise ValueError('Unknown SDK33 primary zygote layout')
        install_file(root, zygote.relative_to(root).as_posix(), primary.encode(), port)
    for rel, field in (('system/system/etc/linker.config.pb', 3), ('vendor/etc/linker.config.pb', 4)):
        path = root / rel
        path.write_bytes(add_linker_library(path.read_bytes(), field))
    existing = {}
    for base, stem in port.POLICY_PARTITIONS + (('vendor', 'vendor'), ('odm', 'odm')):
        path = root / base / 'etc/selinux' / (stem + '_service_contexts')
        if path.exists():
            for line in path.read_text().splitlines():
                bits = line.split()
                if len(bits) == 2 and not bits[0].startswith('#'):
                    existing[bits[0]] = bits[1]
    lines = []
    for name, label in descriptor['hardware_service_labels'].items():
        if name in existing and existing[name] != label:
            raise ValueError('Conflicting hardware service label: ' + name)
        if name not in existing:
            lines.append(name + ' ' + label)
    if lines:
        append_once(root / 'vendor/etc/selinux/vendor_service_contexts',
                    '# Exact SDK34 hardware AIDL service labels', '\n'.join(lines), port)
    report['vintf'] = vintf_compat(root, port)
    init = root / 'system/system/etc/init/hw/init.rc'
    port.write(init, guard_dsu_verifier(init.read_text()))
    report['cache_removed'] = remove_cache(root, port)
    audio = root / 'product/media/bootaudio.mp3'
    audio.unlink(missing_ok=True)
    meta = port.Metadata(root, 'product')
    for table in (meta.fs, meta.ctx):
        table.pop('product/media/bootaudio.mp3', None)
    meta.save()
    # Stock ODM ABI/zygote properties must remain available; never create an
    # incomplete odm/etc/build.prop that masks the root-layout stock build.prop.
    port.set_props(root / 'system/system/build.prop', {
        'ro.system.product.cpu.abilist': 'arm64-v8a', 'ro.system.product.cpu.abilist32': '',
        'ro.system.product.cpu.abilist64': 'arm64-v8a'})
    port.set_props(root / 'vendor/build.prop', port.properties(profile / 'vendor.build.prop'))
    remove = {'persist.vendor.disable_idle_fps.threshold', 'ro.vendor.display.switch_resolution.support',
              'ro.vendor.mi_sf.enable_tp_idle_automode', 'ro.vendor.mi_sf.enable_automode_for_maxfps_setting',
              'ro.vendor.mi_sf.supported_automode_maxfps_list', 'ro.vendor.mi_sf.support_automode_for_normalfps',
              'ro.vendor.mi_sf.support_gradient_idleframerate', 'ro.vendor.mi_sf.set_gradient_idle_timer_ms',
              'persist.vendor.sys.fp.fod.us.target'}
    path = root / 'vendor/build.prop'
    port.write(path, '\n'.join(s for s in path.read_text().splitlines() if s.split('=', 1)[0] not in remove) + '\n')
    report['display_config'] = port.install_display_config(root, profile)
    path = root / 'system_ext/etc/init/init.qseelogd.rc'
    if path.exists():
        port.write(path, '')
    port.set_props(root / 'system_ext/etc/build.prop', {'persist.sys.qseelogd': 'false'})
    report['setupwizard_removed'] = port.remove_setupwizard(root)
    report['optional_oem_bind_wait_removed'] = port.remove_optional_oem_bind_wait(root)
    provision = root / 'system_ext/priv-app/Provision'
    for name in ('oat', 'lib'):
        shutil.rmtree(provision / name, ignore_errors=True)
    port.harden_enforcing_init(root)
    report['duplicate_vendor_property_contexts_removed'] = deduplicate_vendor_property_contexts(root, port)
    report['boot_adb'] = port.install_noauth_boot_adb(root, assets)
    report['boot_adb']['runtime_boot_verified'] = (
        report['boot_adb']['daemon_sha256'] == descriptor['boot_adb_verified_sha256'])
    report['selinux'] = port.compile_policy(root, compiler, enforcing=True)
    info = subprocess.check_output(['seinfo', str(root / 'odm/etc/selinux/precompiled_sepolicy'),
                                    '--type'], text=True)
    types = set(re.findall(r'^\s+([A-Za-z0-9_-]+)\s*$', info, re.M))
    for part in port.PARTS:
        for name, labels in port.Metadata(root, part).ctx.items():
            if labels[0].split(':')[2] not in types:
                raise ValueError('Undefined SDK33 image SELinux label: ' + name + ' ' + labels[0])
    report['image_selinux_labels_verified'] = True
    report['google_factory_modules'] = {'installed': len(descriptor['gms']['factory_modules']),
        'signed_container_unchanged': True, 'independent_modules_unchanged': True,
        'fresh_userdata_runtime_verified': False}
    report['tested'] = descriptor['tested']
    report['boringssl_reboot_guards_removed'] = 0
    port.write(root / 'port_compat.json', json.dumps(report, indent=2) + '\n')
    return report
