#!/usr/bin/env python3
"""Versioned assembly and compatibility fixes shared by CLI and Actions."""
import argparse
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

PARTS = ('system', 'system_ext', 'product', 'vendor', 'odm')


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def properties(path):
    if not path.is_file():
        return {}
    return dict(line.strip().split('=', 1) for line in path.read_text('utf-8').splitlines()
                if '=' in line and not line.lstrip().startswith('#'))


def set_props(path, values, strip_imports=False):
    lines = path.read_text('utf-8').splitlines() if path.exists() else []
    lines = [line for line in lines
             if line.split('=', 1)[0].strip() not in values
             and not (strip_imports and re.match(r'^\s*import\s', line))]
    write(path, '\n'.join(lines).rstrip() + '\n' +
          ''.join(f'{key}={value}\n' for key, value in values.items()))


def sdk(root):
    for rel in ('system/system/build.prop', 'system_ext/etc/build.prop',
                'system_ext/build.prop', 'product/etc/build.prop'):
        props = properties(root / rel)
        for key in ('ro.build.version.sdk', 'ro.system.build.version.sdk',
                    'ro.system_ext.build.version.sdk', 'ro.product.build.version.sdk'):
            if props.get(key, '').isdigit():
                return int(props[key])
    raise ValueError(f'Cannot determine Android SDK from {root}')


def donor_name(root):
    features = root / 'product/etc/device_features'
    names = {path.stem for path in features.glob('*.xml')}
    product = properties(root / 'product/etc/build.prop').get('ro.product.product.name', '')
    if product in names:
        return product
    for rel in ('mi_ext/etc/build.prop', 'system/mi_ext/etc/build.prop',
                'product/etc/build.prop', 'system/system/build.prop'):
        props = properties(root / rel)
        value = props.get('ro.product.mod_device', '').removesuffix('_global')
        if value and re.fullmatch(r'[A-Za-z0-9_-]+', value):
            matches = [name for name in names if value == name or value.startswith(name + '_')]
            if matches:
                return max(matches, key=len)
            return value
    raise ValueError('Missing donor ro.product.mod_device; cannot select FeatureParser XML')


class Metadata:
    """Keep the complete fs_config tail (including capabilities) when relocating."""
    def __init__(self, root, part):
        self.root, self.part = root, part
        self.fs, self.ctx = {}, {}
        for line in (root / 'config' / f'{part}_fs_config').read_text('utf-8').splitlines():
            bits = line.split()
            if len(bits) >= 4:
                self.fs[bits[0].strip('/')] = bits[1:]
        for line in (root / 'config' / f'{part}_file_contexts').read_text('utf-8').splitlines():
            bits = line.split()
            if len(bits) >= 2:
                self.ctx[re.sub(r'\\(.)', r'\1', bits[0]).strip('/')] = bits[1:]

    def save(self):
        write(self.root / 'config' / f'{self.part}_fs_config',
              ''.join((key or '/') + ' ' + ' '.join(value) + '\n'
                      for key, value in self.fs.items()))
        write(self.root / 'config' / f'{self.part}_file_contexts',
              ''.join('/' + re.escape(key) + ' ' + ' '.join(value) + '\n'
                      for key, value in self.ctx.items()))

    def relocate(self, prefix, target, new_prefix):
        for src, dst in ((self.fs, target.fs), (self.ctx, target.ctx)):
            for key, value in list(src.items()):
                if key == prefix or key.startswith(prefix + '/'):
                    dst[new_prefix + key[len(prefix):]] = value
                    del src[key]

    def pin(self, rel, label='system_file', mode='0644'):
        key = self.part + '/' + rel
        self.fs[key] = ['0', '0', mode]
        self.ctx[key] = [f'u:object_r:{label}:s0']


def merge(source, target):
    # Rename leaves preserves both native symlinks and Windows extractor links.
    if source.is_symlink() or source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_dir() and not target.is_symlink():
            raise ValueError(f'File/directory conflict: {source} -> {target}')
        if target.exists() or target.is_symlink():
            target.unlink()
        source.rename(target)
    elif source.is_dir():
        if target.is_symlink() or target.is_file():
            raise ValueError(f'Directory/file conflict: {source} -> {target}')
        target.mkdir(parents=True, exist_ok=True)
        for child in list(source.iterdir()):
            merge(child, target / child.name)
        source.rmdir()


