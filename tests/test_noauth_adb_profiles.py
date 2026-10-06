import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
import port_compat as port


class NoAuthProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'tree'
        self.profiles = Path(self.tmp.name) / 'profiles'
        self.apex = self.root / 'system/system/apex'
        self.apex.mkdir(parents=True)
        self.profiles.mkdir()
        self.write_profile(self.profiles, 'google.apex', b'google donor')
        self.variant = self.profiles / 'variants/nuwa'
        self.variant.mkdir(parents=True)
        self.write_profile(self.variant, 'com.android.adbd.capex', b'nuwa donor')

    def write_profile(self, folder, name, data):
        descriptor = {'donor_file': name, 'donor_sha256': hashlib.sha256(data).hexdigest()}
        (folder / 'profile.json').write_text(json.dumps(descriptor))
        return descriptor

    def test_selects_capex_variant_by_full_hash_without_default_apex(self):
        (self.apex / 'com.android.adbd.capex').write_bytes(b'nuwa donor')
        folder, descriptor = port.select_noauth_adb_profile(self.root, self.profiles)
        self.assertEqual(folder, self.variant)
        self.assertEqual(descriptor['donor_file'], 'com.android.adbd.capex')

    def test_default_profile_remains_supported(self):
        (self.apex / 'google.apex').write_bytes(b'google donor')
        folder, _ = port.select_noauth_adb_profile(self.root, self.profiles)
        self.assertEqual(folder, self.profiles)

    def test_unknown_apex_hash_is_reported_and_not_modified(self):
        apex = self.apex / 'com.android.adbd.capex'
        apex.write_bytes(b'unknown capex')
        with self.assertRaisesRegex(ValueError, 'Observed: com.android.adbd.capex='):
            port.select_noauth_adb_profile(self.root, self.profiles)
        self.assertEqual(apex.read_bytes(), b'unknown capex')

    def test_multiple_matching_profiles_fail(self):
        (self.apex / 'google.apex').write_bytes(b'google donor')
        (self.apex / 'com.android.adbd.capex').write_bytes(b'nuwa donor')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            port.select_noauth_adb_profile(self.root, self.profiles)

    def test_invalid_donor_path_fails(self):
        self.write_profile(self.variant, '../outside.apex', b'bad')
        with self.assertRaisesRegex(ValueError, 'Invalid donor'):
            port.select_noauth_adb_profile(self.root, self.profiles)

    def test_real_hyperos2_variant_asset_matches_descriptor(self):
        profile = REPO / 'devices/OnePlusAce3V/android-35/boot_adb/variants/hyperos2-nuwa-os2.0.219.0'
        descriptor = json.loads((profile / 'profile.json').read_text())
        data = (profile / 'ace3v-adbd').read_bytes()
        offset = int(descriptor['patch_offset'], 0)
        self.assertEqual(hashlib.sha256(data).hexdigest(), descriptor['patched_sha256'])
        self.assertEqual(data[offset:offset + 4].hex(), descriptor['replacement_instruction'])
        original = data[:offset] + bytes.fromhex(descriptor['original_instruction']) + data[offset + 4:]
        self.assertEqual(hashlib.sha256(original).hexdigest(), descriptor['original_sha256'])
        self.assertEqual(descriptor['shell_uid'], 2000)
        self.assertEqual(descriptor['selinux'], 'Enforcing')
