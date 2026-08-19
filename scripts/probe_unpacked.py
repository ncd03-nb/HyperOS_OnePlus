#!/usr/bin/env python3
"""Extract HyperOS-porting metadata from an unpacked OnePlus/OPPO stock ROM.

Only values backed by ROM text/XML are auto-filled. Runtime-only values such as
an active SurfaceFlinger display ID or FOD coordinates are never guessed.
"""
from __future__ import annotations

import argparse, json, os, re, shlex, sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

PARTS = ("system", "system_ext", "product", "my_product", "vendor", "odm", "vendor_dlkm", "odm_dlkm")
RATES = {30, 48, 50, 60, 72, 90, 96, 120, 144, 165}


def text(p: Path, limit=8 * 1024 * 1024):
    try:
        return "" if p.stat().st_size > limit else p.read_text("utf-8", errors="ignore")
    except OSError:
        return ""


def rel(p: Path, root: Path):
    try: return str(p.relative_to(root)).replace(os.sep, "/")
    except ValueError: return str(p)


def roots(root: Path):
    out, seen = [root], {root.resolve()}
    for n in PARTS:
        p = root / n
        if p.is_dir() and p.resolve() not in seen:
            out.append(p); seen.add(p.resolve())
    return out


def prop_db(root: Path):
    db = defaultdict(list); seen = set()
    for base in roots(root):
        for pat in ("*.prop", "build.prop"):
            for p in base.rglob(pat):
                if not p.is_file() or p in seen: continue
                seen.add(p)
                for raw in text(p, 2 * 1024 * 1024).splitlines():
                    s = raw.strip()
                    if not s or s.startswith("#") or "=" not in s: continue
                    k, v = s.split("=", 1); k, v = k.strip(), v.strip().strip("\r")
                    if k and v: db[k].append((v, p))
    return db, sorted(seen)


def first(db, root, keys):
    for k in keys:
        if db.get(k):
            v, p = db[k][0]
            return {"value": v, "source": f"{rel(p, root)}:{k}", "confidence": "high"}
    return None


def as_int(item):
    if not item: return None
    m = re.search(r"\d+", str(item["value"]))
    if not m: return None
    x = dict(item); x["value"] = int(m.group()); return x


def xmls(root, needles):
    out, seen = [], set()
    for base in roots(root):
        for p in base.rglob("*.xml"):
            rp = rel(p, root).lower()
            if p not in seen and any(x in rp for x in needles): out.append(p); seen.add(p)
    return out


def pair(v):
    m = re.fullmatch(r"\s*(\d{3,5})\s*[,xX]\s*(\d{3,5})(?:\s*,\s*\d+)?\s*", v)
    return [int(m.group(1)), int(m.group(2))] if m else None


def resolution(root, db):
    for k in ("persist.sys.miui_resolution", "ro.vendor.display.miui_resolution", "ro.oplus.display.wm_size", "ro.vendor.display.resolution"):
        for v, p in db.get(k, []):
            q = pair(v)
            if q: return {"value": q, "source": f"{rel(p, root)}:{k}", "confidence": "high"}, []
    candidates = []
    for p in xmls(root, ("displayconfig", "display_id_")):
        try: node = ET.fromstring(text(p))
        except ET.ParseError: continue
        rows = []
        for d in node.findall(".//density"):
            try:
                w, h = int((d.findtext("width") or "0").strip()), int((d.findtext("height") or "0").strip())
                dpi = int(float((d.findtext("density") or "0").strip()))
            except ValueError: continue
            if w >= 300 and h >= 300: rows.append((w, h, dpi))
        if rows:
            w, h, dpi = max(rows, key=lambda z: z[0] * z[1])
            candidates.append({"width": w, "height": h, "density": dpi, "source": rel(p, root)})
    geoms = {(x["width"], x["height"]) for x in candidates}
    if len(geoms) == 1:
        w, h = next(iter(geoms))
        return {"value": [w, h], "source": "unique native displayconfig geometry", "confidence": "medium"}, candidates
    return None, candidates


def display_ids(root):
    out = []
    for p in xmls(root, ("display_id_",)):
        m = re.search(r"display_id_(\d+)\.xml$", p.name)
        if m: out.append({"id": m.group(1), "source": rel(p, root)})
    uniq = {(x["id"], x["source"]): x for x in out}
    return sorted(uniq.values(), key=lambda x: (x["id"], x["source"]))


