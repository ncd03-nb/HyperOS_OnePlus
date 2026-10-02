# OnePlus Ace 3V: HyperOS 3 / Android 16

The user confirmed that the peridot donor **OS3.0.305.0.WNPCNXM / SDK36**
booted via DSU on PJF110 using the local BOOTDIAG R3 assembly. This repository
now carries that assembly flow without the crypto logging wrapper. A new image
built by this updated repository still needs its own device boot verification.
Hardware features are not all verified by reaching the launcher.

## Inputs and build

`--stock` supplies Ace 3V hardware vendor/odm. SDK36 also needs stock
`system_ext/apex` from **Android 16**. `--apex-stock` can point at a separate
Android 16 ROM when the hardware base is another version. The porter rejects
an APEX source with a different SDK rather than mixing Android 17 APEX into
an Android 16 donor.

For the extracted sources used in this session, run under Linux/WSL:

```bash
./port.sh \
  --stock /mnt/d/MIO-KITCHEN-PYSIDE6-5.0.0-PREVIEW/port_hyperos3_3v/vendor_odm_3v \
  --hyperos /mnt/d/MIO-KITCHEN-PYSIDE6-5.0.0-PREVIEW/port_hyperos3_3v/hyperos3 \
  --apex-stock /mnt/d/MIO-KITCHEN-PYSIDE6-5.0.0-PREVIEW/cos16 \
  --name HyperOS3-Ace3V
```

Extracted directories require `config/<partition>_fs_config` and
`config/<partition>_file_contexts`; their original UID/GID, permissions,
capabilities and SELinux labels are preserved when files move. Windows MIO
encoded symlinks are converted to native symlinks when assembling on Linux.
URL, ZIP, payload and raw-image inputs remain supported. Use a fresh `--work`
directory for each build. `--assemble-only` stops before image packing.
Packed intermediate partition trees are removed unless `--keep-work` is set,
so storing the final ZIP does not also require retaining all unpacked files.

In Actions use the normal `stock_url` / `hyperos_url` fields; fill
`apex_stock_url` only when stock does not provide SDK36 system_ext. CLI and
Actions execute the same porter; there is no runner-specific source patch.

## SDK36 assembly

1. Merge all three mi_ext payloads with their source fs_config/file_contexts.
   Move residual mi_ext to the system image, link its product/system/system_ext
   paths, and replace its obsolete overlay-mount init script.
2. Move the complete product/pangu to /system/pangu and leave a compatibility
   symlink. Preserve moved binaries' modes and capabilities.
3. Replace system_ext APEX and their metadata from matching Android 16 stock.
4. Keep the donor mod_device/FeatureParser filename. Apply 1240x2772, density
   560, display ID 4630946583411818883 and 120/90/60 Hz. Android 17 keeps its
   existing device profile. SDK36 FOD geometry follows the booted assembly;
   enrollment and illumination still require verification.
5. Synchronize ODM identity and haptics into both build.prop paths, disable
   qseelogd, and install the existing app_process alert-slider helper.
6. Use the minimal Provision APK, remove donor/Android 13 JNI and oat/vdex,
   remove Nothings.Provision overlay and XiaomiEUExt, and run a bounded setup
   bypass after SettingsProvider starts.
7. Enable early insecure ADB, prevent xeu init from resetting ro.debuggable /
   ro.secure, and gate MIUI debug-only filesystem/injection branches behind
   persist.sys.ace3v.miui_debug=1. The setting is absent by default.
8. Compile split CIL using the stock vendor mapping version, make the
   development policy permissive and replace precompiled policy/digests.

The historical failure reboot reason was `boringssl-self-check-failed`.
The booted R3 configuration suppressed six BoringSSL `reboot_on_failure`
guards. SDK36 keeps that scoped compatibility workaround and executes the
original test binaries directly, with their original init triggers/options.
No crypto wrapper, persistent crypto log, or test-result property is shipped.
Suppressing the guards does not establish that all crypto checks pass or fix
the underlying crypto compatibility issue. Android 17 and other devices keep
their guards. This remains a development port with permissive SELinux and
insecure ADB.

`work/port_compat.json` and `build_info/port_compat.json` record the selected
SDK, APEX hashes, policy mapping/hash and compatibility choices at build time.
They are host build records and do not collect device logs.

## Verification

```bash
python3 -m unittest discover -s tests -v
for script in port.sh requirements.sh scripts/actions_probe_stock.sh; do bash -n "$script"; done
```

The tests cover Android 16 metadata relocation, complete pangu layout, APEX
SDK rejection/source metadata, clean setup/ADB, wrapper removal and the
Android 17 path retaining BoringSSL guards. The bundled slider DEX JAR was
built from `devices/OnePlusAce3V/android-36/Ace3vHardware.java` with javac and
R8 D8 (`--min-api 34`); rebuilding it is optional when editing that helper.

On 2026-10-02, the real extracted peridot/vendor/cos16 sources completed
assembly and policy compilation. Comparison with the user-confirmed R3 tree
found **0 UID/GID/mode/capability/SELinux differences across 18,671 shared
active entries**. The compiled policy hash was identical:
`057d223d90fb23e75edbf0406e292ec850ee56d5146ecd7971a6b5826875a887`.
All 11 tests passed, including an actual EROFS inode round trip. The updated
flow has not yet been boot-tested as a newly packed ROM.
