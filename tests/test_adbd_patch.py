import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
from adbd_patch import patch_auth, derive_from_donor
import port_compat as port


def original_asset(profile):
    descriptor = json.loads((profile / 'profile.json').read_text())
    data = bytearray((profile / 'ace3v-adbd').read_bytes())
    offset = int(descriptor['patch_offset'], 0)
    data[offset:offset + 4] = bytes.fromhex(descriptor['original_instruction'])
    assert hashlib.sha256(data).hexdigest() == descriptor['original_sha256']
    return bytes(data)


class DonorAdbdTests(unittest.TestCase):
    def test_real_android14_15_assets_only_change_auth_storage(self):
        # Independent offsets from objdump, including the SDK34 GOT/RELR case.
        cases = [('android-34/boot_adb', 0xa2210, 0x1e747c),
                 ('android-35/boot_adb', 0xa8d8c, 0x1ec198),
                 ('android-35/boot_adb/variants/hyperos2-nuwa-os2.0.219.0', 0xa2264, 0x1c1180)]
        for relative, store, initializer in cases:
            with self.subTest(relative=relative):
                original = original_asset(REPO / 'devices/OnePlusAce3V' / relative)
                patched, report = patch_auth(original)
                expected = bytearray(original)
                expected[initializer] = 0
                struct.pack_into('<I', expected, store, struct.unpack_from('<I', original, store)[0] | 31)
                self.assertEqual(patched, bytes(expected))
                self.assertEqual(patch_auth(patched)[0], patched)
                self.assertTrue(report['uid_drop_unchanged'])
                # A true result is reserved for an actual device boot.
                self.assertFalse(report['runtime_boot_verified'])

    def test_unsupported_elf_fails_before_modification(self):
        original = original_asset(REPO / 'devices/OnePlusAce3V/android-35/boot_adb')
        for data in (b'not ELF', original[:100],
                     original.replace(b'auth_required\0', b'no_such_auth_x\0'),
                     original[:18] + b'\x3e\x00' + original[20:]):
            with self.subTest(size=len(data)), self.assertRaises((ValueError, struct.error)):
                patch_auth(data)

    def test_flattened_regional_apex(self):
        original = original_asset(REPO / 'devices/OnePlusAce3V/android-35/boot_adb')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            daemon = root / 'system/system/apex/com.google.android.adbd/bin/adbd'
            daemon.parent.mkdir(parents=True)
            daemon.write_bytes(original)
            patched, report = derive_from_donor(root)
            self.assertEqual(patched, patch_auth(original)[0])
            self.assertTrue(report['flattened_apex'])
            self.assertEqual(daemon.read_bytes(), original)

    @unittest.skipUnless(os.name != 'nt' and shutil.which('mkfs.ext4') and shutil.which('debugfs'),
                         'Linux e2fsprogs required')
    def test_region_and_apex_packaging_changes_do_not_need_profile(self):
        original = original_asset(REPO / 'devices/OnePlusAce3V/android-35/boot_adb')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'fs/bin').mkdir(parents=True)
            (root / 'fs/bin/adbd').write_bytes(original)
            payload = root / 'payload.img'
            subprocess.run(['mkfs.ext4', '-q', '-F', '-d', str(root / 'fs'), str(payload), '8192'],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, 'w') as z:
                z.writestr('apex_payload.img', payload.read_bytes())
            apex_dir = root / 'system/system/apex'
            apex_dir.mkdir(parents=True)
            hashes = set()
            for name, compressed in [('com.android.adbd.apex', False),
                                     ('com.android.adbd.capex', True),
                                     ('com.google.android.adbd_compressed.apex', True)]:
                package = apex_dir / name
                if compressed:
                    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as z:
                        z.writestr('original_apex', inner.getvalue())
                        z.comment = name.encode()
                else:
                    package.write_bytes(inner.getvalue())
                before = package.read_bytes()
                patched, report = derive_from_donor(root)
                hashes.add(report['donor_sha256'])
                self.assertEqual(patched, patch_auth(original)[0])
                self.assertEqual(package.read_bytes(), before)
                self.assertEqual(report['profile'], 'derived-from-donor')
                package.unlink()
            self.assertEqual(len(hashes), 3)

    @unittest.skipUnless(os.name != 'nt' and shutil.which('fsck.erofs'), 'Linux erofs-utils required')
    def test_erofs_payload_extracts_donor_daemon(self):
        original = original_asset(REPO / 'devices/OnePlusAce3V/android-35/boot_adb')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'fs/bin').mkdir(parents=True)
            (root / 'fs/bin/adbd').write_bytes(original)
            payload = root / 'payload.img'
            subprocess.run([str(REPO / 'bin/Linux/x86_64/mkfs.erofs'), str(payload), str(root / 'fs')],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            package = root / 'system/system/apex/com.android.adbd.apex'
            package.parent.mkdir(parents=True)
            with zipfile.ZipFile(package, 'w') as z:
                z.writestr('apex_payload.img', payload.read_bytes())
            self.assertEqual(derive_from_donor(root)[0], patch_auth(original)[0])


class DisplayConfigTests(unittest.TestCase):
    def test_all_versioned_profiles_cover_region_dependent_minimum(self):
        with tempfile.TemporaryDirectory() as tmp:
            port.write(Path(tmp) / 'config/product_fs_config', 'product 0 0 0755\n')
            port.write(Path(tmp) / 'config/product_file_contexts', '/product u:object_r:system_file:s0\n')
            for sdk in (34, 35, 36):
                profile = REPO / f'devices/OnePlusAce3V/android-{sdk}'
                report = port.install_display_config(Path(tmp), profile)
                self.assertEqual(report[0]['range'], [0, 1])
                self.assertEqual(report[0]['points'], 5)

    def test_previous_bootloop_map_is_rejected_before_replacing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / 'product/etc/displayconfig/display_id_123.xml'
            port.write(existing, 'keep until validation succeeds')
            profile = root / 'profile/displayconfig/display_id_123.xml'
            for mapping in ('<point><value>0.000854597</value><nits>2</nits></point>'
                            '<point><value>1</value><nits>2150</nits></point>',
                            '<point><value>0</value><nits>nan</nits></point>'
                            '<point><value>1</value><nits>2150</nits></point>',
                            '<point><value>0</value><nits>2</nits></point>'
                            '<point><value>1</value><nits>2</nits></point>'):
                port.write(profile, '<displayConfiguration><screenBrightnessMap>' + mapping +
                           '</screenBrightnessMap></displayConfiguration>')
                with self.assertRaisesRegex(ValueError, 'Invalid brightness'):
                    port.install_display_config(root, root / 'profile')
                self.assertEqual(existing.read_text(), 'keep until validation succeeds')


if __name__ == '__main__':
    unittest.main()