def refresh(root):
    found = defaultdict(set)
    for p in xmls(root, ("refresh", "displayconfig", "display_id_")):
        s = text(p)
        for m in re.finditer(r"(?i)(?:refresh(?:rate)?|fps|hz)[^0-9]{0,30}(30|48|50|60|72|90|96|120|144|165)", s):
            r = int(m.group(1)); found[r].add(rel(p, root))
        if "refresh_rate" in p.name.lower():
            for m in re.finditer(r"(?<!\d)(30|48|50|60|72|90|96|120|144|165)(?!\d)", s):
                r = int(m.group(1));
                if r in RATES: found[r].add(rel(p, root))
    return [{"hz": r, "sources": sorted(src)} for r, src in sorted(found.items())]


def features(root):
    items = []
    for p in xmls(root, ("permissions",)):
        s = text(p, 2 * 1024 * 1024)
        if s: items.append((p, s))
    low = "\n".join(s for _, s in items).lower()
    def src(q): return next((rel(p, root) for p, s in items if q in s.lower()), None)
    fod = None
    if "fingerprint.optical" in low: fod = {"value": "optical", "source": src("fingerprint.optical"), "confidence": "high"}
    elif "fingerprint.ultrasonic" in low or "ultrasonic.fingerprint" in low:
        fod = {"value": "ultrasonic", "source": src("ultrasonic"), "confidence": "high"}
    return {
        "fod_type": fod,
        "nfc": {"value": "android.hardware.nfc" in low, "source": src("android.hardware.nfc"), "confidence": "high"},
        "ir": {"value": "android.hardware.consumerir" in low, "source": src("android.hardware.consumerir"), "confidence": "high"},
    }


def fod_geometry(root, db):
    keys = {
        "location": ("persist.vendor.sys.fp.fod.location.X_Y", "persist.vendor.sys.fp.fod.location"),
        "size": ("persist.vendor.sys.fp.fod.size.width_height", "persist.vendor.sys.fp.fod.size"),
        "target": ("persist.vendor.sys.fp.fod.us.target", "persist.vendor.sys.fp.fod.target"),
    }
    out = {k: first(db, root, v) for k, v in keys.items()}
    for kind, token in (("location", "location"), ("size", "size"), ("target", "target")):
        if out[kind]: continue
        for k, vals in db.items():
            if "fod" in k.lower() and token in k.lower() and vals:
                v, p = vals[0]; out[kind] = {"value": v, "source": f"{rel(p, root)}:{k}", "confidence": "medium"}; break
    out["note"] = None if out["location"] and out["size"] else "Runtime/SurfaceFlinger capture required; no guessed FOD geometry was generated."
    return out


def brightness(root):
    tables = []
    for p in xmls(root, ("display_brightness_config",)):
        s = text(p)
        if "brightness_table" not in s: continue
        try: node = ET.fromstring(s)
        except ET.ParseError: continue
        t = node.find(".//brightness_table")
        if t is None: continue
        rows = []
        for e in t.findall(".//level"):
            a = [x.strip() for x in (e.text or "").split(",")]
            try:
                if len(a) >= 3: rows.append((int(float(a[0])), float(a[2])))
            except ValueError: pass
        if not rows: continue
        rows.sort(); declared = int(t.attrib.get("max", "0") or 0)
        normal = min(rows, key=lambda x: abs(x[0] - declared)) if declared else rows[-1]
        lm, hm = node.findtext(".//lux_table_mode"), node.findtext(".//hbm_lux_table_mode")
        tables.append({"source": rel(p, root), "rows": len(rows), "normal_max_level": declared or None,
                       "normal_max_nits": normal[1] if declared else None, "absolute_max_level": rows[-1][0],
                       "absolute_max_nits": rows[-1][1], "lux_table_mode": int(lm) if lm and lm.strip().isdigit() else None,
                       "hbm_lux_table_mode": int(hm) if hm and hm.strip().isdigit() else None})
    tables.sort(key=lambda x: (-x["rows"], x["source"]))
    selected = tables[0] if tables else None
    hbm = None
    if selected and selected["hbm_lux_table_mode"] is not None:
        mode = selected["hbm_lux_table_mode"]
        block = re.compile(rf'<hbm_lux_table\s+id=["\']{mode}["\'][^>]*>(.*?)</hbm_lux_table>', re.I | re.S)
        lux = re.compile(r'<lux\s+[^>]*enter=["\']([^"\']+)["\'][^>]*exit=["\']([^"\']+)["\'][^>]*>(.*?)</lux>', re.I | re.S)
        for p in xmls(root, ("display_brightness_config",)):
            m = block.search(text(p))
            if not m: continue
            entries = [{"enter_lux": float(x.group(1)), "exit_lux": float(x.group(2)), "payload": re.sub(r"\s+", "", x.group(3))} for x in lux.finditer(m.group(1))]
            if entries: hbm = {"mode": mode, "source": rel(p, root), "entries": entries}; break
    return {"tables": tables, "selected": selected, "hbm": hbm,
            "supports_12bit_backlight": bool(selected and selected["normal_max_level"] == 4095)}