def link(path, target):
    if path.exists() or path.is_symlink():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        path.write_bytes(b'!<symlink>' + (target + '\0').encode('utf-16'))
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if not ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | 4):
            raise OSError('Cannot mark extractor symlink: ' + str(path))
    else:
        path.symlink_to(target)


def assemble(root, ace16):
    if os.name != 'nt':
        # Windows MIO extraction encodes links as small SYSTEM-attribute files.
        # On Linux convert these before merging/repacking extracted-tree input.
        for part in (*PARTS, 'mi_ext'):
            for path in (root / part).rglob('*'):
                if not path.is_symlink() and path.is_file() and path.stat().st_size < 4096:
                    data = path.read_bytes()
                    if data.startswith(b'!<symlink>\xff\xfe'):
                        target = data[10:].decode('utf-16').rstrip('\0')
                        link(path, target)
    meta = {part: Metadata(root, part) for part in (*PARTS, 'mi_ext')}
    for part in ('product', 'system_ext', 'system'):
        source = root / 'mi_ext' / part
        dest = 'system/system' if part == 'system' else part
        if source.is_dir():
            merge(source, root / dest)
            meta['mi_ext'].relocate('mi_ext/' + part, meta[part], dest)
    mi_props = properties(root / 'mi_ext/etc/build.prop')
    mi_props.pop('ro.vendor.build.ab_ota_partitions', None)
    set_props(root / 'product/etc/build.prop', mi_props)
    if ace16:
        merge(root / 'mi_ext', root / 'system/mi_ext')
        meta['mi_ext'].relocate('mi_ext', meta['system'], 'system/mi_ext')
        for part in ('product', 'system', 'system_ext'):
            link(root / 'system/mi_ext' / part, '/' + part)
            meta['system'].pin('mi_ext/' + part, mode='0777')
        write(root / 'system/mi_ext/etc/init/init.miui.mi_ext.rc',
              '# Payload merged into system, system_ext and product.\n')
        if (root / 'product/pangu').is_dir():
            merge(root / 'product/pangu', root / 'system/system/pangu')
            meta['product'].relocate('product/pangu', meta['system'], 'system/system/pangu')
            link(root / 'product/pangu', '/system/pangu')
            meta['product'].pin('pangu', mode='0777')
    else:
        # Preserve the existing Android 17 pangu layout.
        set_props(root / 'system/system/build.prop', mi_props)
        if (root / 'product/pangu/system').is_dir():
            merge(root / 'product/pangu/system', root / 'system/system')
            meta['product'].relocate('product/pangu/system', meta['system'], 'system/system')
    for value in meta.values():
        value.save()


def replace_apex(root, stock):
    if not stock or sdk(stock) != sdk(root):
        release = {35: 15, 36: 16, 37: 17}.get(sdk(root), sdk(root))
        raise ValueError(f'Ace 3V Android {release} requires Android {release} stock system_ext APEX; '
                         'use --apex-stock with a matching ROM/extracted tree')
    source = stock / 'system_ext/apex'
    files = sorted(source.glob('*.apex')) + sorted(source.glob('*.capex'))
    if not files:
        raise ValueError('Matching stock system_ext/apex is empty')
    src, dst = Metadata(stock, 'system_ext'), Metadata(root, 'system_ext')
    for table in (dst.fs, dst.ctx):
        for key in list(table):
            if key.startswith('system_ext/apex/'):
                del table[key]
    shutil.rmtree(root / 'system_ext/apex', ignore_errors=True)
    (root / 'system_ext/apex').mkdir(parents=True)
    for file in files:
        shutil.copy2(file, root / 'system_ext/apex' / file.name)
        key = 'system_ext/apex/' + file.name
        if key not in src.fs or key not in src.ctx:
            raise ValueError(f'Missing source APEX metadata: {key}')
        dst.fs[key], dst.ctx[key] = src.fs[key], src.ctx[key]
    dst.save()
    return {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in files}


