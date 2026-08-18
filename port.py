#!/usr/bin/env python3
# HyperOS (2-4) -> OnePlus auto-porter (multi-device; see devices/ and README).
# vendor+odm from the OnePlus stock ROM, system+system_ext+product from HyperOS.

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
import payload_extractor  # noqa: E402  (built-in fallback dumper)
import gdrive  # noqa: E402
from erofs_config import sync_config, set_context  # noqa: E402

# partitions we take from each source ROM
FROM_HOS4 = ["system", "system_ext", "product", "mi_ext"]
FROM_STOCK = ["vendor", "odm"]
# partitions we pack into the final zip
PACK = ["system", "system_ext", "product", "vendor", "odm"]

EROFS_BIN = os.path.join(HERE, "bin", "Linux", "x86_64")
MKFS = os.path.join(EROFS_BIN, "mkfs.erofs")
EXTRACT = os.path.join(EROFS_BIN, "extract.erofs")


def log(msg):
    print(msg, flush=True)


def die(msg):
    log("ERROR: " + msg)
    sys.exit(1)


def run(cmd, quiet=False, **kw):
    log("+ " + " ".join(str(c) for c in cmd))
    if quiet:
        # swallow the tool's own output, but surface it if the command fails
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        if r.returncode != 0:
            sys.stdout.write(r.stdout.decode("utf-8", "replace"))
            raise subprocess.CalledProcessError(r.returncode, cmd)
    else:
        subprocess.run(cmd, check=True, **kw)


# input resolution: URL -> local path (zip / payload.bin / directory, as-is)
def resolve_input(src, dst_dir, label):
    os.makedirs(dst_dir, exist_ok=True)
    if src.startswith("http://") or src.startswith("https://"):
        return download(src, dst_dir, label)
    return src


def download(url, dst_dir, label):
    out = os.path.join(dst_dir, label + "_download")
    # keep the real extension so later logic can tell zip from bin
    if url.lower().split("?")[0].endswith(".zip"):
        out += ".zip"
    elif url.lower().split("?")[0].endswith(".bin"):
        out += ".bin"
    else:
        out += ".zip"  # ROM links are almost always zips
    log("downloading %s ROM: %s" % (label, url))
    if shutil.which("aria2c"):
        run(["aria2c", "-x", "16", "-s", "16", "-o", os.path.basename(out),
             "-d", dst_dir, url])
    elif shutil.which("curl"):
        run(["curl", "-L", "--fail", "-o", out, url])
    elif shutil.which("wget"):
        run(["wget", "-O", out, url])
    else:
        die("no downloader found (need aria2c, curl or wget)")
    return out