def battery(root):
    for p in xmls(root, ("power_profile",)):
        m = re.search(r'<item\s+name=["\']battery\.capacity["\']\s*>\s*([0-9.]+)', text(p), re.I)
        if m: return {"value": int(float(m.group(1))), "source": rel(p, root), "confidence": "high"}
    return None


def probe(root):
    db, prop_files = prop_db(root)
    get = lambda ks: first(db, root, ks)
    model = get(("ro.product.vendor.model", "ro.product.odm.model", "ro.product.model"))
    device = get(("ro.product.vendor.device", "ro.product.odm.device", "ro.build.product", "ro.product.device"))
    market = get(("ro.vendor.oplus.market.name", "ro.product.odm.marketname", "ro.product.marketname"))
    density = as_int(get(("ro.sf.lcd_density", "ro.oplus.density.qhd_default", "persist.vendor.display.lcd_density", "ro.oplus.density.fhd_default")))
    fhd = as_int(get(("ro.oplus.density.fhd_default",)))
    res, candidates = resolution(root, db); ids = display_ids(root); fg = fod_geometry(root, db); ft = features(root); br = brightness(root)
    unresolved = []
    if not res: unresolved.append("native display resolution")
    if len(ids) != 1: unresolved.append("active physical display ID (runtime SurfaceFlinger value)")
    if not fg["location"] or not fg["size"]: unresolved.append("FOD geometry")
    return {
        "root": str(root),
        "identity": {"model": model, "device": device, "market_name": market,
                     "soc": get(("ro.soc.model", "ro.board.platform", "ro.hardware")),
                     "project": get(("ro.boot.prjname", "ro.vendor.oplus.project", "ro.boot.project_name"))},
        "display": {"density": density, "fhd_default_density": fhd, "resolution": res,
                    "resolution_candidates": candidates, "refresh_rates": refresh(root),
                    "display_id_candidates": ids, "active_display_id": ids[0] if len(ids) == 1 else None},
        "fingerprint": {"type": ft["fod_type"], "geometry": fg},
        "features": {"nfc": ft["nfc"], "ir": ft["ir"]},
        "brightness": br, "battery_capacity_mah": battery(root),
        "scan": {"property_files": [rel(p, root) for p in prop_files], "property_count": len(db)},
        "unresolved": unresolved,
    }


def value(p, *keys):
    for k in keys:
        if not isinstance(p, dict): return None
        p = p.get(k)
    return p.get("value") if isinstance(p, dict) and "value" in p else p


def make_conf(p):
    model, dev = value(p, "identity", "model") or "UNKNOWN", value(p, "identity", "device") or "UNKNOWN"
    market = value(p, "identity", "market_name") or model; dens = value(p, "display", "density")
    res = value(p, "display", "resolution"); fhd = value(p, "display", "fhd_default_density") or 480
    loc, size, target = value(p, "fingerprint", "geometry", "location"), value(p, "fingerprint", "geometry", "size"), value(p, "fingerprint", "geometry", "target")
    lines = ["# Generated by scripts/probe_unpacked.py", "# Review unresolved fields before marking this profile verified.",
             f"name={market}", f"model={model}", f"match_models={model}" if model != "UNKNOWN" else "# match_models=UNKNOWN",
             f"match_devices={dev}" if dev != "UNKNOWN" else "# match_devices=UNKNOWN", "status=Experimental",
             f"density={dens}" if dens else "# density=UNKNOWN",
             f"miui_resolution={res[0]},{res[1]},{fhd}" if isinstance(res, list) and len(res) == 2 else "# miui_resolution=WIDTH,HEIGHT,480  # unresolved",
             f"fod_location={loc}" if loc else "# fod_location=X,Y  # runtime/unresolved",
             f"fod_size={size}" if size else "# fod_size=WIDTH,HEIGHT  # runtime/unresolved",
             f"fod_target={target}" if target else "# fod_target=LEFT,TOP,RIGHT,BOTTOM  # runtime/unresolved",
             f"marketname={market}", "camera_gdrive_id=", ""]
    return "\n".join(lines)


