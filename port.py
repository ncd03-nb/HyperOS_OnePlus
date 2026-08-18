#!/usr/bin/env python3
# HyperOS -> OnePlus 13 auto-porter (HyperOS 2, 3 and 4).
#
# Takes a OnePlus 13 stock ROM (for vendor + odm) and a HyperOS ROM
# (for system + system_ext + product), assembles a bootable/DSU-loadable
# port, applies the OnePlus 13 fixes, and writes an uncompressed flashable zip.
#
# Runnable locally and from GitHub Actions. Inputs may be URLs or local paths;
# each may be a payload.bin, a zip containing payload.bin, or an already
# unpacked directory tree.
#
# This porter supports ONLY the OnePlus 13. Do not use it for other devices.
#
# See README.md for the full step list and credits.

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

# working MiuiCamera build (too big for git); fetched into RES when missing
CAMERA_GDRIVE_ID = "125kqJ-vq_7pM85MRbny6lFazYHMUn0Yh"

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


# ---------------------------------------------------------------------------
# input resolution: URL / zip / payload.bin / directory  ->  usable path
# ---------------------------------------------------------------------------
def resolve_input(src, dst_dir, label):
    """Return a local path to either a payload.bin or an unpacked dir."""
    os.makedirs(dst_dir, exist_ok=True)
    local = src
    if src.startswith("http://") or src.startswith("https://"):
        local = download(src, dst_dir, label)
    if os.path.isdir(local):
        return local
    lower = local.lower()
    if lower.endswith(".zip"):
        return unzip_find_payload(local, dst_dir, label)
    if os.path.basename(local) == "payload.bin" or lower.endswith(".bin"):
        return local
    die("cannot handle %s input: %s" % (label, src))


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


# ---------------------------------------------------------------------------
# payload.bin -> raw *.img   (prefer payload-dumper-go, else built-in)
# ---------------------------------------------------------------------------
def dump_payload(payload_path, out_dir, wanted):
    os.makedirs(out_dir, exist_ok=True)
    tool = shutil.which("payload-dumper-go") or shutil.which("payload_dumper_go")
    bundled = os.path.join(EROFS_BIN, "payload-dumper-go")
    if not tool and os.path.exists(bundled):
        tool = bundled
    if tool:
        log("extracting payload (%s) with %s" % (",".join(wanted), os.path.basename(tool)))
        run([tool, "-p", ",".join(wanted), "-o", out_dir, payload_path], quiet=True)
    else:
        log("extracting payload (%s) with built-in extractor" % ",".join(wanted))
        payload_extractor.extract(payload_path, out_dir, wanted, log=lambda *_a: None)
    return {p: os.path.join(out_dir, p + ".img") for p in wanted}


def get_images(resolved, out_dir, wanted, label):
    """resolved is either a payload.bin, a dir of *.img, or an unpacked ROM
    dir. Return {partition: img_path} for the wanted partitions."""
    if os.path.isdir(resolved):
        found = {}
        for p in wanted:
            cand = os.path.join(resolved, p + ".img")
            if os.path.exists(cand):
                found[p] = cand
        missing = [p for p in wanted if p not in found]
        if missing:
            die("%s dir missing images: %s" % (label, ", ".join(missing)))
        return found
    return dump_payload(resolved, out_dir, wanted)


# ---------------------------------------------------------------------------
# erofs image  ->  files + config/<part>_{file_contexts,fs_config,fs_options}
# ---------------------------------------------------------------------------
def unpack_erofs(img, work):
    log("unpacking %s" % os.path.basename(img))
    run([EXTRACT, "-i", img, "-x", "-s", "-f", "-o", work], quiet=True)


# ---------------------------------------------------------------------------
# small build.prop / text helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# the 12-step assembly + fixes
# ---------------------------------------------------------------------------
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

    # --- step 1: fold mi_ext into product + system/system ---
    log("[1] folding mi_ext into product + system")
    move_tree(os.path.join(mi_ext_dir, "product"), product)
    move_tree(os.path.join(mi_ext_dir, "system"), system_sys)

    # --- step 2: append mi_ext/etc/build.prop to product AND system/system ---
    log("[2] merging mi_ext/etc/build.prop into product + system/system build.prop")
    prod_bp = os.path.join(product, "etc", "build.prop")
    sys_bp = os.path.join(system_sys, "build.prop")
    mi_ext_bp = os.path.join(mi_ext_dir, "etc", "build.prop")
    if os.path.exists(mi_ext_bp):
        # drop the huge ab_ota_partitions line once at the source, so it lands
        # in neither product nor system (instead of stripping it from both).
        prop_remove(mi_ext_bp, lambda l: l.strip().startswith(
            "ro.vendor.build.ab_ota_partitions="))
        extra = read_lines(mi_ext_bp)
        for target in (prod_bp, sys_bp):
            base = read_lines(target) if os.path.exists(target) else []
            write_lines(target, base + [""] + extra)

    # --- step 3: home + dexopt into system/system/build.prop ---
    log("[3] system/system/build.prop: home + dexopt")
    prop_append(os.path.join(system_sys, "build.prop"),
                read_fix("system.build.prop"), header="# " + SIGNATURE)

    # --- step 4: tag ro.mi.os.version.incremental in product/etc/build.prop ---
    log("[4] tagging ro.mi.os.version.incremental with | %s" % SIGNATURE)
    tag_incremental(prod_bp)

    # --- step 5: relocate product/pangu/system into system/system ---
    log("[5] moving product/pangu/system -> system/system")
    move_tree(os.path.join(product, "pangu", "system"), system_sys)

    # --- step 6: OP13 vendor props (the "# end of file" block) ---
    # These live in fixes/vendor.build.prop and are appended in apply_fixes(),
    # so there is nothing to pull from the Xiaomi vendor here.
    log("[6] OP13 vendor props applied from fixes/vendor.build.prop (in [FIX])")

    # --- step 7: odm attestation block ---
    log("[7] odm/build.prop: Xiaomi attestation block")
    prop_append(os.path.join(odm, "build.prop"),
                read_fix("odm.build.prop"), header="# " + SIGNATURE)

    # --- step 8: strip 'import' lines from odm/build.prop ---
    log("[8] odm/build.prop: removing import lines")
    prop_remove(os.path.join(odm, "build.prop"),
                lambda l: l.strip().startswith("import"))

    # --- step 9: drop high_pwm_rgb from vendor build.prop ---
    log("[9] removing ro.vendor.oplus.sensor.high_pwm_rgb")
    for bp in (os.path.join(vendor, "build.prop"), os.path.join(odm, "build.prop")):
        prop_remove(bp, lambda l: l.strip().startswith(
            "ro.vendor.oplus.sensor.high_pwm_rgb"))

    # --- step 10: density (+ status bar tint) into product/etc/build.prop ---
    log("[10] product/etc/build.prop: density 600 + status bar tint")
    prop_append(prod_bp, read_fix("product.build.prop"), header="# " + SIGNATURE)

    # --- step 11: delete system_ext/priv-app/qcrilmsgtunnel ---
    log("[11] removing system_ext/priv-app/qcrilmsgtunnel")
    shutil.rmtree(os.path.join(system_ext, "priv-app", "qcrilmsgtunnel"),
                  ignore_errors=True)

    # --- step 12: delete product/priv-app/MiuiCamera (RES supplies its own) ---
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