def unzip_find_payload(zip_path, dst_dir, label):
    """Extract payload.bin from a ROM zip. If the zip has no payload.bin but
    already contains raw partition images, extract those instead."""
    extract_to = os.path.join(dst_dir, label + "_zip")
    os.makedirs(extract_to, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        payload = next((n for n in names if os.path.basename(n) == "payload.bin"), None)
        if payload:
            log("found %s in zip" % payload)
            z.extract(payload, extract_to)
            return os.path.join(extract_to, payload)
        # fall back: pull out any *.img we recognise
        imgs = [n for n in names if n.lower().endswith(".img")]
        if not imgs:
            die("%s zip has neither payload.bin nor *.img files" % label)
        for n in imgs:
            z.extract(n, extract_to)
        return extract_to  # a dir of images


def _find_dumper():
    tool = shutil.which("payload_dumper") or shutil.which("payload-dumper-rust")
    bundled = os.path.join(EROFS_BIN, "payload_dumper")
    if not tool and os.path.exists(bundled):
        tool = bundled
    return tool


# zip / payload.bin -> raw *.img (rust reads either directly; else built-in)
def dump_payload(inp, out_dir, wanted, label):
    os.makedirs(out_dir, exist_ok=True)
    tool = _find_dumper()
    if tool:
        log("extracting %s from %s with payload-dumper-rust"
            % (",".join(wanted), os.path.basename(inp)))
        run([tool, inp, "-o", out_dir, "-i", ",".join(wanted)], quiet=True)
        return {p: os.path.join(out_dir, p + ".img") for p in wanted}
    # built-in fallback needs a payload.bin; unzip it out of a ROM zip first
    payload = inp
    if inp.lower().endswith(".zip"):
        payload = unzip_find_payload(inp, out_dir, label)
        if os.path.isdir(payload):  # zip carried raw *.img, not a payload
            return _imgs_from_dir(payload, wanted, label)
    log("extracting %s with built-in extractor" % ",".join(wanted))
    payload_extractor.extract(payload, out_dir, wanted, log=lambda *_a: None)
    return {p: os.path.join(out_dir, p + ".img") for p in wanted}


def _imgs_from_dir(d, wanted, label):
    found = {p: os.path.join(d, p + ".img") for p in wanted
             if os.path.exists(os.path.join(d, p + ".img"))}
    missing = [p for p in wanted if p not in found]
    if missing:
        die("%s dir missing images: %s" % (label, ", ".join(missing)))
    return found


def get_images(resolved, out_dir, wanted, label):
    if os.path.isdir(resolved):
        return _imgs_from_dir(resolved, wanted, label)
    return dump_payload(resolved, out_dir, wanted, label)


# erofs image  ->  files + config/<part>_{file_contexts,fs_config,fs_options}
def unpack_erofs(img, work):
    log("unpacking %s" % os.path.basename(img))
    run([EXTRACT, "-i", img, "-x", "-s", "-f", "-o", work], quiet=True)


# small build.prop / text helpers
def read_lines(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def prop_append(path, block, header=None):
    """Append prop lines that are not already present (idempotent)."""
    lines = read_lines(path) if os.path.exists(path) else []
    existing = set(l.strip() for l in lines)
    add = [l for l in block if l.strip() and l.strip() not in existing]
    if not add:
        return
    if header:
        lines.append("")
        lines.append(header)
    lines.extend(add)
    write_lines(path, lines)


def prop_set(path, key, value):
    """Replace key=... with key=value (append if absent). No-op if value None."""
    if value is None or not os.path.exists(path):
        return
    lines = read_lines(path)
    for i, l in enumerate(lines):
        if l.startswith(key + "="):
            lines[i] = key + "=" + value
            break
    else:
        lines.append(key + "=" + value)
    write_lines(path, lines)


def prop_remove(path, predicate):
    if not os.path.exists(path):
        return
    lines = read_lines(path)
    kept = [l for l in lines if not predicate(l)]
    if len(kept) != len(lines):
        write_lines(path, kept)


def move_tree(src, dst):
    """Merge-move src/* into dst/ (dst wins only where src is absent)."""
    if not os.path.isdir(src):
        return
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s):
            move_tree(s, d)
        else:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.move(s, d)
    shutil.rmtree(src, ignore_errors=True)


def copy_tree(src, dst):
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target, exist_ok=True)
        for fname in files:
            shutil.copy2(os.path.join(root, fname), os.path.join(target, fname))


# the 12-step assembly + fixes
FIXES_DIR = os.path.join(HERE, "fixes")
SIGNATURE = "palaziks"


def read_fix(name):
    """Read a fixes/<name> block: the lines to append, minus '#' comments and
    blanks. Both port.py and port.sh read the same files, so a change to a fix
    (a FOD coordinate, a new prop) only has to be made once."""
    path = os.path.join(FIXES_DIR, name)
    out = []
    if os.path.exists(path):
        for line in read_lines(path):
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(line)
    return out