def force_adb(root, assets, ace16=False):
    # SDK35 always uses authenticated ADB with the normal shell UID, even if
    # an existing workflow dispatch requests the legacy development flag.
    if sdk(root) == 35:
        return secure_adb(root, assets, ace16=True)
    debug = {'ro.debuggable': '1', 'ro.secure': '0', 'ro.adb.secure': '0'}
    prop_default = root / 'system/system/etc/prop.default'
    if ace16:
        for rel in ('system/system/etc/prop.default', 'system/system/build.prop'):
            set_props(root / rel, debug)
    else:
        # Retain the existing Actions recipe for Android 17/other devices.
        set_props(prop_default if prop_default.exists() else root / 'system/system/build.prop', debug)
    set_props(root / 'system/system/build.prop', {'persist.sys.usb.config': 'adb'})
    set_props(root / 'product/etc/build.prop', {'persist.sys.usb.config': 'adb'})
    set_props(root / 'vendor/build.prop', {'persist.vendor.usb.config': 'adb'})
    if ace16:
        init = root / 'system/system/etc/init/hw/init.rc'
        text = init.read_text('utf-8')
        text = re.sub(r'^.*xeutoolbox\s+-n\s+ro\.(?:secure\s+1|debuggable\s+0)\s*\n',
                      '', text, flags=re.M)
        write(init, text)
    for rel in ('system_ext/etc/init/init.miui.ext.rc', 'system_ext/etc/init/miuserfs.rc') if ace16 else ():
        path = root / rel
        if path.exists():
            lines = path.read_text('utf-8').splitlines()
            write(path, '\n'.join(line + ' && property:persist.sys.ace3v.miui_debug=1'
                                  if line.startswith('on ') and 'property:ro.debuggable=1' in line
                                  and 'persist.sys.ace3v.miui_debug' not in line else line
                                  for line in lines) + '\n')
    target = root / 'system/system/etc/init/hyperos_force_adb.rc'
    target.parent.mkdir(parents=True, exist_ok=True)
    if ace16:
        shutil.copy2(assets / 'hyperos_force_adb.rc', target)
        meta = Metadata(root, 'system')
        meta.pin('system/etc/prop.default')
        meta.pin('system/etc/init/hyperos_force_adb.rc')
        meta.save()
    else:
        write(target, '# Shared early ADB; existing Android 17 recipe.\n'
              'on early-init\n    setprop persist.sys.usb.config adb\n'
              '    setprop persist.vendor.usb.config adb\n\n'
              'on boot\n    setprop persist.sys.usb.config adb\n'
              '    setprop persist.vendor.usb.config adb\n    setprop sys.usb.config adb\n    start adbd\n\n'
              'on post-fs-data\n    setprop persist.sys.usb.config adb\n'
              '    setprop persist.vendor.usb.config adb\n    setprop sys.usb.config adb\n    start adbd\n')


def remove_adb_property_resets(root):
    """Remove standalone donor property reset commands, retaining static defaults."""
    command = re.compile(
        r'^[ \t]*(?:exec(?:_background)?\b[^\n]*?--\s+)?'
        r'(?:\S*/)?(?:resetprop|xeutoolbox)\s+(?:-\S+\s+)*'
        r'ro\.(?:secure|debuggable)\s+\S+[ \t]*(?:#.*)?$', re.M)
    removed = 0
    for part in PARTS:
        for path in (root / part).rglob('*'):
            if path.suffix not in ('.rc', '.sh') or path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            if data.startswith(b'!<symlink>'):
                continue
            text = data.decode('utf-8')
            cleaned, count = command.subn('', text)
            if count:
                write(path, cleaned)
                removed += count
    return removed


