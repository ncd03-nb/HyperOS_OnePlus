# Unpacked stock ROM probe

The repository includes a conservative stock-ROM metadata probe for creating or validating OnePlus device profiles.

## Windows PowerShell

```powershell
.\probe_unpacked.ps1 "D:\MIO Kitchen"
```

Optional output directory:

```powershell
.\probe_unpacked.ps1 "D:\MIO Kitchen" -OutDir "D:\MIO Kitchen\probe"
```

## Linux / GitHub runner

```bash
python3 scripts/probe_unpacked.py /path/to/unpacked-rom
```

The input directory may contain any of these unpacked partition trees: `system`, `system_ext`, `product`, `my_product`, `vendor`, `odm`, `vendor_dlkm`, and `odm_dlkm`.

The probe writes `<ROM>/_hyperos_probe/` by default:

- `probe.json` — machine-readable values, source paths and confidence.
- `probe_report.txt` — compact human-readable report.
- `device.conf.generated` — best-effort device profile with unresolved fields commented instead of guessed.

It currently extracts model/device/market name, SoC/project properties, density, native-resolution candidates, refresh rates, stock `display_id_*.xml` candidates, optical/ultrasonic FOD type, FOD geometry when properties exist, NFC/IR features, brightness/HBM tables, 12-bit backlight indication, and battery capacity when a power profile exposes it.

## Runtime-only data

Do not infer missing values just to complete a profile. In particular, the active physical display ID and FOD geometry can be runtime/HAL values on OPlus devices. If the probe lists them under `unresolved`, capture them from a booted stock ColorOS build (for example with SurfaceFlinger) and merge them into the generated profile.

For shell integration, use:

```bash
python3 scripts/probe_unpacked.py /path/to/unpacked-rom --shell
```

This emits shell-safe `PROBE_*` variables without guessing unresolved values.
