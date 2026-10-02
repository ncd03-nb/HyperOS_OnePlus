# OnePlus Ace 3V: HyperOS 3 / Android 15

SDK35 uses the complete mi_ext/pangu assembly from Android 16, with Android 15
stock APEX and a separate enforcing service profile. The local donor is
mondrian `OS3.0.2.0.VMNTWXM`. The first local Android 15 build reaches
bootanimation, then shuts down, and USB ADB does not enumerate. R3 enumerates
USB ADB but the device reports unauthorized. A completed
boot and hardware operation are not yet verified.

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
  and normal privilege dropping to shell UID2000. Init launches the daemon
  with its standard root UID; adbd drops privileges before USB initialization. `--force-adb`
  cannot enable insecure/root ADB on SDK35. Optional `--adb-key adbkey.pub`
  authorizes a caller-provided public key while retaining authentication.
  `/adb_keys` is a regular root-owned 0644 file labeled `adb_keys_file`.
  Android 15's [libadbd_auth](https://android.googlesource.com/platform/frameworks/native/+/android-15.0.0_r1/libs/adbd_auth/adbd_auth.cpp)
  reads it with [ReadFileToString's default O_NOFOLLOW](https://android.googlesource.com/platform/system/libbase/+/android-15.0.0_r1/include/android-base/file.h), so the old symlink
  to `/product/etc/security/adb_keys` could not load the trusted key. R4
  replaces that symlink with the public-key file. No personal key is committed.
- SDK35 removes standalone `resetprop`/`xeutoolbox` calls changing `ro.secure`,
  `ro.debuggable` or `ro.adb.secure` from extracted
  init/scripts. Toolbuild's ResetProp package adds 13 synchronous calls to
  `post-fs-data`: four security resets and nine locked/green/vbmeta overrides.
  R2 removed only the four security resets; R3 removed all 13. At the user's
  request R4 restores the nine donor boot-state/vbmeta overrides, preserving
  non-ADB ResetProp modifications and keeping the four security resets removed.
  Static secure defaults remain. Unrelated hardware property resets are
  preserved. Binary SHA256, executable metadata
  and init execute permission matched the donor; no executable-label defect
  was found. ResetProp has not been established as the shutdown cause.
  Its separate early ADB recipe starts the APEX-provided daemon when APEXes are
  ready, at `zygote-start`, and when bootanimation runs. The Ace 3V vendor uses
  controller `a600000.dwc3`, with the gadget HAL disabled; Qualcomm creates the
  gadget and FunctionFS mount at `zygote-start`. Standard configfs actions
  bind USB only after adbd signals `sys.usb.ffs.ready=1`. A named init event
  queued from `zygote-start` restarts adbd after all vendor actions have
  created/mounted FunctionFS. Android 15's
  [adbd startup code](https://android.googlesource.com/platform/packages/modules/adb/+/android-15.0.0_r1/daemon/main.cpp)
  checks the existence of `/dev/usb-ffs/adb/ep0` once before starting USB.
  R2's repeated `start` commands leave an already-running daemon unchanged,
  so it can miss USB when started before the mount. R3 performs one restart
  per `zygote-start` event. A separate guarded event handles gadget binding.
  The port never fakes
  readiness and does not cycle an active gadget through `none` during boot.
  This changes ADB timing; it does not establish the cause of the shutdown.
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
