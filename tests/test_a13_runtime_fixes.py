import bz2
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
import a13_runtime_fixes as runtime
import port_compat as port


def integer(value):
    return ((abs(value) | (1 << 63 if value < 0 else 0))).to_bytes(8, 'little')


def delta(ctrl, diff, extra, size):
    c = bz2.compress(b''.join(integer(x) for row in ctrl for x in row))
    d = bz2.compress(diff)
    return b'BSDIFF40' + integer(len(c)) + integer(len(d)) + integer(size) + c + d + bz2.compress(extra)


class DeltaTests(unittest.TestCase):
    def test_copy_diff_insert_seek(self):
        patch_data = delta([(3, 1, -3), (3, 0, 0)], bytes([0, 1, 0, 0, 0, 0]), b'!', 7)
        self.assertEqual(runtime.apply_delta(b'abc', patch_data, 7), b'acc!abc')
        seek_only = delta([(0, 0, 1), (2, 0, 0)], bytes(2), b'', 2)
        self.assertEqual(runtime.apply_delta(b'abc', seek_only, 2), b'bc')

    def test_reject_invalid_header_bounds_truncation_and_extra_bytes(self):
        valid = delta([(3, 0, 0)], bytes(3), b'', 3)
        for data, size in ((b'bad', 3), (valid, 4), (valid[:-1], 3),
                           (valid + b'x', 3), (delta([(-1, 0, 0)], b'', b'', 3), 3),
                           (delta([(4, 0, 0)], bytes(4), b'', 3), 3)):
            with self.subTest(data=data[:32], size=size), self.assertRaises((ValueError, OSError, EOFError)):
                runtime.apply_delta(b'abc', data, size)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'tree'
        self.profile = Path(self.temp.name) / 'profile'
        self.profile.mkdir()
        self.put('system/system/build.prop', b'ro.build.version.sdk=33\n')
        self.put('vendor/etc/selinux/plat_sepolicy_vers.txt', b'34.0\n')
        self.put('system/system/lib64/libbinder.so', b'exact Binder ABI')
        self.put('system/system/lib64/libsensorservice.so', b'abc')
        module = b'unchanged embedded signed module (fixture)'
        apk = self.root / 'product/priv-app/GmsCore/GmsCore.apk'
        apk.parent.mkdir(parents=True)
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('assets/chimera-modules/Test.apk', module)
        patch_data = delta([(3, 0, 0)], bytes([0, 1, 0]), b'', 3)
        (self.profile / 'sensor.bsdiff').write_bytes(patch_data)
        self.descriptor = {'sdk': 33, 'vendor_mapping': '34.0',
            'binder_abi_guards': {'libbinder.so': runtime.sha(b'exact Binder ABI')},
            'patches': [{'path': 'system/system/lib64/libsensorservice.so',
                         'before_sha256': runtime.sha(b'abc'), 'after_sha256': runtime.sha(b'acc'),
                         'after_bytes': 3, 'patch': 'sensor.bsdiff', 'patch_sha256': runtime.sha(patch_data)}],
            'files': [], 'static_files': [],
            'gms': {'GmsCore_sha256': runtime.sha(apk.read_bytes()),
                    'factory_modules': [{'apk_entry': 'assets/chimera-modules/Test.apk',
                                         'name': 'Test.apk', 'sha256': runtime.sha(module), 'bytes': len(module)}]}}
        self.save()

    def put(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def save(self):
        (self.profile / 'profile.json').write_text(json.dumps(self.descriptor))

    def snapshot(self):
        return {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def run_preflight(self):
        with patch('adbd_patch.derive_from_donor', return_value=(b'daemon', {})):
            return runtime.preflight(self.root, self.profile, port)

    def test_plans_exact_patch_and_own_google_module_without_writing(self):
        before = self.snapshot()
        _, planned = self.run_preflight()
        self.assertEqual(planned[0], ('system/system/lib64/libsensorservice.so', b'acc'))
        self.assertTrue(planned[1][0].endswith('/m/container/Test.apk'))
        self.assertEqual(before, self.snapshot())
        self.put('system/system/lib64/libsensorservice.so', b'acc')
        self.assertEqual(self.run_preflight()[1][0][1], b'acc')

    def test_unknown_donor_wrong_sdk_or_base_fails_without_mutation(self):
        for path, bad in [('system/system/lib64/libsensorservice.so', b'unknown'),
                          ('system/system/lib64/libbinder.so', b'wrong ABI'),
                          ('vendor/etc/selinux/plat_sepolicy_vers.txt', b'35.0'),
                          ('system/system/build.prop', b'ro.build.version.sdk=34\n')]:
            original = (self.root / path).read_bytes()
            self.put(path, bad)
            before = self.snapshot()
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.run_preflight()
            self.assertEqual(before, self.snapshot())
            self.put(path, original)

    def test_tampered_patch_or_google_module_fails_without_mutation(self):
        for target in ('patch_sha256', 'module'):
            if target == 'patch_sha256':
                self.descriptor['patches'][0]['patch_sha256'] = '0' * 64
            else:
                self.descriptor['gms']['factory_modules'][0]['sha256'] = '0' * 64
            self.save()
            before = self.snapshot()
            with self.assertRaises(ValueError):
                self.run_preflight()
            self.assertEqual(before, self.snapshot())
            self.descriptor['patches'][0]['patch_sha256'] = runtime.sha((self.profile / 'sensor.bsdiff').read_bytes())


class FlowTests(unittest.TestCase):
    def test_repeated_vendor_recipe_keeps_one_route_and_rejects_conflicting_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'vendor/etc/selinux/vendor_property_contexts'
            first = 'ro.vendor.test. u:object_r:test_prop:s0\n'
            port.write(path, first + '# duplicate recipe\nro.vendor.test.  u:object_r:test_prop:s0 prefix\n')
            self.assertEqual(runtime.deduplicate_vendor_property_contexts(root, port), ['ro.vendor.test.'])
            self.assertEqual(path.read_text(), first + '# duplicate recipe\n')
            self.assertEqual(runtime.deduplicate_vendor_property_contexts(root, port), [])
            for conflict in ('u:object_r:other_prop:s0', 'u:object_r:test_prop:s0 prefix bool'):
                port.write(path, first + 'ro.vendor.test. ' + conflict + '\n')
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'Conflicting'):
                    runtime.deduplicate_vendor_property_contexts(root, port)
                self.assertEqual(path.read_bytes(), before)

    def test_sdk33_dispatch_does_not_request_newer_stock_apex(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            port.write(root / 'system/system/build.prop', 'ro.build.version.sdk=33\n')
            with patch.object(runtime, 'finish', return_value={'sdk33': True}) as finish:
                result = port.finish(root, 'OnePlusAce3V', None, REPO / 'fixes')
            self.assertEqual(result, {'sdk33': True})
            self.assertIs(finish.call_args.args[3], port)

    def test_verifier_normal_boot_command_order_preserved(self):
        header = 'on zygote-start && property:ro.crypto.state=unencrypted'
        original = header + '\n    before\n    exec_start update_verifier_nonencrypted\n    start zygote\n'
        patched = runtime.guard_dsu_verifier(original)
        commands = [s.strip() for s in patched.splitlines() if s.startswith('    ')]
        self.assertEqual(commands, ['before', 'exec_start update_verifier_nonencrypted', 'start zygote'])
        self.assertIn(header + ' && property:sys.ace3v.run_update_verifier=1', patched)
        self.assertEqual(runtime.guard_dsu_verifier(patched), patched)
        rc = (REPO / 'devices/OnePlusAce3V/android-33/files/system/system/etc/init/miui14-dsu-compat.rc').read_text()
        self.assertIn('property:ro.gsid.image_running=1', rc)

    def test_linker_pb_keeps_existing_fields_and_is_idempotent(self):
        original = b'\x0a\x03foo\x10\x01'
        result = runtime.add_linker_library(original, 3)
        self.assertTrue(result.startswith(original))
        self.assertEqual(runtime.add_linker_library(result, 3), result)
        with self.assertRaises(ValueError):
            runtime.add_linker_library(b'\x0a\xff', 3)

    def test_vintf_keeps_target_level_and_hal_data(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            xml = '<manifest version="7.0" type="device" target-level="8"><hal format="aidl"><name>test</name><version>1</version><fqname>ITest/default</fqname></hal></manifest>'
            path = root / 'vendor/etc/vintf/manifest_pineapple.xml'
            port.write(path, xml)
            runtime.vintf_compat(root, port)
            self.assertEqual(path.read_text(), xml.replace('version="7.0"', 'version="5.0"'))
            self.assertEqual(runtime.vintf_compat(root, port), [])

    def test_shipped_assets_and_static_hashes(self):
        profile = REPO / 'devices/OnePlusAce3V/android-33'
        descriptor = json.loads((profile / 'profile.json').read_text())
        for entry in descriptor['patches']:
            self.assertEqual(runtime.sha((profile / entry['patch']).read_bytes()), entry['patch_sha256'])
        for entry in descriptor['static_files']:
            self.assertEqual(runtime.sha((profile / entry['asset']).read_bytes()), entry['sha256'])
        for entry in descriptor['files']:
            self.assertEqual(runtime.sha((profile / entry['asset']).read_bytes()), entry['asset_sha256'])
        self.assertFalse(list(profile.rglob('*.pk8')))
        self.assertFalse(list(profile.rglob('*key.pem')))


if __name__ == '__main__':
    unittest.main()
