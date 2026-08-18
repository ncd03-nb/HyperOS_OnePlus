# HyperOS port for the OnePlus 13

An auto-porter that builds a HyperOS ROM for the **OnePlus 13** (`dodge` /
PJZ110). It supports **HyperOS 2, 3 and 4** as the donor and combines it with a
OnePlus 13 stock ROM:

- `vendor` and `odm` come from the **OnePlus 13 stock ROM**, so the hardware
  stack stays OnePlus.
- `system`, `system_ext` and `product` come from the **HyperOS donor**, with
  `mi_ext` merged in.

The output is an uncompressed zip containing `system.img`, `system_ext.img`,
`product.img`, `vendor.img` and `odm.img`.

It runs the same way locally or from GitHub Actions, and this porter targets the
OnePlus 13 only — the FOD coordinates, display config and vendor props are
specific to this device.

> Keep a working recovery/fastboot path before flashing. This is a porting base,
> not a guarantee every donor boots unchanged.

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
./port.sh --stock <oneplus13-stock> --hyperos <hyperos-rom>
```

Both inputs accept a URL, an OTA/fastboot/recovery zip, a `payload.bin`, or a
directory of raw `.img` files. The finished zip lands in `out/`.

Options:

```text
--name <basename>     output zip basename (default: HyperOS-OnePlus13-port)
--out <dir>           output directory (default: out)
--work <dir>          working directory (default: work)
--res <dir>           overlay directory (default: RES)
--keep-work           keep the working tree instead of cleaning it up
```

There are two interchangeable front-ends — `port.sh` (Bash) and `port.py`
(Python). They do the same job and share the SELinux config generator
(`lib/erofs_config.py`) and the payload fallback (`lib/payload_extractor.py`).

## GitHub Actions

1. Fork this repo.
2. For the automatic pixeldrain upload, add your key under **Settings → Secrets
   and variables → Actions** as `PIXELDRAIN_API_KEY`.
3. Open the **Actions** tab, pick **Build HyperOS for OnePlus 13**, and **Run
   workflow**.
4. Paste the OnePlus 13 stock ROM link and the HyperOS link.

The finished zip is attached to the run as an artifact. The **Upload to
pixeldrain** toggle (on by default) also uploads it and prints a share link; if
the secret is missing or the upload fails, the build still succeeds.

## What it fixes

A straight port of HyperOS onto the OnePlus 13 boots with several things broken.
This porter bakes in the fixes:

- **Under-display fingerprint (FOD).** Adds the FOD geometry props, the
  enrolment gate (`vendor.fingerprint.cali=1`), the fingerprint permission xml
  labelled `vendor_configs_file`, and the SELinux property contexts so
  system_server can read the props and the fingerprint HAL can set its own.
  Without these there's no way to enrol, or enrolment dies with
  `invalid cali data`.
- **120 Hz.** Vendor display props plus the product refresh-rate config and
  device-features flags.
- **Brightness curve and boot hang.** The Xiaomi brightness map starts above the
  OnePlus panel minimum, which makes the brightness spline blow up and the phone
  hang on the boot animation. The bundled display config starts at the real
  panel minimum with the OnePlus 13's calibration.
- **Status bar icon tint.** `debug.layered.strategy.phone=99`.
- **Camera.** The ported MiuiCamera is replaced with a working build, which the
  porter downloads into `RES/` automatically (it's too big for git).

Not fixed here: face-unlock enrolment freeze (needs `Settings.apk` edits) and
slightly buggy fullscreen AOD.

## Porting flow

1. Extract OnePlus 13 `vendor` and `odm` from the stock ROM.
2. Extract HyperOS `system`, `system_ext`, `product` and `mi_ext`.
3. Fold `mi_ext/product` into `product` and `mi_ext/system` into
   `system/system`.
4. Merge `mi_ext/etc/build.prop` into `product` and `system/system`, dropping
   the huge `ro.vendor.build.ab_ota_partitions` line.
5. Add MIUI home/dexopt props to `system/system/build.prop`.
6. Tag `ro.mi.os.version.incremental`.
7. Move `product/pangu/system` into `system/system`.
8. Append the OnePlus 13 vendor props (the `# end of file` block + FOD) to
   `vendor/build.prop`.
9. Add the Xiaomi attestation block to `odm/build.prop` and strip `import`
   lines.
10. Remove `ro.vendor.oplus.sensor.high_pwm_rgb`.
11. Add `persist.miui.density_v2=600` / `ro.sf.lcd_density=600` and the status
    bar prop to `product/etc/build.prop`.
12. Delete `system_ext/priv-app/qcrilmsgtunnel` and the ported
    `product/priv-app/MiuiCamera`.

Then it applies the `RES/` overlays, regenerates the EROFS `fs_config` /
`file_contexts`, repacks each partition, and writes the zip.

## RES overlays

`RES/` holds files copied over the assembled tree, mirroring the partition
layout — `RES/product/...` goes into `product`, `RES/vendor/...` into `vendor`,
and so on. After the copy, the SELinux metadata is regenerated for the new files
(a file under `vendor/etc/permissions` correctly becomes `vendor_configs_file`).
Drop a file into the matching path to add or replace it without touching the
scripts.

Line-based edits (build.prop props, SELinux property contexts) live in `fixes/`
and are shared by both front-ends.

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
