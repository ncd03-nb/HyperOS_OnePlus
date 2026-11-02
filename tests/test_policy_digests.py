from pathlib import Path
import hashlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import port_compat as port


class PolicyDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for part in port.PARTS:
            port.write(self.root / 'config' / (part + '_fs_config'), f'{part} 0 0 0755\n')
            port.write(self.root / 'config' / (part + '_file_contexts'),
                       f'/{part} u:object_r:system_file:s0\n')
        for part, stem in (('system/system', 'plat'), ('system_ext', 'system_ext'), ('product', 'product')):
            base = self.root / part / 'etc/selinux'
            port.write(base / (stem + '_sepolicy.cil'), f'; {stem}\n(type {stem}_fixture)\n')
            port.write(base / 'mapping/34.0.cil', f'; {stem} mapping\n')
            port.write(base / (stem + '_sepolicy_and_mapping.sha256'), '' if stem == 'system_ext' else 'stale\n')
        vendor = self.root / 'vendor/etc/selinux'
        port.write(vendor / 'plat_sepolicy_vers.txt', '34.0\n')
        port.write(vendor / 'plat_pub_versioned.cil', '; vendor mapping\n')
        port.write(vendor / 'vendor_sepolicy.cil', '(type vendor_fixture)\n')
        (self.root / 'odm/etc/selinux').mkdir(parents=True)

    def compiler(self, argv, **kwargs):
        if argv[0] == 'seinfo':
            return subprocess.CompletedProcess(argv, 0, stdout='Permissive Types: 0\n')
        Path(argv[argv.index('-o') + 1]).write_bytes(b'compiled final policy')
        return subprocess.CompletedProcess(argv, 0)

    def test_cache_stamps_follow_final_cil_instead_of_empty_or_stale_donor_hashes(self):
        cil = self.root / 'system_ext/etc/selinux/system_ext_sepolicy.cil'
        port.write(cil, cil.read_text() + '(typepermissive system_ext_fixture)\n')
        with patch.object(port.subprocess, 'run', side_effect=self.compiler):
            report = port.compile_policy(self.root, enforcing=True)
        self.assertNotIn('typepermissive', cil.read_text())
        for part, stem in (('system/system', 'plat'), ('system_ext', 'system_ext'), ('product', 'product')):
            base = self.root / part / 'etc/selinux'
            expected = hashlib.sha256((base / (stem + '_sepolicy.cil')).read_bytes() +
                                      (base / 'mapping/34.0.cil').read_bytes()).hexdigest()
            name = stem + '_sepolicy_and_mapping.sha256'
            self.assertEqual((base / name).read_text(), expected + '\n')
            self.assertEqual(report['policy_digests'][stem], expected)
            for target in ('vendor', 'odm'):
                for kind in ('precompiled_sepolicy', 'precompiled_sepolicy_debug'):
                    self.assertEqual((self.root / target / 'etc/selinux' / (kind + '.' + name)).read_text(),
                                     expected + '\n')
        self.assertEqual(report['permissive_types'], 0)

    def test_failed_compilation_does_not_mark_donor_hashes_as_usable(self):
        before = (self.root / 'system_ext/etc/selinux/system_ext_sepolicy_and_mapping.sha256').read_bytes()
        with patch.object(port.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'secilc')):
            with self.assertRaises(subprocess.CalledProcessError):
                port.compile_policy(self.root, enforcing=True)
        self.assertEqual((self.root / 'system_ext/etc/selinux/system_ext_sepolicy_and_mapping.sha256').read_bytes(), before)

    def test_invalid_property_label_does_not_mark_new_cache_as_usable(self):
        stamp = self.root / 'system_ext/etc/selinux/system_ext_sepolicy_and_mapping.sha256'
        before = stamp.read_bytes()
        port.write(self.root / 'vendor/etc/selinux/vendor_property_contexts',
                   'vendor.fixture u:object_r:undefined_prop:s0 exact\n')
        with patch.object(port.subprocess, 'run', side_effect=self.compiler):
            with self.assertRaisesRegex(ValueError, 'vendor_property_contexts:1.*undefined_prop'):
                port.compile_policy(self.root, enforcing=True)
        self.assertEqual(stamp.read_bytes(), before)

    def test_private_product_policy_without_vendor_mapping_is_compiled_and_stamped(self):
        mapping = self.root / 'product/etc/selinux/mapping/34.0.cil'
        mapping.unlink()
        with patch.object(port.subprocess, 'run', side_effect=self.compiler) as run:
            report = port.compile_policy(self.root, enforcing=True)
        args = run.call_args_list[0].args[0]
        cil = self.root / 'product/etc/selinux/product_sepolicy.cil'
        self.assertIn(str(cil), args)
        self.assertNotIn(str(mapping), args)
        self.assertFalse(mapping.exists())
        expected = hashlib.sha256(cil.read_bytes()).hexdigest()
        self.assertEqual(report['policy_digests']['product'], expected)
        self.assertIn('product/etc/selinux/product_sepolicy.cil', report['policy_inputs'])
        self.assertEqual(report['permissive_types'], 0)
        for part in ('vendor', 'odm'):
            stamp = self.root / part / 'etc/selinux/precompiled_sepolicy.product_sepolicy_and_mapping.sha256'
            self.assertEqual(stamp.read_text(), expected + '\n')

    def test_optional_mappings_are_not_replaced_with_another_api_version(self):
        for part in ('system_ext', 'product'):
            mapping = self.root / part / 'etc/selinux/mapping/34.0.cil'
            mapping.unlink()
            port.write(mapping.with_name('35.0.cil'), '(type wrong_version)\n')
        with patch.object(port.subprocess, 'run', side_effect=self.compiler) as run:
            report = port.compile_policy(self.root, enforcing=True)
        self.assertFalse(any('35.0.cil' in p for p in run.call_args_list[0].args[0]))
        self.assertFalse(any('35.0.cil' in p for p in report['policy_inputs']))
        for part in ('system_ext', 'product'):
            self.assertFalse((self.root / part / 'etc/selinux/mapping/34.0.cil').exists())

    def test_absent_optional_policy_removes_stale_stamp_pairs_and_metadata(self):
        for part in ('system_ext', 'product'):
            base = self.root / part / 'etc/selinux'
            (base / (part + '_sepolicy.cil')).unlink()
            (base / 'mapping/34.0.cil').unlink()
            for target in ('vendor', 'odm'):
                meta = port.Metadata(self.root, target)
                for kind in ('precompiled_sepolicy', 'precompiled_sepolicy_debug'):
                    rel = f'etc/selinux/{kind}.{part}_sepolicy_and_mapping.sha256'
                    port.write(self.root / target / rel, 'stale\n')
                    meta.pin(rel)
                meta.save()
        with patch.object(port.subprocess, 'run', side_effect=self.compiler):
            report = port.compile_policy(self.root, enforcing=True)
        self.assertEqual(set(report['policy_digests']), {'plat'})
        for part in ('system_ext', 'product'):
            self.assertFalse((self.root / part / 'etc/selinux' /
                              (part + '_sepolicy_and_mapping.sha256')).exists())
            for target in ('vendor', 'odm'):
                meta = port.Metadata(self.root, target)
                for kind in ('precompiled_sepolicy', 'precompiled_sepolicy_debug'):
                    rel = f'etc/selinux/{kind}.{part}_sepolicy_and_mapping.sha256'
                    self.assertFalse((self.root / target / rel).exists())
                    self.assertNotIn(target + '/' + rel, meta.fs)
                    self.assertNotIn(target + '/' + rel, meta.ctx)

    def test_platform_mapping_is_still_mandatory_before_mutating_policy(self):
        (self.root / 'system/system/etc/selinux/mapping/34.0.cil').unlink()
        policy = self.root / 'vendor/etc/selinux/vendor_sepolicy.cil'
        before = policy.read_bytes()
        with patch.object(port.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Donor lacks vendor mapping .*system.*34.0.cil'):
                port.compile_policy(self.root, enforcing=True)
        run.assert_not_called()
        self.assertEqual(policy.read_bytes(), before)

    def test_compat_input_is_included_in_digest_for_the_compiled_policy(self):
        compat = self.root / 'system_ext/etc/selinux/mapping/34.0.compat.cil'
        port.write(compat, '; compatibility declarations\n')
        with patch.object(port.subprocess, 'run', side_effect=self.compiler):
            report = port.compile_policy(self.root, enforcing=True)
        expected = hashlib.sha256(b''.join(path.read_bytes() for path in
            port.split_policy_inputs(self.root, '34.0')['system_ext'])).hexdigest()
        self.assertEqual(report['policy_digests']['system_ext'], expected)

    def test_unresolved_policy_with_missing_optional_mapping_still_fails_compilation(self):
        (self.root / 'product/etc/selinux/mapping/34.0.cil').unlink()
        stamp = self.root / 'product/etc/selinux/product_sepolicy_and_mapping.sha256'
        before = stamp.read_bytes()
        with patch.object(port.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'secilc')):
            with self.assertRaises(subprocess.CalledProcessError):
                port.compile_policy(self.root, enforcing=True)
        self.assertEqual(stamp.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