def ensure_camera(res_dir):
    """The working MiuiCamera is too big for git, so fetch it into RES from
    Google Drive if it isn't already there (local users who dropped it in keep
    their copy; a fresh CI checkout downloads it)."""
    dest = os.path.join(res_dir, "product", "priv-app", "MiuiCamera",
                        "MiuiCamera.apk")
    if os.path.exists(dest):
        return
    log("[RES] MiuiCamera not present; downloading from Google Drive")
    priv_app = os.path.join(res_dir, "product", "priv-app")
    os.makedirs(priv_app, exist_ok=True)
    tmp_zip = os.path.join(res_dir, "_MiuiCamera.zip")
    gdrive.download(CAMERA_GDRIVE_ID, tmp_zip, log=log)
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
    # vendor permission xmls must be vendor_configs_file (the FOD label fix).
    # nearest-parent inheritance already yields this, but pin it to be safe.
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


# ---------------------------------------------------------------------------
# pack + zip
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv):
    ap = argparse.ArgumentParser(
        description="HyperOS (2-4) -> OnePlus 13 auto-porter")
    ap.add_argument("--stock", required=True,
                    help="OnePlus 13 stock ROM: URL, zip, payload.bin or dir "
                         "(source of vendor + odm)")
    ap.add_argument("--hyperos", "--hos4", dest="hos4", required=True,
                    help="HyperOS ROM (2 to 4): URL, zip, payload.bin or dir "
                         "(source of system + system_ext + product)")
    ap.add_argument("--work", default="work", help="working directory")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument("--res", default=os.path.join(HERE, "RES"),
                    help="RES overlay directory")
    ap.add_argument("--name", default="HyperOS-OnePlus13-port",
                    help="base name for the output zip")
    ap.add_argument("--keep-work", action="store_true",
                    help="do not delete the working directory at the end")
    args = ap.parse_args(argv)

    work = os.path.abspath(args.work)
    out = os.path.abspath(args.out)
    dl = os.path.join(work, "_inputs")
    for d in (work, out, dl):
        os.makedirs(d, exist_ok=True)

    for tool in (MKFS, EXTRACT):
        if not os.path.exists(tool):
            die("missing tool: %s" % tool)
        os.chmod(tool, 0o755)

    # --- resolve + fetch inputs ---
    log("== resolving inputs ==")
    stock_src = resolve_input(args.stock, dl, "stock")
    hos4_src = resolve_input(args.hos4, dl, "hyperos")

    # --- get raw images ---
    log("== extracting payloads ==")
    stock_imgs = get_images(stock_src, os.path.join(dl, "stock_img"),
                            FROM_STOCK, "stock")
    hos4_imgs = get_images(hos4_src, os.path.join(dl, "hyperos_img"),
                           FROM_HOS4, "hyperos")

    # --- unpack erofs into work tree ---
    log("== unpacking images ==")
    for part in FROM_STOCK:
        unpack_erofs(stock_imgs[part], work)
    for part in ["system", "system_ext", "product"]:
        unpack_erofs(hos4_imgs[part], work)
    # mi_ext is unpacked to a side dir (folded in, never packed on its own)
    mi_ext_dir = os.path.join(work, "mi_ext")
    unpack_erofs(hos4_imgs["mi_ext"], work)

    # --- assemble (12 steps) ---
    log("== assembling ==")
    assemble(work, mi_ext_dir)

    # --- RES overlay + line fixes ---
    ensure_camera(args.res)
    apply_res(work, args.res)
    apply_fixes(work)

    # --- sync SELinux config for everything we added/moved ---
    log("== syncing SELinux config ==")
    for part in PACK:
        sync_config(work, part)

    # --- pack ---
    log("== packing images ==")
    ts = int(time.time())
    imgs = [pack_partition(work, part, out, ts) for part in PACK]

    # --- zip ---
    zip_path = os.path.join(out, args.name + ".zip")
    make_zip(imgs, zip_path)
    log("done: %s" % zip_path)

    if not args.keep_work:
        shutil.rmtree(dl, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
