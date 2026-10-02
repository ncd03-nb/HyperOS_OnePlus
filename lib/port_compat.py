#!/usr/bin/env python3
"""Versioned assembly and compatibility fixes shared by CLI and Actions."""
import argparse
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
    for rel in ('mi_ext/etc/build.prop', 'system/mi_ext/etc/build.prop',
                'product/etc/build.prop', 'system/system/build.prop'):
        props = properties(root / rel)
        value = props.get('ro.product.mod_device', '').removesuffix('_global')
        if value and re.fullmatch(r'[A-Za-z0-9_-]+', value):
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
        raise ValueError('Ace 3V Android 16 requires Android 16 stock system_ext APEX; '
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
    shutil.copy2(assets / 'hyperos_force_adb.rc', forced)
    if ace16 and (root / 'config/system_fs_config').exists():
        meta = Metadata(root, 'system')
        meta.pin('system/etc/init/hyperos_force_adb.rc')
        meta.save()


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


def compile_policy(root, compiler='secilc'):
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
    types = set(re.findall(r'\(type\s+([^\s()]+)\)',
                           '\n'.join(path.read_text('utf-8') for path in inputs)))
    policy = vendor / 'vendor_sepolicy.cil'
    text = policy.read_text('utf-8')
    existing = set(re.findall(r'\(typepermissive\s+([^\s()]+)\)', text))
    write(policy, text.rstrip() + '\n; Ace 3V development port: permissive\n' +
          ''.join(f'(typepermissive {name})\n' for name in sorted(types - existing)))
    output = root / 'odm/etc/selinux/precompiled_sepolicy'
    subprocess.run([compiler, '-m', '-M', 'true', '-G', '-N', '-c', '30',
                    '-o', str(output), '-f', os.devnull, *map(str, inputs)], check=True)
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
            for part, stem in (('system/system', 'plat'), ('system_ext', 'system_ext'), ('product', 'product')):
                source = root / part / 'etc/selinux' / (stem + '_sepolicy_and_mapping.sha256')
                if not source.exists():
                    raise ValueError(f'Missing donor policy digest: {source}')
                shutil.copy2(source, base / (name + '.' + source.name))
                rel = 'etc/selinux/' + name + '.' + source.name
                if part + '/' + rel not in meta.fs:
                    meta.pin(rel, 'vendor_configs_file')
        meta.save()
    return {'mapping': version, 'policy_version': 30, 'permissive_types': len(types),
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


def finish(root, device, stock, assets, adb=False, compiler='secilc'):
    ace16 = device == 'OnePlusAce3V' and sdk(root) == 36
    report = {'device': device, 'donor_sdk': sdk(root), 'ace3v_android16': ace16,
              'force_adb': adb, 'secure_boot_adb': ace16 and not adb,
              'crypto_log_wrapper': False}
    # The replacement Provision APK must never use donor/ref Android 13 JNI/oat.
    provision = root / 'system_ext/priv-app/Provision'
    if provision.exists():
        for folder in ('oat', 'lib'):
            shutil.rmtree(provision / folder, ignore_errors=True)
    if ace16:
        report['apex'] = replace_apex(root, stock)
        profile = assets.parent / 'devices/OnePlusAce3V/android-36'
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
        for rel in ('system/system/bin/ace3v-skip-setup.sh',
                    'system/system/etc/init/ace3v-port.rc',
                    'system/system/framework/ace3v-hardware.jar'):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(profile / 'files' / rel, dest)
        meta = Metadata(root, 'system')
        meta.pin('system/bin/ace3v-skip-setup.sh', 'shell_exec', '0755')
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
        report['boringssl_reboot_guards_removed'] = clean_crypto(root)
        report['boringssl_services_with_suppressed_guards'] = sum(
            len(re.findall(r'^service boringssl_self_test\w+ ', (root / relative).read_text('utf-8'), re.M))
            for relative in ('system/system/etc/init/hw/init.rc', 'vendor/etc/init/boringssl_self_test.rc'))
        report['boringssl_tests'] = 'Original binaries retained; reboot guards suppressed for Ace 3V SDK36'
        report['selinux'] = compile_policy(root, compiler)
    if adb:
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
    args = parser.parse_args()
    if args.stage == 'sdk':
        print(sdk(args.work))
    elif args.stage == 'assemble':
        assemble(args.work, args.device == 'OnePlusAce3V' and sdk(args.work) == 36)
    else:
        assets = Path(__file__).resolve().parent.parent / 'fixes'
        print(json.dumps(finish(args.work, args.device, args.apex_stock, assets,
                               args.force_adb, args.secilc), indent=2))


if __name__ == '__main__':
    main()