def secure_adb(root, assets, ace16=False):
    """Start ADB at boot while retaining authentication and shell privileges."""
    secure = {'ro.debuggable': '0', 'ro.secure': '1', 'ro.adb.secure': '1'}
    targets = ['system/system/build.prop']
    if ace16 or (root / 'system/system/etc/prop.default').exists():
        targets.insert(0, 'system/system/etc/prop.default')
    for rel in targets:
        set_props(root / rel, secure)
    set_props(root / 'system/system/build.prop', {'persist.sys.usb.config': 'adb'})
    set_props(root / 'product/etc/build.prop', {'persist.sys.usb.config': 'adb'})
    set_props(root / 'vendor/build.prop', {'persist.vendor.usb.config': 'adb'})
    forced = root / 'system/system/etc/init/hyperos_force_adb.rc'
    forced.parent.mkdir(parents=True, exist_ok=True)
    ace15 = sdk(root) == 35
    shutil.copy2(assets / ('hyperos_early_adb_a15.rc' if ace15 else 'hyperos_force_adb.rc'), forced)
    if ace15:
        remove_adb_property_resets(root)
        usb = root / 'system/system/etc/init/hw/init.usb.rc'
        if usb.exists():
            write(usb, usb.read_text('utf-8').replace(' --root_seclabel=u:r:su:s0', ''))
    if ace16 and (root / 'config/system_fs_config').exists():
        meta = Metadata(root, 'system')
        meta.pin('system/etc/init/hyperos_force_adb.rc')
        meta.save()


def harden_a15_init(root):
    """Remove donor-only root shell hooks without changing core root daemons."""
    path = root / 'system_ext/etc/init/init.miui.ext.rc'
    if path.exists():
        text = path.read_text('utf-8')
        for name in ('pubcert_download', 'rotatekey_download', 'socid_provision'):
            text = re.sub(r'^service ' + name + r'\s[^\n]*\n(?:[ \t]+[^\n]*\n|\n)*',
                          '', text, flags=re.M)
            text = re.sub(r'^[ \t]+start ' + name + r'\s*\n', '', text, flags=re.M)
        write(path, text)
    profiler = root / 'system/system/etc/init/simpleperf.rc'
    if profiler.exists():
        write(profiler, '# Root boot profiling is disabled in the Android 15 enforcing port.\n')
    ftm = root / 'vendor/etc/init/hw/vendor.oem_ftm_svc_disable.rc'
    if ftm.exists():
        text = ftm.read_text('utf-8')
        text = re.sub(r'(^service console [^\n]*\n(?:(?!service |on )[^\n]*\n)*?)'
                      r'    user root\n', r'\1    user shell\n', text, count=1, flags=re.M)
        write(ftm, text)


def authorize_adb(root, public_key):
    """Optionally trust one caller-provided public key; never ship a private key."""
    text = public_key.read_text('utf-8').strip()
    encoded = text.split()[0]
    if len(base64.b64decode(encoded, validate=True)) != 524:
        raise ValueError('Expected an Android adbkey.pub RSA public key')
    path = root / 'product/etc/security/adb_keys'
    lines = path.read_text('utf-8').splitlines() if path.exists() else []
    if not any(line.split() and line.split()[0] == encoded for line in lines):
        write(path, '\n'.join(lines + [text]) + '\n')
    link(root / 'system/adb_keys', '/product/etc/security/adb_keys')
    for part, rel, mode in (('system', 'adb_keys', '0777'),
                            ('product', 'etc/security/adb_keys', '0644')):
        meta = Metadata(root, part)
        meta.pin(rel, 'adb_keys_file', mode)
        meta.save()
    return hashlib.sha256(encoded.encode('ascii')).hexdigest()


def clean_crypto(root):
    removed = 0
    for relative in ('system/system/etc/init/hw/init.rc', 'vendor/etc/init/boringssl_self_test.rc'):
        path = root / relative
        text = path.read_text('utf-8')
        # Accept the successful R3 tree as well as a clean donor. No logging wrapper is shipped.
        text = re.sub(r'^(service boringssl_self_test\w+) /system/bin/sh '
                      r'/system/bin/ace3v-crypto-test.sh \S+ (\S+)\n'
                      r'    seclabel u:r:(?:vendor_)?boringssl_self_test:s0', r'\1 \2', text, flags=re.M)
        text, count = re.subn(r'^\s*reboot_on_failure reboot,boringssl-self-check-failed[^\S\n]*\n',
                             '', text, flags=re.M)
        removed += count
        write(path, text)
    (root / 'system/system/bin/ace3v-crypto-test.sh').unlink(missing_ok=True)
    return removed