def report(p):
    v = lambda *k: value(p, *k) if value(p, *k) not in (None, "") else "UNKNOWN"
    res = value(p, "display", "resolution"); rs = f"{res[0]}x{res[1]}" if isinstance(res, list) else "UNKNOWN"
    rates = ", ".join(str(x["hz"]) for x in p["display"]["refresh_rates"]); ids = ", ".join(x["id"] for x in p["display"]["display_id_candidates"])
    b, h = p["brightness"]["selected"], p["brightness"]["hbm"]
    lines = ["OnePlus/OPPO unpacked ROM probe", "================================", f"Root: {p['root']}", "",
             f"Model: {v('identity','model')}", f"Device: {v('identity','device')}", f"Market: {v('identity','market_name')}",
             f"SoC: {v('identity','soc')}", f"Project: {v('identity','project')}", "", f"Resolution: {rs}", f"Density: {v('display','density')}",
             f"Refresh: {rates + ' Hz' if rates else 'UNKNOWN'}", f"Display IDs: {ids or 'none found'}", f"FOD type: {v('fingerprint','type')}",
             f"FOD location: {v('fingerprint','geometry','location')}", f"FOD size: {v('fingerprint','geometry','size')}", f"FOD target: {v('fingerprint','geometry','target')}",
             f"NFC: {p['features']['nfc']['value']}", f"IR: {p['features']['ir']['value']}"]
    if b: lines += ["", f"Brightness: {b['source']}", f"Normal max: {b['normal_max_level']} / {b['normal_max_nits']} nits", f"Absolute max: {b['absolute_max_level']} / {b['absolute_max_nits']} nits", f"Lux/HBM mode: {b['lux_table_mode']} / {b['hbm_lux_table_mode']}"]
    if h and h["entries"]: lines += [f"HBM first enter/exit: {h['entries'][0]['enter_lux']} / {h['entries'][0]['exit_lux']} lux"]
    if p["unresolved"]: lines += ["", "Still unresolved (not guessed):"] + [f"  - {x}" for x in p["unresolved"]]
    return "\n".join(lines) + "\n"


def shell(p):
    fields = {"PROBE_MODEL": value(p,"identity","model"), "PROBE_DEVICE": value(p,"identity","device"), "PROBE_MARKET_NAME": value(p,"identity","market_name"),
              "PROBE_SOC": value(p,"identity","soc"), "PROBE_PROJECT": value(p,"identity","project"), "PROBE_DENSITY": value(p,"display","density"),
              "PROBE_FOD_TYPE": value(p,"fingerprint","type"), "PROBE_FOD_LOCATION": value(p,"fingerprint","geometry","location"),
              "PROBE_FOD_SIZE": value(p,"fingerprint","geometry","size"), "PROBE_FOD_TARGET": value(p,"fingerprint","geometry","target")}
    r = value(p,"display","resolution")
    if isinstance(r, list): fields.update(PROBE_WIDTH=r[0], PROBE_HEIGHT=r[1])
    fields["PROBE_REFRESH_RATES"] = ",".join(str(x["hz"]) for x in p["display"]["refresh_rates"])
    ids = p["display"]["display_id_candidates"]; fields["PROBE_DISPLAY_ID"] = ids[0]["id"] if len(ids) == 1 else None
    return "\n".join(f"{k}={shlex.quote('' if x is None else str(x))}" for k,x in fields.items()) + "\n"


def main():
    a = argparse.ArgumentParser(); a.add_argument("root"); a.add_argument("--out-dir"); a.add_argument("--shell", action="store_true"); a.add_argument("--json", action="store_true"); args = a.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir(): print(f"ERROR: unpacked ROM directory not found: {root}", file=sys.stderr); return 2
    p = probe(root); out = Path(args.out_dir).expanduser().resolve() if args.out_dir else root / "_hyperos_probe"; out.mkdir(parents=True, exist_ok=True)
    (out/"probe.json").write_text(json.dumps(p, indent=2, ensure_ascii=False)+"\n", "utf-8"); (out/"probe_report.txt").write_text(report(p), "utf-8"); (out/"device.conf.generated").write_text(make_conf(p), "utf-8")
    sys.stdout.write(shell(p) if args.shell else (json.dumps(p, indent=2, ensure_ascii=False)+"\n" if args.json else report(p)))
    if not args.shell and not args.json: print(f"Wrote: {out}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
