# OnePlus Ace 3V: HyperOS 1 / Android 14

SDK34 uses the complete mi_ext/pangu assembly, a separate Android 14 device
profile, and matching stock `system_ext/apex`. The initial donor is fuxi
`OS1.0.24.1.8.DEV` (Android 14, January 2024). The stock APEX source is Ace 3V
ColorOS `PJF110_14.0.1.720(CN01)`, Android 14 / SDK34, October 2024.

The finishing pass requires an arm64-only vendor base and aligns the donor's
`ro.system.product.cpu.abilist*` with that base. The first device boot exposed
a PackageManager crash while scanning a multiArch Google app: Xiaomi's SDK34
helper tried the donor ABI32 list, then indexed the empty runtime ABI32 list.
Clearing the system ABI32 list avoids this inconsistent lookup while keeping
the system arm64-only. This ABI correction does not modify APKs or framework code.

After the ABI fix, the next device boot completed package scanning but
crashed at systemReady because SecurityCenter requested
`android.permission.READ_WALLPAPER_INTERNAL` without a product allowlist entry.
The SDK34 profile adds only this permission for `com.miui.securitycenter`
in `/product/etc/permissions`. Privapp permission enforcement stays enabled;
this permission correction leaves the APK, framework, APEX signatures and SELinux
policy unchanged.

R3 booted on the device. Its Control Center then retained the initial no-network
state while ConnectivityService reported validated Wi-Fi and both SIMs were
loaded. A SystemUI thread trace identified `SysUiBg` waiting in the synchronous
Xiaomi `IMiuiTelephony.isVoNREnabled()` call during mobile-controller construction.
The SDK34 finishing pass replaces only that RPC instruction and its result move
in `TelephonyManagerEx.isVoNREnabled(I)Z` with a false result and padding. DEX
offsets, exception handlers and unrelated methods remain intact. This avoids a
Xiaomi UI capability query; it does not change the OnePlus vendor's IMS settings.

The donor GMS container also repeatedly rejected its Chimera module set with
`No usable modules`, leaving AccountManager's add-account requests unanswered.
Installing the stock Ace 3V Android 14 GMS 24.21.13 and GSF 14 restored the login
screen on R3, as confirmed by the user. SDK34 donors with `product/priv-app/GmsCore`
now require `my_bigball` from the same `PJF110_14.0.1.720(CN01)` stock source used
for APEX. The porter checks the three bundle files and eight embedded Chimera
modules against `google_stock.json` before copying any files. It installs GmsCore,
its independent AndroidPlatformServices module and GSF, and stages the eight
unchanged embedded module APKs under `GmsCore/m/container`. The signed container
APK is preserved byte for byte. On R4 the factory app resolved only the independent
sidecar and returned `No usable modules`; installing the identical GMS APK as a
data update loaded all eight modules and opened the login screen. The container's
factory loader explicitly supports `m/container` under `/product`. Staging these
modules makes that path available without relying on asset extraction at boot.
It removes stale app oat files and the obsolete standalone sidecar, and pins
ownership, modes and labels for the relocated files. The Google APKs retain their
original Google signatures. A pre-extracted `--apex-stock` directory must include
both `system_ext` and `my_bigball` with their config metadata; a full OTA provides
both automatically. No large Google APK is stored in this repository.

The local port retains the caller's supplied Ace 3V vendor/odm hardware tree;
the OTA supplies the stock system_ext APEX and the Google bundle. System mainline modules,
including Permission and adbd, remain from the Android 14 donor. The Android
15 role overlay is not installed on SDK34.

Full SetupWizard folders and their metadata are removed. The minimal Provision
APK and provisioning service remain, running as system UID1000. Boot ADB uses
the hash-verified Android 14 daemon with no host authentication, while keeping
SELinux Enforcing and shell UID2000. Original BoringSSL tests and reboot guards
remain. See `devices/OnePlusAce3V/android-34/boot_adb/README.md`.

The shared finishing pass removes only `wait` from optional OEM bind sources
`/mnt/vendor/my_*` with matching `/my_*` targets, `none`, `bind` and `nofail`.
This runs for every Ace 3V donor SDK, including the Android 14/15/16 profiles
and the legacy Android 17 flow. Required first-stage/block-device waits remain.
The supplied Android 14 tree has no such late bind-wait lines; absence of a
change is reported as an empty list, not as a runtime speed verification.

Panel/FOD settings use the previous Ace 3V physical calibration: 1240x2772,
density 560, 120/90/60Hz, FOD center 620,2414 and size 195x195. Device-specific
files, enforcing service policy and native app_process launcher use the same
system-UID design as Android 15. The helper uses reflection rather than newer
compile-time Android APIs.

The OTA range extractor verifies operation SHA256 and final partition SHA256.
It does not claim whole-OTA signature verification. Repacking uses native
Linux paths to retain symlinks and case-distinct files; image readback must
match ownership, mode, capabilities, SELinux context and file contents.

The initial device boot reached bootanimation but looped in PackageManager and
entered recovery through RescueParty. R3 boot and the stock Google update's
login-screen behavior are confirmed. R4 boot and its Control Center SIM/Wi-Fi
display are confirmed by the user. GMS worked after reinstalling the same APK
over ADB; Play's first post-login process timed out fetching preload data, and
restarting Play restored store content. The revised factory module layout still
requires a fresh DSU boot to verify it independently of the installed GMS update.
Build/policy/image checks do not establish complete hardware
or application compatibility. No Android 14 change is pushed automatically.
