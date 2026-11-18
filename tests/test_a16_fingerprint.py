import bz2
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))

import a16_fingerprint as fingerprint
import port_compat as port


def replace_delta(data):
    integer = lambda n: n.to_bytes(8, 'little')
    control = bz2.compress(integer(0) + integer(len(data)) + integer(0))
    differences = bz2.compress(b'')
    return (b'BSDIFF40' + integer(len(control)) + integer(len(differences))
            + integer(len(data)) + control + differences + bz2.compress(data))


class Ace3vFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        directory = Path(self.tmp.name)
        self.root = directory / 'work'
        self.profile = directory / 'profile'
        self.profile.mkdir()
        self.product = self.root / 'product/etc/build.prop'
        self.vendor = self.root / 'vendor/build.prop'
        port.set_props(self.product, {'ro.mi.os.version.code': '3',
                       'ro.mi.os.version.incremental': 'OS3.0.310.0.WMCCNXM | build-tag'})
        port.write(self.vendor, 'persist.vendor.sys.fp.fod.location.X_Y=620,2414\n')
        port.write(self.root / fingerprint.COMPOSER, 'known composer')
        shipped = ROOT / 'devices/OnePlusAce3V/android-36/fingerprint/profile.json'
        self.descriptor = json.loads(shipped.read_text())
        self.descriptor['vendor_composer_sha256'] = fingerprint.sha(b'known composer')
        for index, entry in enumerate(self.descriptor['patches']):
            old = f'original {index}'
            new = f'patched {index}'
            port.write(self.root / entry['path'], old)
            delta = replace_delta(new.encode())
            (self.profile / entry['patch']).write_bytes(delta)
            entry.update(before_sha256=fingerprint.sha(old.encode()),
                         after_sha256=fingerprint.sha(new.encode()),
                         patch_sha256=fingerprint.sha(delta), after_bytes=len(new))
        port.write(self.profile / 'profile.json', json.dumps(self.descriptor))

    def install(self):
        return fingerprint.install(self.root, self.profile, port)

    def assert_original_targets(self):
        for index, entry in enumerate(self.descriptor['patches']):
            self.assertEqual((self.root / entry['path']).read_text(), f'original {index}')
        self.assertEqual(port.properties(self.vendor)['persist.vendor.sys.fp.fod.location.X_Y'], '620,2414')

    def test_replaces_verified_components_and_geometry_once(self):
        report = self.install()
        self.assertEqual(report['status'], 'applied')
        self.assertEqual(len(report['patched_files']), 3)
        for index, entry in enumerate(self.descriptor['patches']):
            self.assertEqual((self.root / entry['path']).read_text(), f'patched {index}')
        for key, value in self.descriptor['geometry'].items():
            self.assertEqual(port.properties(self.vendor)[key], value)
        self.assertEqual(self.install()['status'], 'already_applied')

    def test_another_rom_region_skips_bridge(self):
        port.set_props(self.product, {'ro.mi.os.version.incremental': 'OS3.0.310.0.WMCMIXM'})
        self.assertEqual(self.install()['status'], 'skipped')
        self.assert_original_targets()

    def test_unknown_composer_skips_entire_bridge(self):
        port.write(self.root / fingerprint.COMPOSER, 'unknown composer')
        report = self.install()
        self.assertEqual(report['status'], 'skipped')
        self.assertIn(fingerprint.COMPOSER, report['mismatched'])
        self.assert_original_targets()

    def test_invalid_last_delta_fails_before_any_change(self):
        (self.profile / self.descriptor['patches'][-1]['patch']).write_bytes(b'invalid')
        with self.assertRaisesRegex(ValueError, 'Corrupt Ace 3V fingerprint delta'):
            self.install()
        self.assert_original_targets()
