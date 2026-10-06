# OnePlus Ace 3V: HyperOS 2 and 3 / Android 15

Current SDK35 builds remove the full SetupWizard and start ADB without host
authentication during boot. Shell UID2000 and SELinux Enforcing are retained.
The verified daemon and donor hashes are in `android-35/boot_adb/`.
The build selects an exact donor APEX hash from the default profile or a
`boot_adb/variants` profile. HyperOS 2 nuwa `OS2.0.219.0.VMBCNXM` has its own
`com.android.adbd.capex` daemon; its no-auth branch is inspected separately and
does not reuse the HyperOS 3 daemon or its patch offset. Unrecognized APEX
hashes still fail rather than mixing daemon/library versions. The HyperOS 2
variant has working boot-animation ADB: R1 device checks confirm shell UID2000,
SELinux Enforcing and security properties `ro.secure=1`, `ro.adb.secure=0`,
`ro.debuggable=0`. R2 completes boot: `sys.boot_completed=1` and bootanimation
stopped, with shell UID2000 and Enforcing confirmed again.
The earlier HyperOS 3 ROM with this flow completed boot; it still has PermissionController
compatibility crashes involving `RANGING` and `checkOpRawNoThrow`. The role
resource overlay does not resolve those code/API mismatches. Do not disable
PermissionController: that prevents PackageManagerService from starting.

HyperOS 2 nuwa R1 repeatedly aborts `system_server` during
`PackageManagerService.systemReady` because its product apps lack two privileged
permission allowlist entries. The captured log contains 13 system-process
fatals: `com.miui.personalassistant` needs
`android.permission.START_ACTIVITIES_FROM_BACKGROUND`, and
`com.miui.securitycenter` needs `android.permission.READ_WALLPAPER_INTERNAL`.
The SDK35 finish stage installs `privapp-permissions-ace3v-a15.xml` under
`product/etc/permissions`, adding only the observed grant for each matching
product app present in the tree. APKs, PermissionController and privilege
enforcement remain unchanged. Its metadata is root:root 0644 with
`system_file`; R2 needs only a new product image. Its boot is now confirmed by
the user and live ADB properties.

R2's donor GMS 25.10.36 rejects its factory module set with `No usable modules`;
the scan finds only the independent AndroidPlatformServices sidecar. Installing
the byte-identical GmsCore APK as a data update loads its module set, launches
AccountIntroActivity/PreAddAccountActivity and restores the login screen as
confirmed by the user. The APK contains eight signed module APKs, but the
factory tree lacks `GmsCore/m/container`. Its actual SDK35 loader (`sky`/`skw`)
supports reading loose modules from that directory beside a system/product
container APK. The SDK35 finish stage now stages the eight original embedded
module bytes for the hash-verified container in `google_modules.json`.
No GMS, GSF or PermissionController APK is replaced, and the independent module
is retained. Unknown GMS containers are left untouched. R3 product readback
checks the module bytes and metadata; fresh DSU factory loading still requires
a boot without the GMS data update used in the live test.

The following R4–R7 observations describe earlier iterations.

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

For HyperOS 2 nuwa `OS2.0.219.0.VMBCNXM`, use its extracted tree as
`--hyperos /path/to/hyper2` with the same SDK35 stock APEX input. The finish
stage selects `boot_adb/variants/hyperos2-nuwa-os2.0.219.0/profile.json` by
the complete `com.android.adbd.capex` hash. This donor keeps its original
`com.android.permission.capex`; the older HyperOS 3 role overlay is not
installed on an unmatched Permission APEX. Neither the Android 14 Google
bundle nor its framework patch is applied to SDK35.

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

- ADB starts at boot with `ro.secure=1`, `ro.adb.secure=0`, `ro.debuggable=0`
  and normal privilege dropping to shell UID2000. Init launches the daemon
  with its standard root UID; adbd drops privileges before USB initialization.
  SDK35 does not require `--force-adb` or a host key. The original signed APEX
  remains intact; init binds the hash-verified auth-only patched executable
  into the APEX and restarts adbd after activation. Its init mounton rule is
  included before enforcing policy compilation. Unsupported donor APEX hashes
  fail the build. See `devices/OnePlusAce3V/android-35/boot_adb/README.md`.
  The optional key handling from earlier authenticated builds remains available:
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
  Static UID-dropping defaults remain. Unrelated hardware property resets are
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
- The full SetupWizard directory and its fs_config/file_contexts entries are
  removed from the Android 15 image. Setupwizard mode is disabled in system
  and product props. The minimal Provision APK is retained without donor JNI/oat cache.
  Provisioning and alert-slider Java services use system UID1000 in the
  dedicated `ace3v_port` domain. They do not execute a root shell or add shell
  access rules. SDK35 keeps original BoringSSL binaries and reboot guards.

- Optional OEM `my_*` bind mounts retain `nofail` and lose only the `wait`
  flag that caused ten approximately 20-second waits in the observed DSU
  boot. This shared fix now runs for all Ace 3V SDK flows (34–37).
  Required first-stage and block-device waits are unchanged.

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
