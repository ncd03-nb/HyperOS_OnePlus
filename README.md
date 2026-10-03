# HyperOS port for OnePlus

An auto-porter that builds a HyperOS ROM for OnePlus phones. It supports
**HyperOS 2, 3 and 4** as the donor and combines it with a OnePlus stock ROM:

- `vendor` and `odm` come from the **OnePlus stock ROM**, so the hardware stack
  stays OnePlus.
- `system`, `system_ext` and `product` come from the **HyperOS donor**, with
  `mi_ext` merged in.

The output is an uncompressed zip containing `system.img`, `system_ext.img`,
`product.img`, `vendor.img` and `odm.img`.

It runs the same way locally or from GitHub Actions. The device-specific values
(FOD geometry, resolution, density, marketname, camera) live in `devices/`, so
adding a device is a config file, not a code change.

> **Disclaimer:** use this at your own risk. I am **not responsible** for any
> issues, data loss, or bricked phones that result from flashing these builds.
> Keep a working recovery/fastboot path and a backup before you flash.

## Supported Devices

| Device | Codename | Status |
|--------|----------|--------|
| **OnePlus 13** | `PJZ110` | ✅ Fully Supported |
| **OnePlus 15** | `PLK110` | ⚠️ Supported but untested |
| **OnePlus Ace 3V** | `PJF110` | HyperOS 3 / Android 16 boot confirmed via DSU; Android 17 uses the existing profile |

## Requirements

Linux x86_64.

```bash
./requirements.sh
```

This installs the dependencies on Arch, Debian, Ubuntu and Fedora and fetches
`payload-dumper-rust`. If it can't be fetched, the porter falls back to a
built-in Python payload extractor, so it still works offline.

## Local build

```bash
./port.sh --stock <oneplus-stock> --hyperos <hyperos-rom>
```

The target is detected from the OnePlus stock `vendor`/`odm` properties. A
verified folder under `devices/` is preferred. If none matches, the porter
creates an in-memory automatic profile from the stock model, codename, density,
market name and FOD props, then continues the build. `--device` remains an
optional manual override (for example, `OnePlus13`).
`--stock`, `--hyperos` and `--apex-stock` accept a URL, an OTA/fastboot/recovery
ZIP, `payload.bin`, a raw or Android sparse `super.img`, a directory of raw
images, or extracted trees with their `config/` metadata. ZIPs built by
`nothingsvn_xiaomi-toolbuild` are supported: the porter finds `super/super.img`
inside the archive and extracts only the requested logical partitions. Nested
raw images are also supported. Populated `_a`/`_b` names are normalized to
`system.img`, etc.; empty slots are skipped. Flashing scripts and firmware
images from the donor package are not executed or installed. The finished ZIP
lands in `out/`. Super extraction is built into Python and requires neither
`lpunpack` nor an intermediate full unsparse copy. Multi-device retrofit super
images are unsupported.

Google Drive public file share URLs are accepted directly, including the
large-file confirmation step:

```bash
./port.sh --stock /path/to/stock.zip \
  --hyperos 'https://drive.google.com/file/d/FILE_ID/view?usp=sharing'
```

