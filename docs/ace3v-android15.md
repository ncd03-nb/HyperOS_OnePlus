# OnePlus Ace 3V: HyperOS 3 / Android 15

SDK35 uses the complete mi_ext/pangu assembly from Android 16, with Android 15
stock APEX and a separate enforcing service profile. The local donor is
mondrian `OS3.0.2.0.VMNTWXM`. Device boot and hardware are not yet verified.

```bash
./port.sh --device OnePlusAce3V \
  --stock /path/to/vendor_odm_3v --hyperos /path/to/hyperos3 \
  --apex-stock /path/to/stock_a15 \
  --name HyperOS3-Ace3V-Android15-Enforcing
```

`--stock` retains the requested hardware vendor/odm. `--apex-stock` must contain
SDK35 system_ext and its original fs_config/file_contexts. SDK35/36 APEX mixing
is rejected. The provided PJF110 OTA is ColorOS `15.0.0.863(CN01)`, Android 15,
security patch 2025-10-01. Its system_ext image SHA256 is
`540b45ec29d44fd5d363f9b15303fec0238e05d84e024a9f6dad82d75d86564a`.
Operation and image hashes were verified against the OTA manifest; the whole
OTA signature was not verified. It supplies `com.android.compos.apex` and
`com.android.vndk.v34.apex`.

The local stock tree is
`/mnt/d/MIO-KITCHEN-PYSIDE6-5.0.0-PREVIEW/port_hyperos3_3v/_port_work/stock_a15`.

## Security and setup

- ADB starts at boot with `ro.secure=1`, `ro.adb.secure=1`, `ro.debuggable=0`
  and normal privilege dropping to shell UID2000. The daemon starts with its
  standard root UID to initialize USB, then drops privileges. `--force-adb`
  cannot enable insecure/root ADB on SDK35. Optional `--adb-key adbkey.pub`
  authorizes a caller-provided public key while retaining authentication.
  No personal key is committed.
- Init requests enforcing. Shipped production/userdebug CIL has no
  `typepermissive`; `seinfo` must confirm zero permissive types in the compiled
  binary before packing. Vendor/ODM release/debug cached policies share that
  binary, and fallback CIL carries the same rules.
- Compilation uses `-N`, matching [Android init's runtime compiler](https://android.googlesource.com/platform/system/core/+/master/init/selinux.cpp).
  This skips build-time neverallow assertions and retains runtime enforcement.
  The mixed OEM policies fail the separate neverallow build check, so there is
  no CTS policy-compliance claim.
- The minimal Provision APK replaces the donor setup UI and JNI/oat cache.
  Provisioning and alert-slider Java services use system UID1000 in the
  dedicated `ace3v_port` domain. They do not execute a root shell or add shell
  access rules. SDK35 keeps original BoringSSL binaries and reboot guards.

Regional `ro.product.mod_device=mondrian_tw_global` is retained. FeatureParser
uses the existing `mondrian.xml`, with 1240-pixel width and 120/90/60 Hz.
Panel/FOD calibration follows the Android 16 Ace 3V profile; enrollment, slider
and other hardware need Android 15 device verification.

## Validation

Tests cover metadata relocation, SDK-matched APEX, SDK35 security despite the
legacy ADB flag, regional filenames and removal of donor root shell hooks.
The Android 15 Java source is `devices/OnePlusAce3V/android-35/Ace3vHardware.java`;
its DEX JAR was built using `javac --release 8` and R8 D8 `--min-api 35`.
EROFS inode ownership, mode, capabilities, SELinux xattrs and symlink types must
be audited in the actual images. `getenforce` and `adb shell id` remain runtime
checks; host compilation and packing do not establish that the phone booted.
