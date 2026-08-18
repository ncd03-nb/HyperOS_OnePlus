#!/usr/bin/env bash
# HyperOS (2-4) -> OnePlus auto-porter (multi-device; see devices/).
#   ./port.sh --device <PJZ110|PLK110> --stock <stock-rom> --hyperos <hyperos-rom>
# Inputs: URL, zip, payload.bin or an unpacked directory.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
EROFS_BIN="$HERE/bin/Linux/x86_64"
MKFS="$EROFS_BIN/mkfs.erofs"
EXTRACT="$EROFS_BIN/extract.erofs"
FIXES="$HERE/fixes"
PY="${PYTHON:-python3}"
SIG="palaziks"

PACK_PARTS=(system system_ext product vendor odm)

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run() { log "+ $*"; "$@"; }
# run a noisy tool quietly: swallow its output, but dump it if it fails
quiet_run() {
    local lf; lf="$(mktemp)"
    if "$@" >"$lf" 2>&1; then
        rm -f "$lf"
    else
        local rc=$?; cat "$lf"; rm -f "$lf"; return $rc
    fi
}

# args
DEVICE=""; STOCK=""; HOS4=""; WORK="work"; OUT="out"; RES="$HERE/RES"
NAME=""; KEEP_WORK=0
while [ $# -gt 0 ]; do
    case "$1" in
        --device) DEVICE="$2"; shift 2;;
        --stock) STOCK="$2"; shift 2;;
        --hos4|--hyperos) HOS4="$2"; shift 2;;
        --work) WORK="$2"; shift 2;;
        --out) OUT="$2"; shift 2;;
        --res) RES="$2"; shift 2;;
        --name) NAME="$2"; shift 2;;
        --keep-work) KEEP_WORK=1; shift;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
        *) die "unknown arg: $1";;
    esac
done
[ -n "$DEVICE" ] || die "--device is required (a name under devices/, e.g. PJZ110 or PLK110)"
[ -f "$HERE/devices/$DEVICE.yml" ] || die "unknown device: $DEVICE (see devices/)"
[ -n "$STOCK" ] || die "--stock is required (OnePlus stock ROM)"
[ -n "$HOS4" ] || die "--hyperos is required (HyperOS 2-4 ROM)"
[ -n "$NAME" ] || NAME="HyperOS-$DEVICE-port"

# load devices/<DEVICE>.yml into DEV_<key> vars
while IFS= read -r line; do
    case "$line" in ""|\#*) continue;; esac
    case "$line" in *:*) : ;; *) continue;; esac
    k="${line%%:*}"; v="${line#*:}"
    k="$(printf '%s' "$k" | tr -d '[:space:]')"
    v="$(printf '%s' "$v" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [ -n "$k" ] && eval "DEV_${k}=\$v"
done < "$HERE/devices/$DEVICE.yml"
[ -x "$MKFS" ] || die "missing $MKFS"
[ -x "$EXTRACT" ] || die "missing $EXTRACT"

WORK="$(mkdir -p "$WORK" && cd "$WORK" && pwd)"
OUT="$(mkdir -p "$OUT" && cd "$OUT" && pwd)"
DL="$WORK/_inputs"; mkdir -p "$DL"

# input resolution
download() {   # url dstdir label -> echoes path
    local url="$1" dst="$2" label="$3" out
    out="$dst/${label}_download"
    case "${url%%\?*}" in
        *.zip) out="$out.zip";; *.bin) out="$out.bin";; *) out="$out.zip";;
    esac
    log "downloading $label ROM: $url" >&2
    if command -v aria2c >/dev/null; then
        run aria2c -x16 -s16 -o "$(basename "$out")" -d "$dst" "$url" >&2
    elif command -v curl >/dev/null; then
        run curl -L --fail -o "$out" "$url" >&2
    elif command -v wget >/dev/null; then
        run wget -O "$out" "$url" >&2
    else
        die "no downloader (need aria2c, curl or wget)"
    fi
    printf '%s\n' "$out"
}

