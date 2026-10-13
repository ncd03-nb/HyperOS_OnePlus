from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import port_compat as port


class PropertyContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cil = self.root / 'vendor/etc/selinux/vendor_sepolicy.cil'
        self.contexts = self.root / 'vendor/etc/selinux/vendor_property_contexts'
        self.existing = ('persist.vendor. u:object_r:vendor_default_prop:s0 prefix\n'
                         'persist.vendor.fp.native u:object_r:vendor_fp_prop:s0 exact\n')
        self.patch = ''.join(prefix + ' u:object_r:vendor_finerprint_optical_rawdata:s0\n'
                             for prefix in ('persist.vendor.fingerprint.', 'persist.vendor.fp.',
                                            'persist.vendor.side.fp.', 'vendor.oplus.fingerprint.'))
        port.write(self.cil, '(type vendor_fp_prop)\n')
        port.write(self.contexts, self.existing + self.patch)

    def test_a14_removes_undefined_overrides_preserving_native_routes(self):
        removed = port.reconcile_fingerprint_property_contexts(self.root)
        self.assertEqual(len(removed), 4)
        self.assertEqual(self.contexts.read_text(), self.existing)
        self.assertEqual(port.reconcile_fingerprint_property_contexts(self.root), [])

    def test_declared_vendor_type_retains_newer_vendor_mappings(self):
        port.write(self.cil, self.cil.read_text() + '(type vendor_finerprint_optical_rawdata)\n')
        self.assertEqual(port.reconcile_fingerprint_property_contexts(self.root), [])
        self.assertEqual(self.contexts.read_text(), self.existing + self.patch)

    def test_unrelated_unknown_label_fails_instead_of_being_rewritten(self):
        port.reconcile_fingerprint_property_contexts(self.root)
        port.write(self.contexts, self.contexts.read_text() +
                   'vendor.other. u:object_r:missing_type:s0 prefix\n')
        result = subprocess.CompletedProcess([], 0, stdout='Types: 2\n vendor_default_prop\n vendor_fp_prop\n')
        with patch.object(port.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(ValueError, 'vendor_property_contexts:3.*missing_type'):
                port.validate_property_contexts(self.root, Path('final.policy'))
        self.assertIn('missing_type', self.contexts.read_text())

    def test_final_policy_validates_all_partition_contexts(self):
        port.reconcile_fingerprint_property_contexts(self.root)
        port.write(self.root / 'system/system/etc/selinux/plat_property_contexts',
                   '# platform\nro.test u:object_r:exported_default_prop:s0 exact bool\n')
        result = subprocess.CompletedProcess([], 0, stdout='Types: 3\n vendor_default_prop\n vendor_fp_prop\n exported_default_prop\n')
        with patch.object(port.subprocess, 'run', return_value=result):
            self.assertEqual(port.validate_property_contexts(self.root, Path('final.policy')),
                             {'files': 2, 'labels': 3})


if __name__ == '__main__':
    unittest.main()
