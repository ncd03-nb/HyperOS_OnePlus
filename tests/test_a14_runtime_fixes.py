import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
import a14_runtime_fixes as runtime
import port_compat as port
from test_port_compat import fixture


class VoNRTests(unittest.TestCase):
    def setUp(self):
        self.original = (REPO / 'tests/fixtures/vonr.dex').read_bytes()

    def test_rpc_patch_preserves_every_byte_outside_call_and_dex_checksums(self):
        patched, detail = runtime.patch_vonr_dex(self.original)
        offset = detail['code_offset']
        self.assertEqual(len(patched), len(self.original))
        self.assertEqual(patched[:8], self.original[:8])
        self.assertEqual(patched[32:offset], self.original[32:offset])
        self.assertEqual(patched[offset + 8:], self.original[offset + 8:])
        self.assertEqual(patched[offset:offset + 8], bytes.fromhex('1200000000000000'))
        types, methods = runtime._dex_tables(patched)
        code = runtime._method_code(patched, types, methods,
                                   (b'Lmiui/telephony/TelephonyManagerEx;', b'isVoNREnabled', b'(I)Z'))
        self.assertGreater(struct.unpack_from('<H', patched, code + 6)[0], 0)
        again, status = runtime.patch_vonr_dex(patched)
        self.assertTrue(status['already_patched'])
        self.assertEqual(again, patched)

    def test_rejects_corrupted_dex(self):
        corrupted = bytearray(self.original)
        corrupted[-1] ^= 1
        with self.assertRaisesRegex(ValueError, 'signature'):
            runtime.patch_vonr_dex(corrupted)
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            runtime.patch_vonr_dex(b'dex\n039\0')

    def test_jar_preserves_other_entries_and_comment(self):
        with tempfile.TemporaryDirectory() as directory:
            jar = Path(directory) / 'miui-framework.jar'
            with zipfile.ZipFile(jar, 'w') as archive:
                archive.comment = b'original jar comment'
                archive.writestr('classes.dex', self.original)
                archive.writestr('META-INF/MANIFEST.MF', b'Manifest-Version: 1.0\n')
            first = runtime.disable_xiaomi_vonr_query(jar)
            with zipfile.ZipFile(jar) as archive:
                self.assertEqual(archive.comment, b'original jar comment')
                self.assertEqual(archive.read('META-INF/MANIFEST.MF'), b'Manifest-Version: 1.0\n')
            second = runtime.disable_xiaomi_vonr_query(jar)
            self.assertTrue(second['already_patched'])
            self.assertEqual(first['sha256'], second['sha256'])


class GoogleBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'tree'
        fixture(self.root, 34)
        self.stock = self.base / 'stock'
        self.profile = self.base / 'profile'
        self.profile.mkdir()
        entries = []
        for index, (source, target) in enumerate((
                ('my_bigball/priv-app/GmsCore/GmsCore.apk', 'product/priv-app/GmsCore/GmsCore.apk'),
                ('my_bigball/priv-app/GmsCore/m/independent/AndroidPlatformServices.apk',
                 'product/priv-app/GmsCore/m/independent/AndroidPlatformServices.apk'),
                ('my_bigball/non_overlay/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk',
                 'system_ext/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk'))):
            data = ('verified bundle ' + str(index)).encode()
            path = self.stock / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            entries.append({'source': source, 'target': target,
                            'sha256': hashlib.sha256(data).hexdigest()})
        descriptor = {'sdk': 34, 'stock': 'test stock', 'gms_version': 'test GMS',
                      'gsf_version': 'test GSF', 'files': entries}
        (self.profile / 'google_stock.json').write_text(json.dumps(descriptor))
        self.entries = entries
        for relative in ('product/priv-app/GmsCore/GmsCore.apk',
                         'product/priv-app/GmsCore/oat/arm64/GmsCore.vdex',
                         'product/priv-app/AndroidPlatformServices/AndroidPlatformServices.apk',
                         'system_ext/priv-app/GoogleServicesFramework/oat/arm64/GoogleServicesFramework.odex'):
            port.write(self.root / relative, 'old')
            part, name = relative.split('/', 1)
            meta = port.Metadata(self.root, part)
            meta.pin(name)
            meta.save()

    def test_coherent_bundle_removes_old_cache_and_pins_metadata(self):
        report = port.install_a14_stock_gms(self.root, self.stock, self.profile)
        self.assertTrue(report['installed'])
        for entry in self.entries:
            target = self.root / entry['target']
            self.assertEqual(target.read_bytes(), (self.stock / entry['source']).read_bytes())
            meta = port.Metadata(self.root, entry['target'].split('/')[0])
            self.assertEqual(meta.fs[entry['target']], ['0', '0', '0644'])
            self.assertEqual(meta.ctx[entry['target']], ['u:object_r:system_file:s0'])
        self.assertFalse((self.root / 'product/priv-app/AndroidPlatformServices').exists())
        self.assertFalse(any('oat' in str(p) for p in (self.root / 'product/priv-app/GmsCore').rglob('*')))
        self.assertFalse((self.root / 'system_ext/priv-app/GoogleServicesFramework/oat').exists())
        port.install_a14_stock_gms(self.root, self.stock, self.profile)
        self.assertFalse((self.root / 'product/priv-app/AndroidPlatformServices').exists())

    def test_tampered_bundle_fails_before_any_target_changes(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        (self.stock / self.entries[-1]['source']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'No verified'):
            port.install_a14_stock_gms(self.root, self.stock, self.profile)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_sdk_guard(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '35'})
        with self.assertRaisesRegex(ValueError, 'requires SDK34'):
            port.install_a14_stock_gms(self.root, self.stock, self.profile)

    def add_embedded_module(self):
        entry = self.entries[0]
        apk = self.stock / entry['source']
        member = 'assets/chimera-modules/Test.uncompressed.apk'
        data = b'original embedded module bytes'
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr(member, data)
        entry['sha256'] = hashlib.sha256(apk.read_bytes()).hexdigest()
        module = {'source': entry['source'], 'apk_entry': member,
                  'target': 'product/priv-app/GmsCore/m/container/Test.uncompressed.apk',
                  'sha256': hashlib.sha256(data).hexdigest()}
        self.entries.append(module)
        descriptor = json.loads((self.profile / 'google_stock.json').read_text())
        descriptor['files'] = self.entries
        (self.profile / 'google_stock.json').write_text(json.dumps(descriptor))
        return apk, module, data

    def test_factory_modules_keep_the_container_apk_intact_and_are_idempotent(self):
        apk, module, data = self.add_embedded_module()
        original = apk.read_bytes()
        report = port.install_a14_stock_gms(self.root, self.stock, self.profile)
        self.assertEqual(report['preinstalled_chimera_modules'], 1)
        self.assertEqual((self.root / self.entries[0]['target']).read_bytes(), original)
        self.assertEqual((self.root / module['target']).read_bytes(), data)
        meta = port.Metadata(self.root, 'product')
        self.assertEqual(meta.fs[module['target']], ['0', '0', '0644'])
        self.assertEqual(meta.ctx[module['target']], ['u:object_r:system_file:s0'])
        parent = 'product/priv-app/GmsCore/m/container'
        self.assertEqual(meta.fs[parent], ['0', '0', '0755'])
        port.install_a14_stock_gms(self.root, self.stock, self.profile)
        self.assertEqual((self.root / module['target']).read_bytes(), data)

    def test_incorrect_embedded_module_hash_fails_without_mutating_tree(self):
        self.add_embedded_module()
        descriptor = json.loads((self.profile / 'google_stock.json').read_text())
        descriptor['files'][-1]['sha256'] = '0' * 64
        (self.profile / 'google_stock.json').write_text(json.dumps(descriptor))
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'No verified'):
            port.install_a14_stock_gms(self.root, self.stock, self.profile)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