def assemble(work, mi_ext_dir):
    product = os.path.join(work, "product")
    system_sys = os.path.join(work, "system", "system")
    system_ext = os.path.join(work, "system_ext")
    vendor = os.path.join(work, "vendor")
    odm = os.path.join(work, "odm")

    log("[1] folding mi_ext into product + system")
    move_tree(os.path.join(mi_ext_dir, "product"), product)
    move_tree(os.path.join(mi_ext_dir, "system"), system_sys)

    log("[2] merging mi_ext/etc/build.prop into product + system/system build.prop")
    prod_bp = os.path.join(product, "etc", "build.prop")
    sys_bp = os.path.join(system_sys, "build.prop")
    mi_ext_bp = os.path.join(mi_ext_dir, "etc", "build.prop")
    if os.path.exists(mi_ext_bp):
        # drop the huge ab_ota_partitions line once, so it reaches neither target
        prop_remove(mi_ext_bp, lambda l: l.strip().startswith(
            "ro.vendor.build.ab_ota_partitions="))
        extra = read_lines(mi_ext_bp)
        for target in (prod_bp, sys_bp):
            base = read_lines(target) if os.path.exists(target) else []
            write_lines(target, base + [""] + extra)

    log("[3] system/system/build.prop: home + dexopt")
    prop_append(os.path.join(system_sys, "build.prop"),
                read_fix("system.build.prop"), header="# " + SIGNATURE)

    log("[4] tagging ro.mi.os.version.incremental with | %s" % SIGNATURE)
    tag_incremental(prod_bp)

    log("[5] moving product/pangu/system -> system/system")
    move_tree(os.path.join(product, "pangu", "system"), system_sys)

    # applied from fixes/vendor.build.prop in apply_fixes(); nothing to do here
    log("[6] OP13 vendor props applied from fixes/vendor.build.prop (in [FIX])")

    log("[7] odm/build.prop: Xiaomi attestation block")
    prop_append(os.path.join(odm, "build.prop"),
                read_fix("odm.build.prop"), header="# " + SIGNATURE)

    log("[8] odm/build.prop: removing import lines")
    prop_remove(os.path.join(odm, "build.prop"),
                lambda l: l.strip().startswith("import"))

    log("[9] removing ro.vendor.oplus.sensor.high_pwm_rgb")
    for bp in (os.path.join(vendor, "build.prop"), os.path.join(odm, "build.prop")):
        prop_remove(bp, lambda l: l.strip().startswith(
            "ro.vendor.oplus.sensor.high_pwm_rgb"))

    log("[10] product/etc/build.prop: density 600 + status bar tint")
    prop_append(prod_bp, read_fix("product.build.prop"), header="# " + SIGNATURE)

    log("[11] removing system_ext/priv-app/qcrilmsgtunnel")
    shutil.rmtree(os.path.join(system_ext, "priv-app", "qcrilmsgtunnel"),
                  ignore_errors=True)

    log("[12] removing product/priv-app/MiuiCamera (replaced from RES)")
    shutil.rmtree(os.path.join(product, "priv-app", "MiuiCamera"),
                  ignore_errors=True)


def tag_incremental(prod_bp):
    if not os.path.exists(prod_bp):
        return
    lines = read_lines(prod_bp)
    key = "ro.mi.os.version.incremental="
    for i, l in enumerate(lines):
        if l.startswith(key):
            val = l[len(key):]
            if not val.endswith(" | " + SIGNATURE):
                lines[i] = key + val + " | " + SIGNATURE
            break
    write_lines(prod_bp, lines)


def load_device(name):
    ddir = os.path.join(HERE, "devices", name)
    path = os.path.join(ddir, "device.conf")
    if not os.path.exists(path):
        base = os.path.join(HERE, "devices")
        avail = ", ".join(sorted(d for d in os.listdir(base)
                                 if os.path.exists(os.path.join(base, d, "device.conf"))))
        die("unknown device '%s' (available: %s)" % (name, avail))
    cfg = {"_dir": ddir}
    for line in read_lines(path):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        cfg[k.strip()] = v.strip()
    return cfg


def _detect_device(work):
    vbp = os.path.join(work, "vendor", "build.prop")
    for line in (read_lines(vbp) if os.path.exists(vbp) else []):
        for key in ("ro.product.vendor.device=", "ro.product.device="):
            if line.startswith(key):
                return line.split("=", 1)[1].strip()
    return None


