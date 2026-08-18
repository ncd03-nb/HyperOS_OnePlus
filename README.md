# HyperOS port for the OnePlus 13

An auto-porter that builds a HyperOS ROM for the **OnePlus 13** (`dodge` /
PJZ110). It supports HyperOS **2, 3 and 4** as the donor. You give it two ROMs —
a OnePlus 13 stock package and a HyperOS package from a Xiaomi phone — and it
spits out a flashable, uncompressed zip with `system`, `system_ext`, `product`,
`vendor` and `odm` images.

It runs the same way locally or from GitHub Actions. There is no GUI and nothing
to click through; it downloads (or takes local files), unpacks the payloads,
does the porting steps, applies the fixes, repacks the erofs images and zips
them.

This porter targets the OnePlus 13 and nothing else. The geometry, panel values
and SELinux labels are specific to this phone. Do not point it at another device
and expect a boot.

## What comes from where

- **vendor** and **odm** are taken from the **OnePlus 13 stock ROM**. They stay
  OnePlus. HyperOS runs on top of the OnePlus vendor blobs, not Xiaomi's.
- **system**, **system_ext** and **product** come from the **HyperOS 4 ROM**,
  with `mi_ext` folded into `system` and `product`.

## Requirements

Linux (x86_64). The `requirements.sh` script installs what you need on Arch,
Debian, Ubuntu and Fedora, and grabs `payload-dumper-go`:

```
./requirements.sh
```

If `payload-dumper-go` can't be fetched, the porter falls back to a small
built-in Python payload extractor, so it still works offline.

## Running it locally

```
./requirements.sh
./port.sh --stock <oneplus13-stock> --hos4 <hyperos4-rom>
```

`--stock` and `--hos4` each accept:

- a direct download link,
- a local `.zip` (full OTA / fastboot / recovery package),
- a local `payload.bin`, or
- a directory that already holds the raw `.img` files.

The finished zip lands in `out/`. Useful flags:

```
--name <basename>     name for the output zip
--pixeldrain          upload the zip to pixeldrain (uses $PIXELDRAIN_API_KEY)
--out <dir>           output directory (default: out)
--work <dir>          working directory (default: work)
--keep-work           keep the working tree instead of cleaning it up
```

There are two interchangeable front-ends:

- `port.sh` — the Bash pipeline (does the downloading, unpacking, prop edits,
  packing and zipping in shell).
- `port.py` — the same pipeline in Python.

Both share the SELinux config generator (`lib/erofs_config.py`) and the payload
fallback (`lib/payload_extractor.py`). Use whichever you prefer.

## Running it from GitHub Actions

1. Fork this repo.
2. If you want the automatic pixeldrain upload, add your key under
   **Settings → Secrets and variables → Actions** as `PIXELDRAIN_API_KEY`.
3. Go to the **Actions** tab, pick **Build HyperOS 4 for OnePlus 13**, and hit
   **Run workflow**.
4. Paste the OnePlus 13 stock ROM link and the HyperOS 4 link.

The **Upload to pixeldrain** toggle is on by default. If you leave it on but
never added the secret, the build still finishes and the zip is attached as a
normal workflow artifact — it just skips the upload.

## What it fixes

A straight port of HyperOS 4 onto the OnePlus 13 boots with several things
broken. This porter bakes in the fixes for them:

- **Under-display fingerprint (FOD).** Out of the box there is no way to enrol a
  fingerprint (no FOD icon, no "add fingerprint"), and even once it shows,
  enrolment dies with `invalid cali data`. Fixed with the FOD geometry props,
  the enrolment gate (`vendor.fingerprint.cali=1`), the fingerprint permission
  xml labelled `vendor_configs_file`, and the SELinux property contexts that let
  system_server read the props and let the fingerprint HAL set its own.
- **120 Hz.** The display runs at 60 Hz until the refresh-rate block and the
  device-features flags are corrected.
- **Brightness curve and boot hang.** The Xiaomi brightness map starts above the
  OnePlus panel's minimum brightness, which makes the brightness spline blow up
  and the phone hangs on the boot animation. The bundled display config starts
  the map at the real panel minimum and uses the OnePlus 13's own calibration.
- **Status bar icon tint.** Status-bar icons get stuck at the wrong intensity
  without `debug.layered.strategy.phone=99`.
- **Camera.** The ported MiuiCamera is removed and replaced with a working
  build. That apk is too big for git, so the porter downloads it into `RES/`
  automatically (locally and on Actions) unless you already put it there.

### Known issues that are NOT fixed here

- **Face unlock enrolment freezes.** Fixing it needs edits to `Settings.apk`,
  which this porter does not do.
- **Always-on display (AOD).** Not addressed.

## The RES folder

`RES/` holds files that get copied over the assembled ROM at the end, replacing
whatever was there. The layout mirrors the partitions: `RES/product/...` copies
into `product`, `RES/vendor/...` copies into `vendor`, and so on — everything
inside a partition folder in `RES` is copied to that partition. After the copy,
the porter regenerates the SELinux `fs_config` and `file_contexts` entries for
the new files, inheriting each label from the nearest parent directory (which is
why a new file under `vendor/etc/permissions` correctly comes out as
`vendor_configs_file`).

If you want to change the camera, add a permission xml, swap the display config,
etc., drop the file into the matching path under `RES/` and it gets picked up.
No need to touch the scripts.

## The porting steps

For reference, this is what the porter does to assemble the ROM:

1. Fold `mi_ext/product` into `product` and `mi_ext/system` into
   `system/system`.
2. Append `mi_ext/etc/build.prop` to both `product/etc/build.prop` and
   `system/system/build.prop` (dropping the huge `ro.vendor.build.ab_ota_partitions`
   line from `mi_ext` once, up front, so it lands in neither).
3. Add `ro.miui.product.home=com.miui.home` and `pm.dexopt.shared=verify` to
   `system/system/build.prop`.
4. Tag `ro.mi.os.version.incremental` in `product/etc/build.prop`.
5. Move `product/pangu/system` into `system/system`.
6. Merge the Xiaomi vendor `build.prop` tail (the lines after `#end of file`,
   minus `ro.oplus.image.vendor.version`) into the OnePlus `vendor/build.prop`.
7. Add the Xiaomi attestation block to `odm/build.prop`.
8. Strip `import` lines from `odm/build.prop`.
9. Remove `ro.vendor.oplus.sensor.high_pwm_rgb`.
10. Add `persist.miui.density_v2=600` and `ro.sf.lcd_density=600` to
    `product/etc/build.prop`.
11. Delete `system_ext/priv-app/qcrilmsgtunnel`.
12. Delete `product/priv-app/MiuiCamera` (RES supplies the working one).

Then it applies the fixes above, regenerates the SELinux config, repacks each
partition with `mkfs.erofs`, and stores everything in an uncompressed zip.

## Credits

- **MIO Kitchen** — the erofs and image tools in `bin/`.
- **[payload-dumper-go](https://github.com/ssut/payload-dumper-go)** by ssut —
  payload.bin extraction (MIT).
- **[XMAPort](https://github.com/NorthStarK-Lvy/XMAPort)** and
  **[HyperOS-Port-Python](https://github.com/toraidl/HyperOS-Port-Python)** —
  references for the porting flow.

The fixes were worked out by palaziks. If you reuse them, keep the credit.

## License

GPLv3. See [LICENSE](LICENSE).