Drive quota or permission errors stop the download; HTML error pages are never
saved as ROM ZIPs. There is no guaranteed anonymous quota bypass. Use a mirror,
wait for Drive's quota to reset, or make a permitted copy in your own Drive
account and share that file. See [Google's download guidance](https://support.google.com/drive/answer/2423534?hl=en).

For the toolbuild GitHub mirror, pass the direct download URL of its
`*.zip.download.json` asset. The porter streams the release parts in manifest
order into the original ZIP and checks every part and the full ZIP against
SHA256 before unpacking. No second ZIP archive is created:

```bash
./port.sh --stock /path/to/stock.zip \
  --hyperos 'https://github.com/OWNER/REPO/releases/download/TAG/ROM.zip.download.json'
```

Interrupted downloads retain a `.part` file for diagnosis. Use a fresh work
directory for the next attempt; these Python downloaders do not resume parts.

For Ace 3V HyperOS 3 Android 15 or 16, use a matching stock ROM containing
`system_ext`, or supply it separately with `--apex-stock`. The hardware
`vendor`/`odm` still come from `--stock`; the APEX source must match the
donor's Android SDK. See [the Android 15 enforcing flow](docs/ace3v-android15.md)
or [the Android 16 flow](docs/ace3v-android16.md) for the
inputs, extracted-tree build examples and remaining compatibility limits.

OPlus Android 16 links containing `downloadCheck` are resolved automatically
to their signed CDN URL before downloading, with the same retry behaviour as
the NothingsVN toolbuild flow.

Telegram `/bp4` uses `/bp4 <base ROM> -port <port ROM>`. For workflow-side
progress notifications, add `TELEGRAM_BOT_TOKEN` to this repository's Actions
secrets (the same token used by the existing bot). The workflow edits the bot's
pending message at download, unpack, build, pack and upload stages, then sends a
fresh completion/failure alert. `TELEGRAM_CHANNEL_ID` is optional for manual
workflow runs because bot-dispatched runs include the requesting user's ID.

Options:

```text
--device <name>       optional target-profile override; normally auto-detected
--name <basename>     output zip basename (default: HyperOS-<device>-port)
--out <dir>           output directory (default: out)
--work <dir>          working directory (default: work)
--res <dir>           overlay directory (default: RES)
--keep-work           keep the working tree instead of cleaning it up
--apex-stock <input>  separate Android-matched stock ROM/tree for Ace 3V SDK35/36 APEX
--adb-key <public>    optionally authorize one adbkey.pub for authenticated ADB builds
--force-adb           development ADB; SDK35 already uses no-auth ADB with UID2000
--assemble-only       assemble and compile policy without repacking images
```

`port.sh` is the entry point. It's mostly Bash, and calls small Python helpers
under `lib/` for the parts that are cleaner in Python — SELinux config synthesis
(`lib/erofs_config.py`), the payload fallback (`lib/payload_extractor.py`) and
the Google Drive download (`lib/gdrive.py`).

## GitHub Actions

1. Fork this repo.
2. For the automatic pixeldrain upload, add your key under **Settings → Secrets
   and variables → Actions** as `PIXELDRAIN_API_KEY`.
3. Open the **Actions** tab, pick **Build HyperOS for OnePlus**, and **Run
   workflow**.
4. Enter a device profile name, then paste the OnePlus stock ROM link and the
   HyperOS link. The profile must be a verified folder in `devices/`.

The finished zip is attached to the run as an artifact. **Upload to Google
Drive** is on by default: configure `GDRIVE_FOLDER_ID` and either
`GOOGLE_SERVICE_ACCOUNT_JSON` or `GOOGLE_OAUTH_CREDENTIALS_JSON` as Actions
secrets. A service-account Drive folder must be shared with that account. If an
upload fails, the GitHub artifact remains available. Pixeldrain remains an
optional alternative.

## Adding a device

Each device is a folder under `devices/<name>/`:

```text
devices/OnePlus13/
  device.conf                                 # scalar values (below)
  device_features.xml                         # copied to <ro.product.device>.xml
  displayconfig/display_id_<panelid>.xml      # brightness / refresh / density map
```

Start from `devices/_template/` to replace an automatic profile with a verified
one. Automatic builds intentionally do not stop when properties are missing:
they use conservative fallback display/FOD values and a donor feature file.
They are experimental and should be tested with recovery or fastboot available.

`device.conf`:

```ini
name=OnePlus 13
model=PJZ110
status=Fully Supported
density=600
miui_resolution=1440,3168,480
fod_location=628,2200
fod_size=184,184
fod_target=616,2388,824,2616
marketname=一加 13
camera_gdrive_id=<google drive id of that device's MiuiCamera.zip>
```

The porter overlays the folder's `displayconfig` and `device_features.xml` (the
latter renamed to the port's detected `ro.product.device`), then writes the
scalar values into the right build.props (FOD, density and resolution) and the
odm attestation (marketname). To add a device, copy an existing folder and drop
in that device's **verified** values — FOD coordinates and the displayconfig are
panel-specific, and the displayconfig filename must be that device's real panel
id (read it from a dump). Reusing another device's values is what "untested"
means.

## What it fixes

A straight port of HyperOS onto a OnePlus boots with several things broken. This
porter bakes in the fixes:

- **Under-display fingerprint (FOD).** Adds the FOD geometry props, the
  enrolment gate (`vendor.fingerprint.cali=1`), the fingerprint permission xml
  labelled `vendor_configs_file`, and the SELinux property contexts so
  system_server can read the props and the fingerprint HAL can set its own.
  Without these there's no way to enrol, or enrolment dies with
  `invalid cali data`.
- **120 Hz.** Vendor display props plus the product refresh-rate config and
  device-features flags.
- **Brightness curve and boot hang.** A brightness map that starts above the
  panel minimum makes the brightness spline blow up and the phone hang on the
  boot animation. The bundled display config starts at the real panel minimum.
- **Status bar icon tint.** `debug.layered.strategy.phone=99`.
- **Camera.** The ported MiuiCamera is replaced with a working build, which the
  porter downloads into `RES/` automatically (it's too big for git).

Not fixed here: face-unlock enrolment freeze (needs `Settings.apk` edits) and
slightly buggy fullscreen AOD.

## Porting flow

1. Extract OnePlus `vendor` and `odm` from the stock ROM.
2. Extract HyperOS `system`, `system_ext`, `product` and `mi_ext`.
3. Fold `mi_ext/product`, `mi_ext/system_ext` and `mi_ext/system` into
   their target partitions, retaining their source metadata.
4. Merge `mi_ext/etc/build.prop` into `product` and `system/system`, dropping
   the huge `ro.vendor.build.ab_ota_partitions` line.
5. Add MIUI home/dexopt props to `system/system/build.prop`.
6. Tag `ro.mi.os.version.incremental`.
7. Move `product/pangu/system` into `system/system`.
8. Append the OnePlus vendor props (the `# end of file` block + FOD) to
   `vendor/build.prop`.
9. Add the Xiaomi attestation block to `odm/build.prop` and strip `import`
   lines.
10. Remove `ro.vendor.oplus.sensor.high_pwm_rgb`.
11. Add the density and status-bar props to `product/etc/build.prop`.
12. Delete `system_ext/priv-app/qcrilmsgtunnel` and the ported
    `product/priv-app/MiuiCamera`.

Then it applies the `RES/` overlays and the device overrides, regenerates the
EROFS `fs_config` / `file_contexts`, repacks each partition, and writes the zip.
Ace 3V SDK36 additionally moves the remaining `mi_ext` into the system image,
moves the complete `pangu` directory, replaces `system_ext` APEX from matching
stock, compiles the compatibility policy, and applies the setup flow. ADB remains
enabled at boot but uses authentication and drops to UID 2000; SELinux stays
globally enforcing. Insecure/root ADB is available only through `--force-adb`.
These changes are selected by donor SDK, not the ROM name.

Ace 3V SDK35 removes SetupWizard and enables ADB without host authentication
at boot, including bootanimation. It retains `ro.secure=1`, `ro.debuggable=0`,
shell UID2000 and SELinux Enforcing. The signed adbd APEX is unchanged; the
tested daemon is bound into its namespace after APEX activation. The no-auth
profile accepts only the recorded donor APEX hash and fails on unsupported
versions. See [Android 15 boot ADB](devices/OnePlusAce3V/android-35/boot_adb/README.md).

## RES overlays

`RES/` holds files copied over the assembled tree, mirroring the partition
layout — `RES/product/...` goes into `product`, `RES/vendor/...` into `vendor`.
After the copy, the SELinux metadata is regenerated for the new files (a file
under `vendor/etc/permissions` correctly becomes `vendor_configs_file`). Line-
based edits (build.prop props, SELinux property contexts) live in `fixes/` and
are shared by both front-ends.

## Credits

- **MIO Kitchen** — the erofs and image tools in `bin/`.
- **[payload-dumper-rust](https://github.com/rhythmcache/payload-dumper-rust)**
  — payload extraction (reads the ROM zip directly).
- **[XMAPort](https://github.com/NorthStarK-Lvy/XMAPort)** and
  **[HyperOS-Port-Python](https://github.com/toraidl/HyperOS-Port-Python)** —
  references for the porting flow.
- **[tqmane](https://github.com/tqmane)** — for some help.

Fixes worked out by palaziks. If you reuse them, keep the credit.

## License

GPLv3. See [LICENSE](LICENSE).
