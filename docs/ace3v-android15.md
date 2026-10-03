# OnePlus Ace 3V: HyperOS 3 / Android 15

SDK35 uses the complete mi_ext/pangu assembly from Android 16, with Android 15
stock APEX and a separate enforcing service profile. The local donor is
mondrian `OS3.0.2.0.VMNTWXM`. R4 has authenticated boot ADB, with shell UID2000
and SELinux enforcing confirmed on the device. Its system_server repeatedly
crashes in RoleControllerService when granting the unknown permission
`android.permission.REPOSITION_SELF_WINDOWS`; RescueParty then requests recovery.
R5 changed the Permission module to ColorOS stock and failed before ADB.
After that attempt, the host ROM records `reboot,boringssl-self-check-failed`;
the exact failed service was not captured. R6 restores R4's original signed
mainline modules and adds a role-resource overlay. Live R6 logs now confirm
the same RoleControllerService crash: its overlay was installed under
`/system/overlay`, which the donor's PackagePartitions excludes from package
scanning. The overlay package is absent and resource lookup still resolves
`xml/roles` to the original Google APK. This placement must be corrected to a
scanned partition. R7 places it at
`/system_ext/overlay/Ace3vPermissionRoles/Ace3vPermissionRoles.apk` and removes
the known R6 copy plus its metadata when updating an existing assembled tree.
R6 failed boot; enforcing and shell UID2000 were confirmed in that session.
Live R7 ADB checks confirm boot_completed=1, bootanimation stopped, unchanged
system_server PID across the observation window, and the enabled overlay
actually supplying xml/roles. The earlier RoleControllerService fatal is absent
from the captured logs. Runtime enforcing, shell UID2000 and secure properties
1/1/0 verify. Two init-named subprocess aborts and vendor SELinux AVCs remain;
hardware operation is not fully verified.

```bash
./port.sh --device OnePlusAce3V \
  --stock /path/to/vendor_odm_3v --hyperos /path/to/hyperos3 \
  --apex-stock /path/to/stock_a15 \
  --name HyperOS3-Ace3V-Android15-Enforcing
```

`--stock` retains the requested hardware vendor/odm. `--apex-stock` must contain
SDK35 system_ext and its original fs_config/file_contexts. Only system_ext APEX
are replaced; system mainline modules remain from the donor. SDK35/36 APEX
mixing is rejected.
The provided PJF110 OTA is ColorOS `15.0.0.863(CN01)`, Android 15,
security patch 2025-10-01. Its system_ext image SHA256 is
`540b45ec29d44fd5d363f9b15303fec0238e05d84e024a9f6dad82d75d86564a`.
Operation and image hashes were verified against the OTA manifest; the whole
OTA signature was not verified. It supplies `com.android.compos.apex` and
`com.android.vndk.v34.apex`.

The system image SHA256 is
`700e5295b56221611d24a45c7c57136819e0861e43cb19550c91184e0a4bb375`.
Its operations and complete image also match the OTA manifest.

## Permission module compatibility

The modded donor ships `com.google.android.permission_compressed.apex`, module
version 360743220. Its BROWSER role requests `REPOSITION_SELF_WINDOWS` through
a feature flag without a minimum SDK guard. The donor Android 15 framework
does not declare that permission. Live logs from two boots show the same
RoleControllerService exception, including 22 repeats in the second capture.

R7 keeps the donor Permission APEX and its embedded APK byte-identical. For
the known donor APEX SHA256
`7c3edc915b9c6c5955a6e82bfb731b4f4523fd4e50d22d000665d8557441e3bb`,
a system_ext overlay adds `minSdkVersion="37"` to that one permission entry in
`xml/roles`. All other role nodes and attributes remain equivalent. Unknown
module hashes are left untouched, so the overlay does not freeze unrelated
mainline versions to this donor's role configuration.

The overlay APK is installed under system_ext/overlay, has no code, is immutable,
targets the Google controller, and targets SDK28 to use the platform's pre-Q
system overlay compatibility
path. Both the donor PackagePartitions scan location and IdmapManager
legacy package checks were inspected. IdmapManager matches the
[AOSP implementation](https://android.googlesource.com/platform/frameworks/base/+/master/services/core/java/com/android/server/om/IdmapManager.java).
This uses an existing compatibility path for this preinstalled overlay; no
framework enforcement switch, SELinux allow rule, permission declaration,
APEX signing key or controller signing key is changed. The donor controller's
seapp mapping is restored exactly. If a system_ext overlay configuration
already exists, the porter registers this overlay enabled and immutable;
otherwise its static manifest supplies the default activation.

The profile under `devices/OnePlusAce3V/android-35/permission_roles/` includes
the compiled signed APK, source manifest/XML and module/overlay hashes.
R6 image readback verifies all 34 system APEX files match the donor and the
compiled role tree differs only by that one SDK guard. Donor idmap2 and its
libraries, run from a temporary directory on ColorOS, successfully create
the `xml/roles` mapping. No package was installed for this test.
Other builds still require a HyperOS boot check for overlay activation and
the resulting role resource. A passing image audit does not prove the resource
was loaded on the phone. R6's valid idmap did not establish package scanning;
R7 explicitly uses system_ext, whose containsOverlay flag is true in the
actual donor framework. R7's live device check now verifies registration,
enabled state, resource lookup and completed boot. This is a short observation
window, not a full hardware or long-term stability test.
Original BoringSSL binaries and reboot guards remain.

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
