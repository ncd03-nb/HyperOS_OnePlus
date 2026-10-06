import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
import port_compat as port


class A15FactoryGmsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'tree'
        self.profile = Path(self.tmp.name) / 'profile'
        self.profile.mkdir()
        port.write(self.root / 'system/system/build.prop', 'ro.build.version.sdk=35\n')
        port.write(self.root / 'config/product_fs_config', 'product 0 0 0755\n')
        port.write(self.root / 'config/product_file_contexts', '/product u:object_r:system_file:s0\n')
        self.apk = self.root / 'product/priv-app/GmsCore/GmsCore.apk'
        self.apk.parent.mkdir(parents=True)
        self.payloads = {'DynamiteModulesA.apk': b'signed module A', 'DynamiteLoader.apk': b'signed loader'}
        with zipfile.ZipFile(self.apk, 'w') as archive:
            archive.writestr('classes.dex', b'container')
            for name, payload in self.payloads.items():
                archive.writestr('assets/chimera-modules/' + name, payload)
        self.descriptor = {'sdk': 35, 'gms_version': 'fixture',
            'container_sha256': hashlib.sha256(self.apk.read_bytes()).hexdigest(),
            'modules': [{'apk_entry': 'assets/chimera-modules/' + name,
                         'sha256': hashlib.sha256(payload).hexdigest()} for name, payload in self.payloads.items()]}
        self.save_profile()

    def save_profile(self):
        port.write(self.profile / 'google_modules.json', json.dumps(self.descriptor))

    def test_stages_own_module_bytes_preserves_container_and_independent_modules(self):
        original = self.apk.read_bytes()
        independent = self.apk.parent / 'm/independent/AndroidPlatformServices.apk'
        port.write(independent, 'independent')
        directory = self.apk.parent / 'm/container'
        port.write(directory / 'obsolete.apk', 'old')
        for _ in range(2):
            result = port.install_a15_gms_factory_modules(self.root, self.profile)
        self.assertTrue(result['installed'])
        self.assertEqual(self.apk.read_bytes(), original)
        self.assertEqual(independent.read_text(), 'independent')
        self.assertEqual({path.name: path.read_bytes() for path in directory.iterdir()}, self.payloads)
        meta = port.Metadata(self.root, 'product')
        self.assertEqual(meta.fs['product/priv-app/GmsCore/m/container'], ['0', '0', '0755'])
        for name in self.payloads:
            key = 'product/priv-app/GmsCore/m/container/' + name
            self.assertEqual(meta.fs[key], ['0', '0', '0644'])
            self.assertEqual(meta.ctx[key], ['u:object_r:system_file:s0'])

    def test_unknown_container_is_untouched(self):
        self.apk.write_bytes(b'unknown donor')
        self.assertFalse(port.install_a15_gms_factory_modules(self.root, self.profile)['installed'])
        self.assertEqual(self.apk.read_bytes(), b'unknown donor')
        self.assertFalse((self.apk.parent / 'm').exists())

    def test_corrupt_module_hash_fails_before_tree_changes(self):
        self.descriptor['modules'][1]['sha256'] = '0' * 64
        self.save_profile()
        with self.assertRaisesRegex(ValueError, 'module hash mismatch'):
            port.install_a15_gms_factory_modules(self.root, self.profile)
        self.assertFalse((self.apk.parent / 'm').exists())

    def test_incomplete_profile_is_rejected(self):
        self.descriptor['modules'].pop()
        self.save_profile()
        with self.assertRaisesRegex(ValueError, 'module list mismatch'):
            port.install_a15_gms_factory_modules(self.root, self.profile)
        self.assertFalse((self.apk.parent / 'm').exists())

    def test_other_sdk_is_rejected(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '34'})
        with self.assertRaisesRegex(ValueError, 'restricted to SDK35'):
            port.install_a15_gms_factory_modules(self.root, self.profile)


if __name__ == '__main__':
    unittest.main()