unzip_find_payload() {   # zip dstdir label -> echoes path (payload.bin or dir)
    local zip="$1" dst="$2" label="$3" ex="$2/${3}_zip"
    mkdir -p "$ex"
    local payload
    payload="$(unzip -Z1 "$zip" 2>/dev/null | grep -m1 '\(^\|/\)payload\.bin$' || true)"
    if [ -n "$payload" ]; then
        log "found $payload in zip" >&2
        run unzip -o -j "$zip" "$payload" -d "$ex" >&2
        printf '%s\n' "$ex/$(basename "$payload")"
    else
        run unzip -o "$zip" '*.img' -d "$ex" >&2 || true
        ls "$ex"/*.img >/dev/null 2>&1 || die "$label zip: no payload.bin and no *.img"
        printf '%s\n' "$ex"
    fi
}

resolve_input() {   # src dstdir label -> echoes local path (zip/bin/dir, as-is)
    local src="$1" dst="$2" label="$3"
    mkdir -p "$dst"
    case "$src" in http://*|https://*) download "$src" "$dst" "$label";; *) printf '%s\n' "$src";; esac
}

find_dumper() {   # echoes payload-dumper-rust binary if available
    command -v payload_dumper >/dev/null && { echo payload_dumper; return; }
    [ -x "$EROFS_BIN/payload_dumper" ] && echo "$EROFS_BIN/payload_dumper"
}

# zip/payload.bin -> *.img (rust reads either directly); echoes dir of <part>.img
dump_payload() {   # input outdir label parts...
    local inp="$1" outdir="$2" label="$3"; shift 3
    local parts="$*"; parts="${parts// /,}"
    mkdir -p "$outdir"
    local tool; tool="$(find_dumper)"
    if [ -n "$tool" ]; then
        log "extracting $parts from $(basename "$inp") with payload-dumper-rust" >&2
        quiet_run "$tool" "$inp" -o "$outdir" -i "$parts" >&2
        printf '%s\n' "$outdir"; return
    fi
    local payload="$inp"
    case "$inp" in *.zip) payload="$(unzip_find_payload "$inp" "$outdir" "$label")";; esac
    [ -d "$payload" ] && { printf '%s\n' "$payload"; return; }  # zip carried raw *.img
    log "extracting $parts with built-in extractor" >&2
    quiet_run "$PY" "$HERE/lib/payload_extractor.py" -o "$outdir" -p "$parts" "$payload" >&2
    printf '%s\n' "$outdir"
}

get_images() {   # resolved outdir label parts... ; sets IMG_<part> vars
    local resolved="$1" outdir="$2" label="$3"; shift 3
    local p imgdir
    if [ -d "$resolved" ]; then
        imgdir="$resolved"
    else
        imgdir="$(dump_payload "$resolved" "$outdir" "$label" "$@")"
    fi
    for p in "$@"; do
        [ -f "$imgdir/$p.img" ] || die "$label: $p.img not produced"
        eval "IMG_$p=\"$imgdir/$p.img\""
    done
}

unpack_erofs() { log "unpacking $(basename "$1")"; quiet_run "$EXTRACT" -i "$1" -x -s -f -o "$2"; }

# build.prop helpers
prop_append() {   # file header line...
    local file="$1" header="$2"; shift 2
    [ -f "$file" ] || : > "$file"
    local added=0 l
    local tmp; tmp="$(mktemp)"; cp "$file" "$tmp"
    for l in "$@"; do
        grep -qxF "$l" "$tmp" || { added=1; }
    done
    if [ "$added" -eq 1 ]; then
        { printf '\n%s\n' "$header"; for l in "$@"; do grep -qxF "$l" "$file" || printf '%s\n' "$l"; done; } >> "$file"
    fi
    rm -f "$tmp"
}
prop_remove_prefix() {   # file prefix
    local file="$1" pfx="$2"
    [ -f "$file" ] || return 0
    grep -v "^[[:space:]]*${pfx}" "$file" > "$file.tmp" || true
    mv "$file.tmp" "$file"
}
tag_incremental() {   # file
    local file="$1"
    [ -f "$file" ] || return 0
    awk -v sig="$SIG" '
        /^ro\.mi\.os\.version\.incremental=/ && $0 !~ (" \\| " sig "$") { print $0 " | " sig; next }
        { print }' "$file" > "$file.tmp"
    mv "$file.tmp" "$file"
}
# fixes/ blocks are shared with port.py; a change to a fix is made in one place.
apply_fix() {   # target header fixfile
    local target="$1" header="$2" fixfile="$FIXES/$3"
    [ -f "$fixfile" ] || return 0
    local lines=()
    mapfile -t lines < <(grep -v '^[[:space:]]*#' "$fixfile" | grep -v '^[[:space:]]*$' || true)
    [ ${#lines[@]} -gt 0 ] && prop_append "$target" "$header" "${lines[@]}"
}
prop_set() {   # file key value ; replace key=... (append if absent)
    local file="$1" key="$2" val="$3"
    [ -f "$file" ] && [ -n "$val" ] || return 0
    local esc; esc="$(printf '%s' "$key" | sed 's/[.[\*^$/]/\\&/g')"
    if grep -q "^${esc}=" "$file"; then
        sed -i "s/^${esc}=.*/${key}=${val}/" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}

log "== resolving inputs =="
STOCK_SRC="$(resolve_input "$STOCK" "$DL" stock)"
HOS4_SRC="$(resolve_input "$HOS4" "$DL" hyperos)"

log "== extracting payloads =="
get_images "$STOCK_SRC" "$DL/stock_img" stock vendor odm
get_images "$HOS4_SRC" "$DL/hyperos_img" hyperos system system_ext product mi_ext

log "== unpacking images =="
unpack_erofs "$IMG_vendor" "$WORK"
unpack_erofs "$IMG_odm" "$WORK"
unpack_erofs "$IMG_system" "$WORK"
unpack_erofs "$IMG_system_ext" "$WORK"
unpack_erofs "$IMG_product" "$WORK"
unpack_erofs "$IMG_mi_ext" "$WORK"
MIEXT="$WORK/mi_ext"

# 12-step assembly
PRODUCT="$WORK/product"; SYS="$WORK/system/system"
SYSEXT="$WORK/system_ext"; VENDOR="$WORK/vendor"; ODM="$WORK/odm"
PROD_BP="$PRODUCT/etc/build.prop"

log "[1] folding mi_ext into product + system"
[ -d "$MIEXT/product" ] && cp -a "$MIEXT/product/." "$PRODUCT/" && rm -rf "$MIEXT/product"
[ -d "$MIEXT/system" ] && cp -a "$MIEXT/system/." "$SYS/" && rm -rf "$MIEXT/system"

log "[2] merging mi_ext/etc/build.prop into product + system/system build.prop"
if [ -f "$MIEXT/etc/build.prop" ]; then
    # drop the huge ab_ota_partitions line once, so it reaches neither target
    prop_remove_prefix "$MIEXT/etc/build.prop" "ro.vendor.build.ab_ota_partitions="
    printf '\n' >> "$PROD_BP"; cat "$MIEXT/etc/build.prop" >> "$PROD_BP"
    printf '\n' >> "$SYS/build.prop"; cat "$MIEXT/etc/build.prop" >> "$SYS/build.prop"
fi

log "[3] system/system/build.prop: home + dexopt"
apply_fix "$SYS/build.prop" "# $SIG" system.build.prop

log "[4] tagging ro.mi.os.version.incremental with | $SIG"
tag_incremental "$PROD_BP"

log "[5] moving product/pangu/system -> system/system"
if [ -d "$PRODUCT/pangu/system" ]; then
    cp -a "$PRODUCT/pangu/system/." "$SYS/" && rm -rf "$PRODUCT/pangu/system"
fi

# step 6 vendor props live in fixes/vendor.build.prop, applied in [FIX] below
log "[6] OP13 vendor props applied from fixes/vendor.build.prop (in [FIX])"

log "[7] odm/build.prop: Xiaomi attestation block"
apply_fix "$ODM/build.prop" "# $SIG" odm.build.prop

log "[8] odm/build.prop: removing import lines"
prop_remove_prefix "$ODM/build.prop" "import"

log "[9] removing ro.vendor.oplus.sensor.high_pwm_rgb"
prop_remove_prefix "$VENDOR/build.prop" "ro.vendor.oplus.sensor.high_pwm_rgb"
prop_remove_prefix "$ODM/build.prop" "ro.vendor.oplus.sensor.high_pwm_rgb"

log "[10] product/etc/build.prop: density 600 + status bar tint"
apply_fix "$PROD_BP" "# $SIG" product.build.prop

log "[11] removing system_ext/priv-app/qcrilmsgtunnel"
rm -rf "$SYSEXT/priv-app/qcrilmsgtunnel"

log "[12] removing product/priv-app/MiuiCamera (replaced from RES)"
rm -rf "$PRODUCT/priv-app/MiuiCamera"

# fetch MiuiCamera into RES if missing (too big for git)
CAM="$RES/product/priv-app/MiuiCamera/MiuiCamera.apk"
if [ ! -f "$CAM" ]; then
    log "[RES] MiuiCamera not present; downloading from Google Drive"
    mkdir -p "$RES/product/priv-app"
    run "$PY" "$HERE/lib/gdrive.py" "${DEV_camera_gdrive_id:-}" "$RES/_MiuiCamera.zip"
    run unzip -o -q "$RES/_MiuiCamera.zip" -d "$RES/product/priv-app/"
    rm -f "$RES/_MiuiCamera.zip"
    [ -f "$CAM" ] || die "camera zip did not contain MiuiCamera/MiuiCamera.apk"
fi

# RES overlay
log "[RES] overlaying RES files"
if [ -d "$RES" ]; then
    for part in "$RES"/*/; do
        [ -d "$part" ] || continue
        pname="$(basename "$part")"
        mkdir -p "$WORK/$pname"
        cp -a "$part". "$WORK/$pname/"
    done
fi

# vendor line-based fixes
log "[FIX] vendor build.prop + property_contexts fixes"
apply_fix "$VENDOR/build.prop" "# OP13 vendor props + FOD ($SIG)" vendor.build.prop

PC="$VENDOR/etc/selinux/vendor_property_contexts"
if [ -f "$PC" ]; then
    apply_fix "$PC" "# FOD / status-bar / face prop contexts ($SIG)" vendor.property_contexts
else
    log "    WARNING: vendor_property_contexts missing; FOD props unreadable"
fi

# device-specific overrides on top of the shared baseline
log "[DEVICE] ${DEV_name:-$DEVICE} ($DEVICE)"
prop_set "$VENDOR/build.prop" "persist.vendor.sys.fp.fod.location.X_Y" "${DEV_fod_location:-}"
prop_set "$VENDOR/build.prop" "persist.vendor.sys.fp.fod.size.width_height" "${DEV_fod_size:-}"
prop_set "$VENDOR/build.prop" "persist.vendor.sys.fp.fod.us.target" "${DEV_fod_target:-}"
prop_set "$VENDOR/build.prop" "persist.sys.miui_resolution" "${DEV_miui_resolution:-}"
prop_set "$PROD_BP" "persist.miui.density_v2" "${DEV_density:-}"
prop_set "$PROD_BP" "ro.sf.lcd_density" "${DEV_density:-}"
prop_set "$ODM/build.prop" "ro.product.odm.marketname" "${DEV_marketname:-}"
# name device_features after the port's ro.product.device
DEVNAME="$(grep -m1 -E '^ro\.product\.(vendor\.)?device=' "$VENDOR/build.prop" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
DF="$WORK/product/etc/device_features"
if [ -n "$DEVNAME" ] && [ -f "$DF/ossi.xml" ] && [ ! -f "$DF/$DEVNAME.xml" ]; then
    cp "$DF/ossi.xml" "$DF/$DEVNAME.xml"; log "    device_features -> $DEVNAME.xml"
fi

# SELinux config synthesis (delegated to Python helper)
log "== syncing SELinux config =="
for part in "${PACK_PARTS[@]}"; do
    run "$PY" "$HERE/lib/erofs_config.py" sync "$WORK" "$part"
done
# pin vendor permission xmls to vendor_configs_file (the FOD label fix)
if [ -d "$VENDOR/etc/permissions" ]; then
    for f in "$VENDOR/etc/permissions"/*.xml; do
        [ -e "$f" ] || continue
        run "$PY" "$HERE/lib/erofs_config.py" set "$WORK" vendor \
            "etc/permissions/$(basename "$f")" vendor_configs_file 0644
    done
fi

# pack
log "== packing images =="
TS="$(date +%s)"
IMGS=()
for part in "${PACK_PARTS[@]}"; do
    img="$OUT/$part.img"
    log "packing $part.img"
    quiet_run "$MKFS" -zlz4hc,0 -T "$TS" \
        "--mount-point=/$part" \
        "--product-out=$WORK" \
        "--fs-config-file=$WORK/config/${part}_fs_config" \
        "--file-contexts=$WORK/config/${part}_file_contexts" \
        "$img" "$WORK/$part"
    log "  -> $(du -h "$img" | cut -f1)"
    IMGS+=("$img")
done

# uncompressed zip
ZIP="$OUT/$NAME.zip"
log "packing uncompressed zip: $ZIP"
rm -f "$ZIP"
( cd "$OUT" && zip -0 -X -j "$ZIP" "${IMGS[@]##*/}" >/dev/null )
log "done: $ZIP"

[ "$KEEP_WORK" -eq 1 ] || rm -rf "$DL"