def compile_policy(root, compiler='secilc', enforcing=False):
    vendor = root / 'vendor/etc/selinux'
    version = (vendor / 'plat_sepolicy_vers.txt').read_text('utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+', version):
        raise ValueError('Invalid vendor SELinux mapping version')
    inputs = []
    for part, stem in (('system/system', 'plat'), ('system_ext', 'system_ext'), ('product', 'product')):
        base = root / part / 'etc/selinux'
        inputs.append(base / (stem + '_sepolicy.cil'))
        mapping = base / 'mapping' / (version + '.cil')
        if not mapping.exists():
            raise ValueError(f'Donor lacks vendor mapping {mapping}')
        inputs.append(mapping)
        compat = base / 'mapping' / (version + '.compat.cil')
        if compat.exists():
            inputs.append(compat)
    inputs += [vendor / 'plat_pub_versioned.cil', vendor / 'vendor_sepolicy.cil']
    odm_cil = root / 'odm/etc/selinux/odm_sepolicy.cil'
    if odm_cil.exists():
        inputs.append(odm_cil)
    if enforcing:
        # Remove domain permissiveness in every shipped CIL, including unused
        # userdebug input, so neither the cached nor fallback policy weakens it.
        for partition in PARTS:
            for source in (root / partition).rglob('*.cil'):
                original = source.read_text('utf-8')
                cleaned = re.sub(r'\(typepermissive\s+[^\s()]+\)\s*', '', original)
                if cleaned != original:
                    write(source, cleaned)
    types = set(re.findall(r'\(type\s+([^\s()]+)\)',
                           '\n'.join(path.read_text('utf-8') for path in inputs)))
    policy = vendor / 'vendor_sepolicy.cil'
    text = policy.read_text('utf-8')
    existing = set(re.findall(r'\(typepermissive\s+([^\s()]+)\)', text))
    if not enforcing:
        write(policy, text.rstrip() + '\n; Ace 3V development port: permissive\n' +
              ''.join(f'(typepermissive {name})\n' for name in sorted(types - existing)))
    output = root / 'odm/etc/selinux/precompiled_sepolicy'
    subprocess.run([compiler, '-m', '-M', 'true', '-G', '-N', '-c', '30',
                    '-o', str(output), '-f', os.devnull, *map(str, inputs)], check=True)
    if enforcing:
        # -N matches Android init's runtime compilation of mixed-version CIL;
        # it skips build-time neverallow assertions, not runtime enforcement.
        audit = subprocess.run(['seinfo', str(output), '--permissive'], check=True,
                               capture_output=True, text=True)
        if not re.search(r'Permissive Types:\s+0\b', audit.stdout):
            raise ValueError('Compiled policy contains permissive domains: ' + audit.stdout)
    for base in (vendor, output.parent):
        part = 'vendor' if base == vendor else 'odm'
        meta = Metadata(root, part)
        for name in ('precompiled_sepolicy', 'precompiled_sepolicy_debug'):
            target = base / name
            if target != output:
                shutil.copy2(output, target)
            if part + '/etc/selinux/' + name not in meta.fs:
                meta.pin('etc/selinux/' + name,
                         'vendor_configs_file' if name.endswith('_debug') else 'sepolicy_file')
            if name.endswith('_debug'):
                meta.ctx[part + '/etc/selinux/' + name] = ['u:object_r:vendor_configs_file:s0']
            for source_part, stem in (('system/system', 'plat'), ('system_ext', 'system_ext'), ('product', 'product')):
                source = root / source_part / 'etc/selinux' / (stem + '_sepolicy_and_mapping.sha256')
                if not source.exists():
                    raise ValueError(f'Missing donor policy digest: {source}')
                shutil.copy2(source, base / (name + '.' + source.name))
                rel = 'etc/selinux/' + name + '.' + source.name
                if part + '/' + rel not in meta.fs:
                    meta.pin(rel, 'vendor_configs_file')
        meta.save()
    return {'mapping': version, 'policy_version': 30,
            'enforcing': enforcing, 'permissive_types': 0 if enforcing else len(types),
            'neverallow_build_checks': False,
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


def finish(root, device, stock, assets, adb=False, compiler='secilc'):
    ace15 = device == 'OnePlusAce3V' and sdk(root) == 35
    ace16 = device == 'OnePlusAce3V' and sdk(root) == 36
    ace_full = ace15 or ace16
    report = {'device': device, 'donor_sdk': sdk(root), 'ace3v_android16': ace16,
              'ace3v_android15': ace15,
              'force_adb': adb and not ace15, 'secure_boot_adb': ace15 or (ace16 and not adb),
              'crypto_log_wrapper': False}
    # The replacement Provision APK must never use donor/ref Android 13 JNI/oat.
    provision = root / 'system_ext/priv-app/Provision'
    if provision.exists():
        for folder in ('oat', 'lib'):
            shutil.rmtree(provision / folder, ignore_errors=True)
    if ace_full:
        report['apex'] = replace_apex(root, stock)
        profile = assets.parent / f'devices/OnePlusAce3V/android-{sdk(root)}'
        name = donor_name(root)
        feature = root / 'product/etc/device_features' / (name + '.xml')
        xml = ET.parse(feature)
        for key, values in (('fpsList', ('120', '90', '60')),
                            ('screen_resolution_supported', ('1240',))):
            for old in list(xml.getroot()):
                if old.get('name') == key:
                    xml.getroot().remove(old)
            array = ET.SubElement(xml.getroot(), 'integer-array', {'name': key})
            for value in values:
                ET.SubElement(array, 'item').text = value
        write(feature, ET.tostring(xml.getroot(), encoding='unicode') + '\n')
        product_meta = Metadata(root, 'product')
        display = root / 'product/etc/displayconfig'
        display.mkdir(parents=True, exist_ok=True)
        for file in display.glob('display_id_*.xml'):
            file.unlink()
        for file in (profile / 'displayconfig').glob('*.xml'):
            shutil.copy2(file, display / file.name)
            product_meta.pin('etc/displayconfig/' + file.name)
        product_meta.save()
        set_props(root / 'product/etc/build.prop', {'ro.miui.region': 'cn',
                  'ro.miui.product.home': 'com.miui.home', 'ro.apex.updatable': 'true',
                  'ro.hardware.fp.fod': 'true', 'ro.hardware.fp.fod.c': 'true'})
        set_props(root / 'vendor/build.prop', properties(profile / 'vendor.build.prop'))
        # Remove inherited OP13 LTPO/physical-resolution policy, absent in booted R3.
        remove = ('persist.vendor.disable_idle_fps.threshold', 'ro.vendor.display.switch_resolution.support',
                  'ro.vendor.mi_sf.enable_tp_idle_automode', 'ro.vendor.mi_sf.enable_automode_for_maxfps_setting',
                  'ro.vendor.mi_sf.supported_automode_maxfps_list', 'ro.vendor.mi_sf.support_automode_for_normalfps',
                  'ro.vendor.mi_sf.support_gradient_idleframerate', 'ro.vendor.mi_sf.set_gradient_idle_timer_ms',
                  'persist.vendor.sys.fp.fod.us.target')
        path = root / 'vendor/build.prop'
        write(path, '\n'.join(line for line in path.read_text('utf-8').splitlines()
                              if line.split('=', 1)[0] not in remove) + '\n')
        odm_values = properties(profile / 'odm.build.prop')
        odm_values.update({'ro.product.odm.device': name, 'ro.product.odm.name': name,
                          'ro.product.name_for_attestation': name, 'ro.product.device_for_attestation': name})
        set_props(root / 'odm/build.prop', odm_values, strip_imports=True)
        shutil.copy2(root / 'odm/build.prop', root / 'odm/etc/build.prop')
        write(root / 'system_ext/etc/init/init.qseelogd.rc', '')
        set_props(root / 'system_ext/etc/build.prop', {'persist.sys.qseelogd': 'false'})
        for rel in ('product/priv-app/XiaomiEUExt',):
            shutil.rmtree(root / rel, ignore_errors=True)
        (root / 'product/overlay/Nothings.Provision.apk').unlink(missing_ok=True)
        # RES supplies only the minimal Provision APK. Remove original native/cache assets.
        payloads = ('system/system/bin/ace3v-skip-setup.sh',
                    'system/system/etc/init/ace3v-port.rc',
                    'system/system/framework/ace3v-hardware.jar') if ace16 else (
                    'system/system/etc/init/ace3v-port.rc',
                    'system/system/framework/ace3v-hardware.jar')
        for rel in payloads:
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(profile / 'files' / rel, dest)
        meta = Metadata(root, 'system')
        if ace16:
            meta.pin('system/bin/ace3v-skip-setup.sh', 'shell_exec', '0755')
        else:
            # Native app_process launcher, running as system (1000) in its own
            # domain. Neither setup provisioning nor the slider uses a root shell.
            launcher = root / 'system/system/bin/ace3v-hardware'
            shutil.copy2(root / 'system/system/bin/app_process64', launcher)
            meta.pin('system/bin/ace3v-hardware', 'ace3v_port_exec', '0755')
            contexts = root / 'system/system/etc/selinux/plat_file_contexts'
            if contexts.exists():
                text = contexts.read_text('utf-8')
                if '/system/bin/ace3v-hardware ' not in text:
                    write(contexts, text.rstrip() +
                          '\n/system/bin/ace3v-hardware -- u:object_r:ace3v_port_exec:s0\n')
            (root / 'system/system/bin/ace3v-skip-setup.sh').unlink(missing_ok=True)
            cil = root / 'vendor/etc/selinux/vendor_sepolicy.cil'
            marker = '\n; Ace 3V Android 15 service policy\n'
            write(cil, cil.read_text('utf-8').split(marker)[0].rstrip() + marker +
                  (profile / 'port.cil').read_text('utf-8'))
        meta.pin('system/etc/init/ace3v-port.rc')
        meta.pin('system/framework/ace3v-hardware.jar')
        meta.save()
        odm_meta = Metadata(root, 'odm')
        for rel in ('build.prop', 'etc/build.prop'):
            key = 'odm/' + rel
            values = list(odm_meta.fs.get(key, ['0', '0', '0644']))
            values[2] = '0644'
            odm_meta.fs[key] = values
            odm_meta.ctx[key] = ['u:object_r:vendor_configs_file:s0' if rel.startswith('etc/')
                                 else 'u:object_r:vendor_file:s0']
        odm_meta.save()
        if ace16:
            report['boringssl_reboot_guards_removed'] = clean_crypto(root)
            report['boringssl_services_with_suppressed_guards'] = sum(
                len(re.findall(r'^service boringssl_self_test\w+ ', (root / relative).read_text('utf-8'), re.M))
                for relative in ('system/system/etc/init/hw/init.rc', 'vendor/etc/init/boringssl_self_test.rc'))
            report['boringssl_tests'] = 'Original binaries retained; reboot guards suppressed for Ace 3V SDK36'
            report['selinux'] = compile_policy(root, compiler)
        else:
            report['boringssl_reboot_guards_removed'] = 0
            report['boringssl_tests'] = 'Original binaries and reboot guards retained'
            report['selinux'] = compile_policy(root, compiler, enforcing=True)
    if ace15:
        harden_a15_init(root)
        secure_adb(root, assets, ace16=True)
    elif adb:
        force_adb(root, assets, ace16=ace16)
    elif ace16:
        secure_adb(root, assets, ace16=True)
    write(root / 'port_compat.json', json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('sdk', 'assemble', 'finish'))
    parser.add_argument('work', type=Path)
    parser.add_argument('--device', default='')
    parser.add_argument('--apex-stock', type=Path)
    parser.add_argument('--force-adb', action='store_true')
    parser.add_argument('--secilc', default='secilc')
    parser.add_argument('--adb-key', type=Path, help='Optional adbkey.pub to authorize boot ADB')
    args = parser.parse_args()
    if args.stage == 'sdk':
        print(sdk(args.work))
    elif args.stage == 'assemble':
        assemble(args.work, args.device == 'OnePlusAce3V' and sdk(args.work) in (35, 36))
    else:
        assets = Path(__file__).resolve().parent.parent / 'fixes'
        report = finish(args.work, args.device, args.apex_stock, assets,
                        args.force_adb, args.secilc)
        if args.adb_key:
            report['authorized_adb_public_key_sha256'] = authorize_adb(args.work, args.adb_key)
            write(args.work / 'port_compat.json', json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
