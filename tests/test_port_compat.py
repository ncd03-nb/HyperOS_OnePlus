import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
import port_compat as port
import erofs_config


def fixture(root, sdk=36):
    for part in (*port.PARTS, 'mi_ext'):
        (root / part).mkdir(parents=True)
    files = {
        'system/system/build.prop': f'ro.build.version.sdk={sdk}\n',
        'system/system/bin/app_process64': 'fixture ELF',
        'system/system/etc/selinux/plat_seapp_contexts':
            'user=_app isPrivApp=true name=com.google.android.permissioncontroller domain=permissioncontroller_app type=privapp_data_file levelFrom=all\n'
            'user=_app seinfo=platform isPrivApp=true name=com.android.permissioncontroller domain=permissioncontroller_app type=privapp_data_file levelFrom=all\n'
            'user=_app seinfo=platform domain=platform_app type=app_data_file levelFrom=user\n',
        'system/system/apex/com.google.android.permission_compressed.apex': 'donor permission',
        'product/etc/build.prop': f'ro.product.build.version.sdk={sdk}\n',
        'mi_ext/etc/build.prop': 'ro.product.mod_device=peridot\nro.mi.os.version.name=OS3.0\n',
        'mi_ext/system/bin/moved-service': 'executable',
        'mi_ext/system_ext/lib64/moved.so': 'library',
        'mi_ext/product/etc/device_features/peridot.xml': '<features><bool name="donor_flag">true</bool></features>',
        'product/pangu/system/bin/helper': 'pangu',
        'system/system/etc/init/hw/init.rc': ''.join(
            f'service {name} {binary}\n    oneshot\n    reboot_on_failure reboot,boringssl-self-check-failed\n'
            for name, binary in (('boringssl_self_test64', '/system/bin/boringssl_self_test64'),
                                 ('boringssl_self_test_apex64', '/apex/com.android.conscrypt/bin/boringssl_self_test64')))
            + 'on boot\n    exec -- /system/bin/xeutoolbox -n ro.debuggable 0\n',
        'vendor/etc/init/boringssl_self_test.rc': 'service boringssl_self_test64_vendor /vendor/bin/boringssl_self_test64\n    oneshot\n    reboot_on_failure reboot,boringssl-self-check-failed\n',
        'system_ext/etc/init/miuserfs.rc': 'on property:ro.debuggable=1\n    start miuserfs\n',
        'system_ext/etc/build.prop': f'ro.system_ext.build.version.sdk={sdk}\n',
        'vendor/build.prop': 'ro.product.vendor.device=OP5CFBL1\n',
        'odm/build.prop': 'import /my_manifest/build.prop\nro.product.odm.model=PJF110\n',
        'odm/etc/build.prop': 'stock',
        'system_ext/priv-app/Provision/Provision.apk': 'old APK',
        'system_ext/priv-app/Provision/oat/arm64/Provision.odex': 'stale',
        'system_ext/priv-app/Provision/lib/arm64/libnative-jni.so': 'stale',
        'system_ext/apex/com.android.art.compatible.apex': 'donor',
    }
    for rel, value in files.items():
        port.write(root / rel, value)
    if sdk == 35:
        # Structurally valid APEX fixture; no claim of a test signature.
        with zipfile.ZipFile(root / 'system/system/apex/com.android.permission.apex', 'w') as archive:
            archive.writestr('apex_manifest.pb', b'\x0a\x16com.android.permission\x10\x90\xef\xf1\xa7\x01')
            archive.writestr('apex_pubkey', b'fixture public key')
            archive.writestr('apex_payload.img', b'fixture payload')
    for part in (*port.PARTS, 'mi_ext'):
        paths = [root / part, *(root / part).rglob('*')]
        port.write(root / 'config' / (part + '_fs_config'), '/ 0 0 0755\n' + ''.join(
            f'{path.relative_to(root).as_posix()} 0 0 {"0755" if path.is_dir() else "0644"}\n'
            for path in paths))
        port.write(root / 'config' / (part + '_file_contexts'), '/ u:object_r:system_file:s0\n' + ''.join(
            '/' + re.escape(path.relative_to(root).as_posix()) + ' u:object_r:system_file:s0\n'
            for path in paths))
    meta = port.Metadata(root, 'mi_ext')
    meta.fs['mi_ext/system/bin/moved-service'] = ['1000', '2000', '0750', 'capabilities=0x400']
    meta.ctx['mi_ext/system/bin/moved-service'] = ['u:object_r:custom_exec:s0']
    meta.save()


class PortFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        fixture(self.root)

    def test_regional_donor_selects_existing_feature_without_rewriting_mod_device(self):
        port.set_props(self.root / 'mi_ext/etc/build.prop', {'ro.product.mod_device': 'mondrian_tw_global'})
        port.set_props(self.root / 'product/etc/build.prop', {'ro.product.product.name': 'mondrian'})
        port.write(self.root / 'mi_ext/product/etc/device_features/mondrian.xml', '<features/>')
        port.assemble(self.root, True)
        self.assertEqual(port.donor_name(self.root), 'mondrian')
        self.assertEqual(port.properties(self.root / 'product/etc/build.prop')['ro.product.mod_device'], 'mondrian_tw_global')

    def test_a15_permission_replacement_preserves_signed_bytes_and_metadata(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '35'})
        stock = Path(self.tmp.name) / 'stock15'
        fixture(stock, 35)
        relative = 'system/system/apex/com.android.permission.apex'
        meta = port.Metadata(stock, 'system')
        meta.fs[relative] = ['0', '2000', '0640', 'capabilities=0x0']
        meta.ctx[relative] = ['u:object_r:fixture_apex_file:s0']
        meta.save()
        other = self.root / 'system/system/apex/com.android.unrelated.apex'
        port.write(other, 'keep original module')
        expected = (stock / relative).read_bytes()
        seapp = self.root / 'system/system/etc/selinux/plat_seapp_contexts'
        previous = seapp.read_text()
        result = port.replace_permission_apex_a15(self.root, stock)
        self.assertEqual((self.root / relative).read_bytes(), expected)
        self.assertEqual(other.read_text(), 'keep original module')
        self.assertEqual(result['removed_donor_variants'], ['com.google.android.permission_compressed.apex'])
        actual = port.Metadata(self.root, 'system')
        self.assertEqual(actual.fs[relative], meta.fs[relative])
        self.assertEqual(actual.ctx[relative], meta.ctx[relative])
        expected_mapping = previous.replace('seinfo=platform isPrivApp=true name=com.android.permissioncontroller',
                                            'isPrivApp=true name=com.android.permissioncontroller')
        self.assertEqual(seapp.read_text(), expected_mapping)
        self.assertEqual(result['stock_privileged_controller_domain'], 'permissioncontroller_app')
        self.assertNotIn('system/system/apex/com.google.android.permission_compressed.apex', actual.fs)
        self.assertNotIn('system/system/apex/com.google.android.permission_compressed.apex', actual.ctx)
        self.assertEqual(port.replace_permission_apex_a15(self.root, stock)['removed_donor_variants'], [])

    def test_a15_permission_rejects_missing_or_wrong_module_before_mutation(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '35'})
        stock = Path(self.tmp.name) / 'stock15'
        fixture(stock, 35)
        source = stock / 'system/system/apex/com.android.permission.apex'
        donor = self.root / 'system/system/apex/com.google.android.permission_compressed.apex'
        before = donor.read_bytes()
        for manifest in (b'\x0a\x07invalid\x10\x01',
                         b'\x0a\x16com.android.permission\x10\xb4\x82\x82\xac\x01'):
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('apex_manifest.pb', manifest)
                archive.writestr('apex_pubkey', 'fixture')
                archive.writestr('apex_payload.img', 'fixture')
            with self.assertRaisesRegex(ValueError, 'version 35'):
                port.replace_permission_apex_a15(self.root, stock)
            self.assertEqual(donor.read_bytes(), before)
        source.unlink()
        with self.assertRaisesRegex(ValueError, 'must include system and system_ext'):
            port.replace_permission_apex_a15(self.root, stock)
        self.assertEqual(donor.read_bytes(), before)

    def test_android15_keeps_enforcing_secure_shell_even_with_legacy_force_flag(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '35'})
        port.write(self.root / 'vendor/etc/selinux/vendor_sepolicy.cil', '(type vendor_fixture)\n')
        port.write(self.root / 'system/system/etc/init/hw/init.usb.rc',
                   'service adbd /system/bin/adbd --root_seclabel=u:r:su:s0\n    user root\n')
        port.assemble(self.root, True)
        stock = Path(self.tmp.name) / 'stock15'
        fixture(stock, 35)
        with patch.object(port, 'compile_policy', return_value={'permissive_types': 0}) as compile_mock:
            report = port.finish(self.root, 'OnePlusAce3V', stock, REPO / 'fixes', adb=True)
        compile_mock.assert_called_once_with(self.root, 'secilc', enforcing=True)
        self.assertFalse(report['force_adb'])
        self.assertTrue(report['secure_boot_adb'])
        self.assertEqual(report['permission_apex']['version'], 352090000)
        self.assertFalse((self.root / 'system/system/apex/com.google.android.permission_compressed.apex').exists())
        props = port.properties(self.root / 'system/system/etc/prop.default')
        self.assertEqual([props[k] for k in ('ro.debuggable', 'ro.secure', 'ro.adb.secure')], ['0', '1', '1'])
        init = (self.root / 'system/system/etc/init/ace3v-port.rc').read_text()
        self.assertIn('user system', init)
        self.assertIn('seclabel u:r:ace3v_port:s0', init)
        self.assertNotIn('user root', init)
        self.assertNotIn('u:r:shell:s0', init)
        self.assertFalse((self.root / 'system/system/bin/ace3v-skip-setup.sh').exists())
        self.assertIn('reboot_on_failure', (self.root / 'system/system/etc/init/hw/init.rc').read_text())
        self.assertNotIn('--root_seclabel', (self.root / 'system/system/etc/init/hw/init.usb.rc').read_text())

    def test_android15_removes_runtime_security_resets_and_starts_before_setup(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '35'})
        path = self.root / 'system/system/etc/init/hw/init.rc'
        retained = '    exec -- /system_ext/xbin/xeutoolbox -n ro.vendor.display.panel device-panel\n'
        port.write(path, 'on post-fs-data\n'
                   '    exec u:r:init:s0 root root -- /system_ext/xbin/xeutoolbox -n ro.secure 1\n'
                   '    exec -- /system/bin/resetprop -n ro.debuggable 0\n'
                   '    exec -- /system_ext/xbin/xeutoolbox -n ro.secureboot.lockstate locked\n' + retained)
        script = self.root / 'vendor/bin/example.sh'
        port.write(script, '#!/system/bin/sh\nresetprop ro.secure 0\nresetprop -n ro.debuggable 1\n')
        alias = self.root / 'vendor/bin/example-link.sh'
        port.link(alias, '/vendor/bin/example.sh')
        port.secure_adb(self.root, REPO / 'fixes', ace16=True)
        self.assertEqual(path.read_text(), 'on post-fs-data\n\n\n'
                         '    exec -- /system_ext/xbin/xeutoolbox -n ro.secureboot.lockstate locked\n' + retained)
        self.assertNotIn('resetprop', script.read_text())
        self.assertTrue(alias.is_symlink() if os.name != 'nt' else alias.read_bytes().startswith(b'!<symlink>'))
        self.assertEqual(port.remove_adb_property_resets(self.root), 0)
        rc = (self.root / 'system/system/etc/init/hyperos_force_adb.rc').read_text()
        for trigger in ('apex.all.ready=true', 'init.svc.bootanim=running', 'on zygote-start'):
            self.assertIn(trigger, rc)
        self.assertNotIn('setprop sys.usb.ffs.ready', rc)
        self.assertNotIn('setprop sys.usb.config none', rc)
        self.assertIn('setprop service.adb.root 0', rc)
        self.assertIn('setenforce 1', rc)
        blocks = re.split(r'(?m)^on ', rc)
        bind = next(block for block in blocks if 'write /config/usb_gadget/g1/UDC' in block)
        self.assertIn('property:sys.usb.ffs.ready=1', bind.splitlines()[0])
        self.assertIn('property:sys.usb.config=adb', bind.splitlines()[0])
        self.assertIn('property:sys.usb.configfs=1', bind.splitlines()[0])
        self.assertEqual(port.properties(self.root / 'system/system/etc/prop.default')['ro.secure'], '1')

    def test_android15_preserves_toolbuild_boot_resets_and_removes_only_adb_security(self):
        values = [('boot.vbmeta.device_state', 'locked'), ('boot.verifiedbootstate', 'green'),
                  ('secureboot.lockstate', 'locked'), ('boot.flash.locked', '1'),
                  ('secure', '1'), ('debuggable', '0'), ('boot.vbmeta.avb_version', '1.3'),
                  ('boot.vbmeta.hash_alg', 'sha256'), ('boot.vbmeta.size', '8192'),
                  ('boot.vbmeta.digest', 'donor-digest'), ('secure', '1'),
                  ('debuggable', '0'), ('boot.flash.locked', '1')]
        path = self.root / 'system/system/etc/init/hw/init.rc'
        hardware = '    exec -- /system_ext/xbin/xeutoolbox -n ro.boot.hardware qcom\n'
        port.write(path, 'on post-fs-data\n' + ''.join(
            f'    exec u:r:init:s0 root root -- /system_ext/xbin/xeutoolbox -n ro.{key} {value}\n'
            for key, value in values) + hardware)
        self.assertEqual(port.remove_adb_property_resets(self.root), 4)
        expected = 'on post-fs-data\n' + ''.join(
            '\n' if key in ('secure', 'debuggable') else
            f'    exec u:r:init:s0 root root -- /system_ext/xbin/xeutoolbox -n ro.{key} {value}\n'
            for key, value in values) + hardware
        self.assertEqual(path.read_text(), expected)
        self.assertEqual(port.remove_adb_property_resets(self.root), 0)

    def test_adb_public_key_is_regular_file_readable_without_following_symlinks(self):
        # Test transport/storage, not RSA verification; no personal key fixture.
        encoded = base64.b64encode(bytes(524)).decode('ascii')
        public_key = Path(self.tmp.name) / 'test-adbkey.pub'
        port.write(public_key, encoded + ' test-host\n')
        key = self.root / 'system/adb_keys'
        port.link(key, '/product/etc/security/adb_keys')
        for _ in range(2):
            port.authorize_adb(self.root, public_key)
        self.assertFalse(key.is_symlink())
        self.assertFalse(key.read_bytes().startswith(b'!<symlink>'))
        self.assertEqual(key.read_text(), encoded + ' test-host\n')
        self.assertEqual(key.read_bytes(), (self.root / 'product/etc/security/adb_keys').read_bytes())
        meta = port.Metadata(self.root, 'system')
        self.assertEqual(meta.fs['system/adb_keys'], ['0', '0', '0644'])
        self.assertEqual(meta.ctx['system/adb_keys'], ['u:object_r:adb_keys_file:s0'])
        if os.name != 'nt':
            fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                self.assertEqual(os.read(fd, 4096), key.read_bytes())
            finally:
                os.close(fd)

    def test_android15_restarts_adbd_only_after_vendor_gadget_event(self):
        rc = (REPO / 'fixes/hyperos_early_adb_a15.rc').read_text()
        actions = {}
        for block in re.split(r'(?m)^on ', rc)[1:]:
            lines = block.splitlines()
            actions[lines[0]] = [line.strip() for line in lines[1:] if line.startswith('    ')]
        # Init queues the named event behind every vendor zygote-start action.
        self.assertIn('trigger ace3v-adb-usb-ready', actions['zygote-start'])
        self.assertNotIn('restart adbd', actions['zygote-start'])
        ready = actions['ace3v-adb-usb-ready']
        self.assertLess(ready.index('setprop service.adb.root 0'), ready.index('restart adbd'))
        self.assertLess(ready.index('restart adbd'), ready.index('trigger ace3v-adb-bind'))
        self.assertEqual(sum(action.count('restart adbd') for action in actions.values()), 1)

    def test_android15_direct_force_adb_cannot_disable_authentication(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '35'})
        port.force_adb(self.root, REPO / 'fixes')
        props = port.properties(self.root / 'system/system/etc/prop.default')
        self.assertEqual(props['ro.adb.secure'], '1')
        self.assertEqual(props['ro.secure'], '1')
        self.assertEqual(props['ro.debuggable'], '0')

    def test_android15_removes_donor_root_shell_service_but_keeps_root_daemon(self):
        init = self.root / 'system_ext/etc/init/init.miui.ext.rc'
        port.write(init, 'service pubcert_download /system/bin/sh /system_ext/bin/init.rootpub.sh\n'
                   '    user root\n    seclabel u:r:shell:s0\n\n'
                   'on property:odm.security.rootpub.trigger=1\n    start pubcert_download\n\n'
                   'service fdpp /system_ext/bin/fdpp daemon\n    user root\n')
        port.harden_a15_init(self.root)
        self.assertNotIn('pubcert_download', init.read_text())
        self.assertIn('service fdpp', init.read_text())
        self.assertIn('user root', init.read_text())

    def test_android16_relocation_keeps_capabilities_context_and_full_pangu(self):
        port.assemble(self.root, True)
        meta = port.Metadata(self.root, 'system')
        self.assertEqual(meta.fs['system/system/bin/moved-service'], ['1000', '2000', '0750', 'capabilities=0x400'])
        self.assertEqual(meta.ctx['system/system/bin/moved-service'], ['u:object_r:custom_exec:s0'])
        self.assertTrue((self.root / 'system_ext/lib64/moved.so').is_file())
        self.assertTrue((self.root / 'system/system/pangu/system/bin/helper').is_file())
        self.assertTrue((self.root / 'system/mi_ext/etc/build.prop').is_file())
        if os.name != 'nt':
            self.assertEqual(os.readlink(self.root / 'product/pangu'), '/system/pangu')

    def test_android17_keeps_legacy_pangu_and_crypto_guards(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '37'})
        original = (self.root / 'system/system/etc/init/hw/init.rc').read_bytes()
        port.assemble(self.root, False)
        result = port.finish(self.root, 'OnePlusAce3V', None, REPO / 'fixes')
        self.assertFalse(result['ace3v_android16'])
        self.assertEqual((self.root / 'system/system/etc/init/hw/init.rc').read_bytes(), original)
        self.assertTrue((self.root / 'system/system/bin/helper').is_file())
        self.assertTrue((self.root / 'mi_ext/etc/build.prop').is_file())
        self.assertFalse((self.root / 'system/system/etc/init/ace3v-port.rc').exists())

    def test_wrong_android_apex_is_rejected(self):
        stock = Path(self.tmp.name) / 'stock17'
        fixture(stock, 37)
        with self.assertRaisesRegex(ValueError, 'Android 16 stock'):
            port.replace_apex(self.root, stock)

    def test_android17_forced_adb_keeps_crypto_guards_and_miui_branches(self):
        port.set_props(self.root / 'system/system/build.prop', {'ro.build.version.sdk': '37'})
        branch = (self.root / 'system_ext/etc/init/miuserfs.rc').read_bytes()
        init = (self.root / 'system/system/etc/init/hw/init.rc').read_bytes()
        port.write(self.root / 'system/system/etc/prop.default', 'ro.debuggable=0\n')
        port.finish(self.root, 'OnePlusAce3V', None, REPO / 'fixes', adb=True)
        self.assertIn('reboot_on_failure', (self.root / 'system/system/etc/init/hw/init.rc').read_text())
        self.assertEqual((self.root / 'system_ext/etc/init/miuserfs.rc').read_bytes(), branch)
        self.assertEqual((self.root / 'system/system/etc/init/hw/init.rc').read_bytes(), init)
        self.assertEqual(port.properties(self.root / 'system/system/etc/prop.default')['ro.debuggable'], '1')

    def test_apex_source_metadata_replaces_donor_metadata(self):
        stock = Path(self.tmp.name) / 'stock'
        fixture(stock)
        (stock / 'system_ext/apex/com.android.art.compatible.apex').unlink()
        apex = stock / 'system_ext/apex/com.android.compos.apex'
        port.write(apex, 'stock compos')
        meta = port.Metadata(stock, 'system_ext')
        meta.pin('apex/com.android.compos.apex', 'apex_test_file', '0640')
        meta.save()
        port.replace_apex(self.root, stock)
        self.assertFalse((self.root / 'system_ext/apex/com.android.art.compatible.apex').exists())
        self.assertEqual(port.Metadata(self.root, 'system_ext').fs['system_ext/apex/com.android.compos.apex'], ['0', '0', '0640'])

    def test_clean_finish_has_secure_adb_and_original_crypto_services(self):
        port.assemble(self.root, True)
        stock = Path(self.tmp.name) / 'stock'
        fixture(stock)
        shutil.copy2(REPO / 'RES/system_ext/priv-app/Provision/Provision.apk',
                     self.root / 'system_ext/priv-app/Provision/Provision.apk')
        with patch.object(port, 'compile_policy', return_value={'compiled': True}):
            result = port.finish(self.root, 'OnePlusAce3V', stock, REPO / 'fixes')
        self.assertEqual(result['boringssl_reboot_guards_removed'], 3)
        init = (self.root / 'system/system/etc/init/hw/init.rc').read_text()
        self.assertIn('service boringssl_self_test64 /system/bin/boringssl_self_test64', init)
        self.assertNotIn('reboot_on_failure', init)
        self.assertNotIn('ace3v-crypto-test', init)
        self.assertIn('xeutoolbox -n ro.debuggable 0', init)
        self.assertFalse((self.root / 'system_ext/priv-app/Provision/oat').exists())
        self.assertFalse((self.root / 'system_ext/priv-app/Provision/lib').exists())
        expected = {'ro.debuggable': '0', 'ro.secure': '1', 'ro.adb.secure': '1'}
        self.assertEqual({key: port.properties(self.root / 'system/system/etc/prop.default')[key]
                          for key in expected}, expected)
        self.assertEqual({key: port.properties(self.root / 'system/system/build.prop')[key]
                          for key in expected}, expected)
        self.assertFalse(result['force_adb'])
        self.assertTrue(result['secure_boot_adb'])
        self.assertTrue((self.root / 'system/system/etc/init/hyperos_force_adb.rc').is_file())
        self.assertEqual(port.properties(self.root / 'system/system/build.prop')['persist.sys.usb.config'], 'adb')
        self.assertIn('on property:ro.debuggable=1', (self.root / 'system_ext/etc/init/miuserfs.rc').read_text())
        self.assertIn('donor_flag', (self.root / 'product/etc/device_features/peridot.xml').read_text())
        script = self.root / 'system/system/bin/ace3v-skip-setup.sh'
        self.assertNotIn(b'\r', script.read_bytes())
        self.assertEqual(port.Metadata(self.root, 'system').fs['system/system/bin/ace3v-skip-setup.sh'][2], '0755')

    def test_android16_insecure_adb_requires_explicit_flag(self):
        port.assemble(self.root, True)
        stock = Path(self.tmp.name) / 'stock'
        fixture(stock)
        with patch.object(port, 'compile_policy', return_value={'compiled': True}):
            result = port.finish(self.root, 'OnePlusAce3V', stock, REPO / 'fixes', adb=True)
        props = port.properties(self.root / 'system/system/etc/prop.default')
        self.assertEqual(props['ro.debuggable'], '1')
        self.assertEqual(props['ro.secure'], '0')
        self.assertEqual(props['ro.adb.secure'], '0')
        self.assertTrue(result['force_adb'])
        self.assertFalse(result['secure_boot_adb'])
        self.assertTrue((self.root / 'system/system/etc/init/hyperos_force_adb.rc').is_file())

    def test_logging_wrapper_is_removed_without_removing_test(self):
        init = self.root / 'system/system/etc/init/hw/init.rc'
        port.write(init, 'service boringssl_self_test64 /system/bin/sh /system/bin/ace3v-crypto-test.sh boringssl_self_test64 /system/bin/boringssl_self_test64\n    seclabel u:r:boringssl_self_test:s0\n    oneshot\n')
        port.clean_crypto(self.root)
        self.assertEqual(init.read_text(), 'service boringssl_self_test64 /system/bin/boringssl_self_test64\n    oneshot\n')

    @unittest.skipIf(os.name == 'nt', 'native Linux symlinks')
    def test_windows_extractor_symlinks_are_converted_on_linux(self):
        encoded = self.root / 'mi_ext/system/bin/encoded-link'
        encoded.write_bytes(b'!<symlink>' + ('/system/bin/sh\0').encode('utf-16'))
        port.assemble(self.root, True)
        self.assertEqual(os.readlink(self.root / 'system/system/bin/encoded-link'), '/system/bin/sh')

    @unittest.skipIf(os.name == 'nt', 'native Linux symlinks')
    def test_repacked_inode_keeps_uid_gid_mode_capabilities_and_selinux(self):
        port.assemble(self.root, True)
        generated = self.root / 'system/system/etc/init/generated.rc'
        port.write(generated, 'on boot\n    setprop test.fixture 1\n')
        generated.chmod(0o777)  # Simulate Windows/DrvFS host mode.
        erofs_config.sync_config(str(self.root), 'system')
        image = Path(self.tmp.name) / 'system.img'
        tools = REPO / 'bin/Linux/x86_64'
        subprocess.run([str(tools / 'mkfs.erofs'), '-zlz4hc,0', '--mount-point=/system',
                        '--product-out=' + str(self.root),
                        '--fs-config-file=' + str(self.root / 'config/system_fs_config'),
                        '--file-contexts=' + str(self.root / 'config/system_file_contexts'),
                        str(image), str(self.root / 'system')], check=True, capture_output=True)
        extracted = Path(self.tmp.name) / 'roundtrip'
        subprocess.run([str(tools / 'extract.erofs'), '-i', str(image), '-x', '--only-cfg',
                        '-s', '-f', '-o', str(extracted)], check=True, capture_output=True)
        actual = port.Metadata(extracted, 'system')
        self.assertEqual(actual.fs['system/system/bin/moved-service'], ['1000', '2000', '0750', 'capabilities=0x400'])
        self.assertEqual(actual.ctx['system/system/bin/moved-service'], ['u:object_r:custom_exec:s0'])
        self.assertEqual(actual.fs['system/system/etc/init/generated.rc'][2], '0644')

    def test_context_override_keeps_existing_owner_and_capabilities(self):
        port.assemble(self.root, True)
        erofs_config.set_context(str(self.root), 'system', 'system/bin/moved-service', 'updated_exec', '0750')
        meta = port.Metadata(self.root, 'system')
        self.assertEqual(meta.fs['system/system/bin/moved-service'], ['1000', '2000', '0750', 'capabilities=0x400'])
        self.assertEqual(meta.ctx['system/system/bin/moved-service'], ['u:object_r:updated_exec:s0'])

    @unittest.skipIf(os.name == 'nt', 'native Linux symlinks')
    def test_sync_preserves_metadata_and_does_not_dereference_absolute_link(self):
        port.assemble(self.root, True)
        erofs_config.sync_config(str(self.root), 'system')
        meta = port.Metadata(self.root, 'system')
        self.assertEqual(meta.fs['system/system/bin/moved-service'], ['1000', '2000', '0750', 'capabilities=0x400'])
        self.assertEqual(meta.fs['system/mi_ext/system'][2], '0777')
        self.assertEqual(meta.ctx['system/system/bin/moved-service'], ['u:object_r:custom_exec:s0'])


if __name__ == '__main__':
    unittest.main()