def apply_device(work, cfg):
    """Apply the device folder: its displayconfig and device_features overlay
    plus the scalar overrides (FOD geometry, density, resolution, marketname).
    device_features is named after the port's detected ro.product.device."""
    log("[DEVICE] %s (%s)" % (cfg.get("name", "?"), cfg.get("model", "?")))
    ddir = cfg["_dir"]

    dc_src = os.path.join(ddir, "displayconfig")
    if os.path.isdir(dc_src):
        dc_dst = os.path.join(work, "product", "etc", "displayconfig")
        os.makedirs(dc_dst, exist_ok=True)
        for f in os.listdir(dc_src):
            shutil.copy2(os.path.join(dc_src, f), os.path.join(dc_dst, f))

    df_src = os.path.join(ddir, "device_features.xml")
    dev = _detect_device(work)
    if os.path.exists(df_src) and dev:
        dfd = os.path.join(work, "product", "etc", "device_features")
        os.makedirs(dfd, exist_ok=True)
        shutil.copy2(df_src, os.path.join(dfd, dev + ".xml"))
        log("    device_features -> %s.xml" % dev)

    vbp = os.path.join(work, "vendor", "build.prop")
    prop_set(vbp, "persist.vendor.sys.fp.fod.location.X_Y", cfg.get("fod_location"))
    prop_set(vbp, "persist.vendor.sys.fp.fod.size.width_height", cfg.get("fod_size"))
    prop_set(vbp, "persist.vendor.sys.fp.fod.us.target", cfg.get("fod_target"))
    prop_set(vbp, "persist.sys.miui_resolution", cfg.get("miui_resolution"))
    pbp = os.path.join(work, "product", "etc", "build.prop")
    prop_set(pbp, "persist.miui.density_v2", cfg.get("density"))
    prop_set(pbp, "ro.sf.lcd_density", cfg.get("density"))
    prop_set(os.path.join(work, "odm", "build.prop"),
             "ro.product.odm.marketname", cfg.get("marketname"))


def ensure_camera(res_dir, gdrive_id):
    """Fetch the working MiuiCamera into RES from Google Drive when it isn't
    already present (too big for git)."""
    dest = os.path.join(res_dir, "product", "priv-app", "MiuiCamera",
                        "MiuiCamera.apk")
    if os.path.exists(dest):
        return
    log("[RES] MiuiCamera not present; downloading from Google Drive")
    priv_app = os.path.join(res_dir, "product", "priv-app")
    os.makedirs(priv_app, exist_ok=True)
    tmp_zip = os.path.join(res_dir, "_MiuiCamera.zip")
    gdrive.download(gdrive_id, tmp_zip, log=log)
    with zipfile.ZipFile(tmp_zip) as z:
        z.extractall(priv_app)
    os.remove(tmp_zip)
    if not os.path.exists(dest):
        die("camera zip did not contain MiuiCamera/MiuiCamera.apk")


def apply_res(work, res_dir):
    """Copy every partition folder present in RES/ over the assembled tree,
    then re-sync SELinux config so the new files carry correct labels. RES
    convention: RES/<part>/... maps onto work/<part>/..."""
    log("[RES] overlaying RES files")
    for part in sorted(os.listdir(res_dir)):
        src = os.path.join(res_dir, part)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(work, part)
        copy_tree(src, dst)
    # pin vendor permission xmls to vendor_configs_file (the FOD label fix)
    vperm = os.path.join(work, "vendor", "etc", "permissions")
    if os.path.isdir(vperm):
        for fn in os.listdir(vperm):
            if fn.endswith(".xml"):
                set_context(work, "vendor", "etc/permissions/" + fn,
                            "vendor_configs_file", mode="0644")


def apply_fixes(work):
    """Vendor-side line fixes: the OP13 vendor props (step-6 '# end of file'
    block + FOD) and the SELinux property_contexts. (Product build.prop fixes
    are applied in step 10; whole-file overlays live in RES/.)"""
    log("[FIX] applying vendor build.prop + property_contexts fixes")
    vendor_bp = os.path.join(work, "vendor", "build.prop")
    prop_append(vendor_bp, read_fix("vendor.build.prop"),
                header="# OP13 vendor props + FOD (palaziks)")

    pc = os.path.join(work, "vendor", "etc", "selinux", "vendor_property_contexts")
    if os.path.exists(pc):
        prop_append(pc, read_fix("vendor.property_contexts"),
                    header="# FOD / status-bar / face prop contexts (palaziks)")
    else:
        log("    WARNING: vendor_property_contexts missing; FOD props unreadable")


# pack + zip
def pack_partition(work, part, out_dir, ts):
    img = os.path.join(out_dir, part + ".img")
    fsc = os.path.join(work, "config", part + "_fs_config")
    fc = os.path.join(work, "config", part + "_file_contexts")
    log("packing %s.img" % part)
    run([MKFS, "-zlz4hc,0", "-T", str(ts),
         "--mount-point=/" + part,
         "--product-out=" + work,
         "--fs-config-file=" + fsc,
         "--file-contexts=" + fc,
         img, os.path.join(work, part)], quiet=True)
    return img


def make_zip(imgs, zip_path):
    log("packing uncompressed zip: %s" % zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for img in imgs:
            z.write(img, os.path.basename(img))


# main
def main(argv):
    ap = argparse.ArgumentParser(
        description="HyperOS (2-4) -> OnePlus auto-porter")
    ap.add_argument("--device", required=True,
                    help="target device, required (a name under devices/, "
                         "e.g. OnePlus13 or OnePlus15)")
    ap.add_argument("--stock", required=True,
                    help="OnePlus stock ROM: URL, zip, payload.bin or dir "
                         "(source of vendor + odm)")
    ap.add_argument("--hyperos", "--hos4", dest="hos4", required=True,
                    help="HyperOS ROM (2 to 4): URL, zip, payload.bin or dir "
                         "(source of system + system_ext + product)")
    ap.add_argument("--work", default="work", help="working directory")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument("--res", default=os.path.join(HERE, "RES"),
                    help="RES overlay directory")
    ap.add_argument("--name", default=None,
                    help="base name for the output zip")
    ap.add_argument("--keep-work", action="store_true",
                    help="do not delete the working directory at the end")
    args = ap.parse_args(argv)

    device = load_device(args.device)
    if not args.name:
        args.name = "HyperOS-%s-port" % args.device

    work = os.path.abspath(args.work)
    out = os.path.abspath(args.out)
    dl = os.path.join(work, "_inputs")
    for d in (work, out, dl):
        os.makedirs(d, exist_ok=True)

    for tool in (MKFS, EXTRACT):
        if not os.path.exists(tool):
            die("missing tool: %s" % tool)
        os.chmod(tool, 0o755)

    log("== resolving inputs ==")
    stock_src = resolve_input(args.stock, dl, "stock")
    hos4_src = resolve_input(args.hos4, dl, "hyperos")

    log("== extracting payloads ==")
    stock_imgs = get_images(stock_src, os.path.join(dl, "stock_img"),
                            FROM_STOCK, "stock")
    hos4_imgs = get_images(hos4_src, os.path.join(dl, "hyperos_img"),
                           FROM_HOS4, "hyperos")

    log("== unpacking images ==")
    for part in FROM_STOCK:
        unpack_erofs(stock_imgs[part], work)
    for part in ["system", "system_ext", "product"]:
        unpack_erofs(hos4_imgs[part], work)
    # mi_ext is unpacked to a side dir (folded in, never packed on its own)
    mi_ext_dir = os.path.join(work, "mi_ext")
    unpack_erofs(hos4_imgs["mi_ext"], work)

    log("== assembling ==")
    assemble(work, mi_ext_dir)

    ensure_camera(args.res, device.get("camera_gdrive_id"))
    apply_res(work, args.res)
    apply_fixes(work)
    apply_device(work, device)

    log("== syncing SELinux config ==")
    for part in PACK:
        sync_config(work, part)

    log("== packing images ==")
    ts = int(time.time())
    imgs = [pack_partition(work, part, out, ts) for part in PACK]

    zip_path = os.path.join(out, args.name + ".zip")
    make_zip(imgs, zip_path)
    log("done: %s" % zip_path)

    if not args.keep_work:
        shutil.rmtree(dl, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
