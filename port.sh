#!/usr/bin/env bash
# HyperOS (1-4) -> OnePlus auto-porter (multi-device; see devices/).
#   ./port.sh --stock <stock-rom> --hyperos <hyperos-rom> [--device <profile>]
# Inputs: URL, zip (payload/raw/super), payload.bin, super.img or an unpacked directory.
# Extracted trees require config/*_fs_config and config/*_file_contexts.
# --apex-stock supplies matching system_ext APEX for Ace 3V SDK34/35/36.
# SDK34 GMS donors also use the verified Android 14 my_bigball Google bundle.
# SDK34/35 keep SELinux enforcing and enable no-auth boot ADB (shell UID2000).
# SDK36 starts ADB securely by default; --force-adb enables its development mode.
# --assemble-only performs assembly and policy compilation, skipping image packing.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
EROFS_BIN="$HERE/bin/Linux/x86_64"
MKFS="$EROFS_BIN/mkfs.erofs"
EXTRACT="$EROFS_BIN/extract.erofs"
FIXES="$HERE/fixes"
PY="${PYTHON:-python3}"
SIG="palaziks"
# Used only for automatic profiles when the stock ROM provides no compatible
# MiuiCamera package. It is the same generic package used by existing profiles.
DEFAULT_CAMERA_GDRIVE_ID="125kqJ-vq_7pM85MRbny6lFazYHMUn0Yh"

PACK_PARTS=(system system_ext product vendor odm)

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run() { log "+ $*"; "$@"; }
notify_stage() {
    [ -f "$HERE/scripts/notify.py" ] || return 0
    (cd "$HERE" && "$PY" "$HERE/scripts/notify.py" "$1") || true
}
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
NAME=""; KEEP_WORK=0; APEX_STOCK=""; ADB_KEY=""; FORCE_ADB="${FORCE_ADB:-0}"; ASSEMBLE_ONLY=0
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
        --apex-stock) APEX_STOCK="$2"; shift 2;;
        --force-adb) FORCE_ADB=1; shift;;
        --adb-key) ADB_KEY="$2"; shift 2;;
        --assemble-only) ASSEMBLE_ONLY=1; shift;;
        -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
        *) die "unknown arg: $1";;
    esac
done
# --device is optional. If absent, we extract the OnePlus vendor/odm images
# first and select a matching profile from their read-only build properties.
if [ -n "$DEVICE" ]; then
    [ "$DEVICE" != "_template" ] || die "_template is documentation, not a buildable device"
    case "$DEVICE" in
        *[!A-Za-z0-9_-]*) die "invalid device profile name: $DEVICE" ;;
    esac
    [ -f "$HERE/devices/$DEVICE/device.conf" ] || die "unknown device: $DEVICE (see devices/)"
fi
[ -n "$STOCK" ] || die "--stock is required (OnePlus stock ROM)"
[ -n "$HOS4" ] || die "--hyperos is required (HyperOS 1-4 ROM)"
[ -x "$MKFS" ] || die "missing $MKFS"
[ -x "$EXTRACT" ] || die "missing $EXTRACT"

WORK="$(mkdir -p "$WORK" && cd "$WORK" && pwd)"
OUT="$(mkdir -p "$OUT" && cd "$OUT" && pwd)"
[ "$WORK" != "/" ] && [ "$WORK" != "$HERE" ] || die "--work must be a dedicated build directory"
DL="$WORK/_inputs"; mkdir -p "$DL"

# input resolution
decrypt_oplus_link() { # OPlus Android 16 downloadCheck URL -> signed CDN URL
    local url="$1" result attempt
    [[ "$url" == *"downloadCheck"* ]] || { printf '%s\n' "$url"; return 0; }
    for attempt in 1 2 3 4 5; do
        if result="$(DECRYPT_URL="$url" "$PY" - <<'PY'
import os
import sys
import urllib.error
import urllib.request

url = os.environ.get("DECRYPT_URL", "")
headers = {
    "User-Agent": "okhttp/3.12.12",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "Keep-Alive",
    "Cache-Control": "no-cache",
    "userId": "oplus-ota|16002018",
}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, new_url):
        return None

try:
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(request, timeout=15)
    except urllib.error.HTTPError as error:
        location = error.headers.get("location", "").strip()
        if error.code in (301, 302, 303, 307, 308) and location.startswith("http"):
            print(location)
            sys.exit(0)
except Exception:
    pass
sys.exit(1)
PY
)"; then :; else result=""; fi
        result="$(printf '%s' "$result" | tr -d '\r\n' | xargs)"
        [ -n "$result" ] && { printf '%s\n' "$result"; return 0; }
        log "OPlus link decryption attempt $attempt/5 failed; retrying" >&2
        sleep 2
    done
    die "could not decrypt OPlus downloadCheck link"
}

download() {   # url dstdir label -> echoes path
    local url="$1" dst="$2" label="$3" out
    out="$dst/${label}_download"
    case "${url%%\?*}" in
        *.zip) out="$out.zip";; *.bin) out="$out.bin";; *) out="$out.zip";;
    esac
    log "downloading $label ROM: $url" >&2
    if [[ "${url%%\?*}" == *.download.json ]]; then
        "$PY" "$HERE/lib/release_download.py" "$url" "$out" >&2 || return $?
    elif "$PY" "$HERE/lib/gdrive.py" --is-drive-url "$url"; then
        "$PY" "$HERE/lib/gdrive.py" "$url" "$out" >&2 || return $?
    elif command -v aria2c >/dev/null; then
        run aria2c -x16 -s16 -o "$(basename "$out")" -d "$dst" "$url" >&2 || return $?
    elif command -v curl >/dev/null; then
        run curl -L --fail -o "$out" "$url" >&2 || return $?
    elif command -v wget >/dev/null; then
        run wget -O "$out" "$url" >&2 || return $?
    else
        die "no downloader (need aria2c, curl or wget)"
    fi
    printf '%s\n' "$out"
}

resolve_input() {   # src dstdir label -> echoes local path (zip/bin/dir, as-is)
    local src="$1" dst="$2" label="$3"
    mkdir -p "$dst"
    case "$src" in
        http://*|https://*)
            if [[ "$src" == *"downloadCheck"* ]]; then
                log "detected OPlus protected OTA link; resolving CDN URL" >&2
                src="$(decrypt_oplus_link "$src")"
            fi
            download "$src" "$dst" "$label"
            ;;
        *) printf '%s\n' "$src";;
    esac
}

find_dumper() {   # echoes payload-dumper-rust binary if available
    command -v payload_dumper >/dev/null && { echo payload_dumper; return; }
    if [ -x "$EROFS_BIN/payload_dumper" ]; then echo "$EROFS_BIN/payload_dumper"; fi
    return 0
}

# Detect payload/raw/super before invoking a payload dumper; echoes image dir.
dump_payload() {   # input outdir label parts...
    local inp="$1" outdir="$2" label="$3"; shift 3
    local parts="$*"; parts="${parts// /,}"
    mkdir -p "$outdir"
    local tool; tool="$(find_dumper)"
    local args=()
    [ -z "$tool" ] || args+=(--payload-tool "$tool")
    log "extracting $parts from $(basename "$inp") (payload/raw/super auto-detection)" >&2
    quiet_run "$PY" "$HERE/lib/rom_input.py" "$inp" -o "$outdir" -p "$parts" "${args[@]}" >&2 || return $?
    printf '%s\n' "$outdir"
}

get_images() {   # resolved outdir label parts... ; sets IMG_<part> vars
    local resolved="$1" outdir="$2" label="$3"; shift 3
    local p imgdir
    if [ -d "$resolved" ] && [ -f "$resolved/config/${1}_fs_config" ] && [ -f "$resolved/config/${1}_file_contexts" ]; then
        imgdir="$resolved"
    else
        imgdir="$(dump_payload "$resolved" "$outdir" "$label" "$@")" || return $?
    fi
    for p in "$@"; do
        if [ -d "$imgdir/$p" ] && [ -f "$imgdir/config/${p}_fs_config" ] && [ -f "$imgdir/config/${p}_file_contexts" ]; then
            eval "IMG_$p=\"$imgdir/$p\""
        else
            [ -f "$imgdir/$p.img" ] || die "$label: $p.img or extracted $p + config metadata not produced"
            eval "IMG_$p=\"$imgdir/$p.img\""
        fi
    done
}

unpack_erofs() {
    log "unpacking $(basename "$1")"
    if [ -d "$1" ]; then
        local pname; pname="$(basename "$1")"
        mkdir -p "$2/config"
        [ ! -e "$2/$pname" ] || die "refusing to reuse unpacked $2/$pname; choose a fresh --work"
        cp -a "$1" "$2/$pname"
        cp "$(dirname "$1")/config/${pname}_fs_config" "$2/config/"
        cp "$(dirname "$1")/config/${pname}_file_contexts" "$2/config/"
    else
        [ ! -e "$2/$(basename "$1" .img)" ] || die "choose a fresh --work; partition tree already exists"
        quiet_run "$EXTRACT" -i "$1" -x -s -f -o "$2"
    fi
}

config_value() { # config-file key
    sed -n "s/^[[:space:]]*$2[[:space:]]*=[[:space:]]*//p" "$1" | head -n1 | sed 's/[[:space:]]*$//'
}

load_device_config() {
    local line k v
    while IFS= read -r line; do
        case "$line" in ""|\#*) continue;; esac
        case "$line" in *=*) : ;; *) continue;; esac
        k="${line%%=*}"; v="${line#*=}"
        k="$(printf '%s' "$k" | tr -d '[:space:]')"
        v="$(printf '%s' "$v" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -n "$k" ] && eval "DEV_${k}=\$v"
    done < "${1:-$HERE/devices/$DEVICE/device.conf}"
}

stock_property_values() { # property key; vendor/odm were already unpacked
    local key="$1" root value
    for root in "$WORK/odm" "$WORK/vendor"; do
        [ -d "$root" ] || continue
        value="$(grep -rhs -m1 "^${key}=" "$root" --include='*.prop' 2>/dev/null | head -n1 | cut -d= -f2- | tr -d '\r')"
        [ -n "$value" ] && printf '%s\n' "$value"
    done
}

profile_matches_values() { # config file, candidate values...
    local config="$1" key list candidate entry
    shift
    for key in match_models match_devices model; do
        list="$(config_value "$config" "$key")"
        [ -n "$list" ] || continue
        local entries=()
        IFS=',' read -r -a entries <<< "$list"
        for entry in "${entries[@]}"; do
            entry="$(printf '%s' "$entry" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            for candidate in "$@"; do
                [ -n "$candidate" ] && [ "$entry" = "$candidate" ] && return 0
            done
        done
    done
    return 1
}

first_stock_property() { # property keys in priority order
    local key value
    for key in "$@"; do
        value="$(stock_property_values "$key" | head -n1)"
        [ -n "$value" ] && { printf '%s\n' "$value"; return 0; }
    done
    return 1
}

safe_profile_component() {
    printf '%s' "$1" | tr -cd 'A-Za-z0-9_-' | cut -c1-48
}

configure_automatic_profile() {
    # SHARED_AUTO_PROFILE_CONF
    if [ -n "${AUTO_PROFILE_CONF:-}" ] && [ -f "$AUTO_PROFILE_CONF" ]; then
        local line k v
        while IFS= read -r line; do
            case "$line" in ""|\#*) continue;; esac
            case "$line" in *=*) : ;; *) continue;; esac
            k="${line%%=*}"; v="${line#*=}"
            k="$(printf '%s' "$k" | tr -d '[:space:]')"
            v="$(printf '%s' "$v" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            [ -n "$k" ] && eval "DEV_${k}=\$v"
        done < "$AUTO_PROFILE_CONF"
        DEVICE="Auto-$(safe_profile_component "${DEV_model:-${DEV_name:-OnePlus}}")"
        [ "$DEVICE" != "Auto-" ] || DEVICE="Auto-OnePlus"
        AUTO_PROFILE=1
        log "== using generated stock-ROM profile: $AUTO_PROFILE_CONF =="
        return 0
    fi
    # Build a best-effort profile from the actual OnePlus vendor/odm props.
    # Defaults only keep the pipeline moving; the output is intentionally
    # labelled Automatic so it is never confused with a tested device profile.
    local model codename width height size location target density resolution market
    model="$(first_stock_property ro.product.odm.model ro.product.vendor.model ro.product.model || true)"
    codename="$(first_stock_property ro.product.odm.device ro.product.vendor.device ro.product.device ro.build.product || true)"
    DEVICE="Auto-$(safe_profile_component "${codename:-$model}")"
    [ "$DEVICE" != "Auto-" ] || DEVICE="Auto-OnePlus"
    density="$(first_stock_property ro.sf.lcd_density persist.vendor.display.lcd_density persist.sys.miui_density || true)"
    density="${density:-440}"
    resolution="$(first_stock_property persist.sys.miui_resolution ro.vendor.display.miui_resolution || true)"
    if [[ "$resolution" =~ ^([0-9]+),([0-9]+),([0-9]+)$ ]]; then
        width="${BASH_REMATCH[1]}"; height="${BASH_REMATCH[2]}"
    else
        # Common OPlus QHD/FHD panel defaults only when the stock omitted a
        # usable resolution property. They keep image generation possible.
        width=1080; height=2400; resolution="$width,$height,480"
    fi
    location="$(first_stock_property persist.vendor.sys.fp.fod.location.X_Y || true)"
    size="$(first_stock_property persist.vendor.sys.fp.fod.size.width_height || true)"
    target="$(first_stock_property persist.vendor.sys.fp.fod.us.target || true)"
    size="${size:-184,184}"
    if [ -z "$location" ]; then
        local fw fh fx fy
        IFS=',' read -r fw fh <<< "$size"
        fx=$(( width / 2 - fw / 2 )); fy=$(( height * 70 / 100 ))
        location="$fx,$fy"
    fi
    if [ -z "$target" ]; then
        local lx ly sw sh
        IFS=',' read -r lx ly <<< "$location"; IFS=',' read -r sw sh <<< "$size"
        target="$((lx - 12)),$((ly - 12)),$((lx + sw + 12)),$((ly + sh + 12))"
    fi
    market="$(first_stock_property ro.product.odm.marketname ro.vendor.oplus.market.name ro.product.marketname || true)"
    DEV_name="${market:-${model:-OnePlus Automatic}}"
    DEV_model="${model:-$DEVICE}"
    DEV_density="$density"; DEV_miui_resolution="$resolution"
    DEV_fod_location="$location"; DEV_fod_size="$size"; DEV_fod_target="$target"
    DEV_marketname="${market:-$DEV_name}"
    DEV_camera_gdrive_id="$DEFAULT_CAMERA_GDRIVE_ID"
    AUTO_PROFILE=1
}

detect_device_profile() {
    local candidates=() key value config profile
    for key in ro.product.odm.model ro.product.vendor.model ro.product.model \
        ro.product.odm.device ro.product.vendor.device ro.product.device ro.build.product; do
        while IFS= read -r value; do
            [ -n "$value" ] && candidates+=("$value")
        done < <(stock_property_values "$key")
    done
    [ ${#candidates[@]} -gt 0 ] || return 1
    for config in "$HERE"/devices/*/device.conf; do
        [ -f "$config" ] || continue
        profile="$(basename "$(dirname "$config")")"
        [ "$profile" != "_template" ] || continue
        if profile_matches_values "$config" "${candidates[@]}"; then
            printf '%s\n' "$profile"
            return 0
        fi
    done
    printf 'stock identifiers: %s\n' "$(IFS=', '; echo "${candidates[*]}")" >&2
    return 1
}

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
# append a fixes/ block (comments/blanks stripped) under a header, idempotently
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
notify_stage download
STOCK_SRC="$(resolve_input "$STOCK" "$DL" stock)"
HOS4_SRC="$(resolve_input "$HOS4" "$DL" hyperos)"

log "== extracting payloads =="
notify_stage unpack
get_images "$STOCK_SRC" "$DL/stock_img" stock vendor odm
get_images "$HOS4_SRC" "$DL/hyperos_img" hyperos system system_ext product mi_ext

log "== unpacking stock identity =="
unpack_erofs "$IMG_vendor" "$WORK"
unpack_erofs "$IMG_odm" "$WORK"
if [ -z "$DEVICE" ]; then
    log "== detecting OnePlus device profile =="
    DEVICE="$(detect_device_profile 2>/dev/null || true)"
    if [ -n "$DEVICE" ]; then
        load_device_config
        log "== target profile: ${DEV_name:-$DEVICE} ($DEVICE) =="
    else
        log "== no verified profile matched; generating an automatic profile =="
        configure_automatic_profile
        log "== target profile: ${DEV_name:-$DEVICE} ($DEVICE; automatic) =="
    fi
else
    load_device_config
    log "== target profile: ${DEV_name:-$DEVICE} ($DEVICE; manual override) =="
fi
[ -n "$NAME" ] || NAME="HyperOS-$DEVICE-port"
mkdir -p "$HERE/build_info"
printf '%s\n' "${DEV_name:-$DEVICE}" > "$HERE/build_info/device_name.txt"
printf '%s\n' "${DEV_model:-$DEVICE}" > "$HERE/build_info/device_model.txt"
printf '%s\n' "$DEVICE" > "$HERE/build_info/device_code.txt"
# Version information is written after reading the donor properties.

log "== unpacking remaining images =="
unpack_erofs "$IMG_system" "$WORK"
unpack_erofs "$IMG_system_ext" "$WORK"
unpack_erofs "$IMG_product" "$WORK"
unpack_erofs "$IMG_mi_ext" "$WORK"
MIEXT="$WORK/mi_ext"
DONOR_SDK="$("$PY" "$HERE/lib/port_compat.py" sdk "$WORK")"
ACE_FULL=0
APEX_ROOT=""
if [ "$DEVICE" = "OnePlusAce3V" ] && [[ "$DONOR_SDK" = "34" || "$DONOR_SDK" = "35" || "$DONOR_SDK" = "36" ]]; then
    ACE_FULL=1
    load_device_config "$HERE/devices/$DEVICE/android-$DONOR_SDK/device.conf"
    # system_ext APEX comes from matching stock; system mainline modules stay
    # with the donor. Vendor/odm keep the requested hardware base.
    APEX_SRC="$STOCK_SRC"
    [ -z "$APEX_STOCK" ] || APEX_SRC="$(resolve_input "$APEX_STOCK" "$DL" apex_stock)"
    APEX_PARTS=(system_ext)
    if [ "$DONOR_SDK" = "34" ] && [ -f "$WORK/product/priv-app/GmsCore/GmsCore.apk" ]; then
        # SDK34 login was blocked by the donor's invalid Chimera module set.
        # The tested stock Google bundle resides in Ace 3V's my_bigball.
        APEX_PARTS+=(my_bigball)
    fi
    get_images "$APEX_SRC" "$DL/apex_stock_img" apex_stock "${APEX_PARTS[@]}"
    APEX_ROOT="$WORK/_stock_apex"
    mkdir -p "$APEX_ROOT"
    unpack_erofs "$IMG_system_ext" "$APEX_ROOT"
    if [ "${#APEX_PARTS[@]}" -gt 1 ]; then
        unpack_erofs "$IMG_my_bigball" "$APEX_ROOT"
    fi
    [ "$("$PY" "$HERE/lib/port_compat.py" sdk "$APEX_ROOT")" = "$DONOR_SDK" ] || die "stock APEX SDK does not match donor SDK $DONOR_SDK; supply a matching --apex-stock"
    command -v secilc >/dev/null || die "Ace 3V SDK34/35/36 requires secilc (run requirements.sh)"
    if [[ "$DONOR_SDK" = "34" || "$DONOR_SDK" = "35" ]]; then
        command -v seinfo >/dev/null || die "Android 14/15 requires seinfo to verify zero permissive domains (run requirements.sh)"
    fi
fi
printf '%s\n' "HyperOS / Android SDK $DONOR_SDK / OnePlus base" > "$HERE/build_info/rom_version.txt"
notify_stage build

# 12-step assembly
PRODUCT="$WORK/product"; SYS="$WORK/system/system"
SYSEXT="$WORK/system_ext"; VENDOR="$WORK/vendor"; ODM="$WORK/odm"
PROD_BP="$PRODUCT/etc/build.prop"

log "[1-2] assembling mi_ext and preserving source metadata"
run "$PY" "$HERE/lib/port_compat.py" assemble "$WORK" --device "$DEVICE"

log "[3] system/system/build.prop: home + dexopt"
apply_fix "$SYS/build.prop" "# $SIG" system.build.prop

log "[4] tagging ro.mi.os.version.incremental with | $SIG"
tag_incremental "$PROD_BP"

# Pangu was moved with its metadata by port_compat.py.

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
[ "$ACE_FULL" -eq 1 ] || rm -rf "$SYSEXT/priv-app/qcrilmsgtunnel"

# SHARED_DEVICE_CAMERA_SOURCE
CAMERA_SOURCE="${DEV_camera_source:-gdrive}"
case "$CAMERA_SOURCE" in
    donor)
        log "[12] keeping HyperOS donor MiuiCamera for $DEVICE"
        ;;
    gdrive|"")
        log "[12] removing product/priv-app/MiuiCamera (replaced from RES)"
        rm -rf "$PRODUCT/priv-app/MiuiCamera"

        # fetch MiuiCamera into RES if missing (too big for git)
        CAM="$RES/product/priv-app/MiuiCamera/MiuiCamera.apk"
        if [ ! -f "$CAM" ]; then
            log "[RES] MiuiCamera not present; downloading from Google Drive"
            mkdir -p "$RES/product/priv-app"
            run "$PY" "$HERE/lib/gdrive.py" "${DEV_camera_gdrive_id:-$DEFAULT_CAMERA_GDRIVE_ID}" "$RES/_MiuiCamera.zip"
            run unzip -o -q "$RES/_MiuiCamera.zip" -d "$RES/product/priv-app/"
            rm -f "$RES/_MiuiCamera.zip"
            [ -f "$CAM" ] || die "camera zip did not contain MiuiCamera/MiuiCamera.apk"
        fi
        ;;
    *)
        die "unsupported camera_source '$CAMERA_SOURCE' (expected donor or gdrive)"
        ;;
esac

# RES overlay
log "[RES] overlaying RES files"
# Keep camera_source=donor effective even if RES has a cached generic camera.
if [ "$CAMERA_SOURCE" = "donor" ] && [ -d "$PRODUCT/priv-app/MiuiCamera" ]; then
    mv "$PRODUCT/priv-app/MiuiCamera" "$WORK/_donor_camera"
fi
if [ -d "$RES" ]; then
    for part in "$RES"/*/; do
        [ -d "$part" ] || continue
        pname="$(basename "$part")"
        mkdir -p "$WORK/$pname"
        cp -a --remove-destination "$part". "$WORK/$pname/"
    done
fi
if [ "$CAMERA_SOURCE" = "donor" ]; then
    rm -rf "$PRODUCT/priv-app/MiuiCamera"
    [ ! -d "$WORK/_donor_camera" ] || mv "$WORK/_donor_camera" "$PRODUCT/priv-app/MiuiCamera"
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

# device folder: displayconfig + device_features overlay, then scalar overrides
DDIR="$HERE/devices/$DEVICE"
[ "$ACE_FULL" -eq 0 ] || DDIR="$DDIR/android-$DONOR_SDK"
[ "${AUTO_PROFILE:-0}" -eq 1 ] && DDIR=""
log "[DEVICE] ${DEV_name:-$DEVICE} ($DEVICE)"
if [ -d "$DDIR/displayconfig" ]; then
    mkdir -p "$WORK/product/etc/displayconfig"
    cp -a "$DDIR/displayconfig/." "$WORK/product/etc/displayconfig/"
elif [ "${AUTO_PROFILE:-0}" -eq 1 ] && [ -d "$VENDOR/etc/displayconfig" ]; then
    # Prefer the stock panel definition over a generic one when it is present.
    mkdir -p "$WORK/product/etc/displayconfig"
    cp -a "$VENDOR/etc/displayconfig/." "$WORK/product/etc/displayconfig/"
fi
DEVNAME="$(grep -m1 -E '^ro\.product\.(vendor\.)?device=' "$VENDOR/build.prop" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
if [ "$ACE_FULL" -eq 0 ] && [ -f "$DDIR/device_features.xml" ] && [ -n "$DEVNAME" ]; then
    mkdir -p "$WORK/product/etc/device_features"
    cp "$DDIR/device_features.xml" "$WORK/product/etc/device_features/$DEVNAME.xml"
    log "    device_features -> $DEVNAME.xml"
elif [ "${AUTO_PROFILE:-0}" -eq 1 ] && [ -n "$DEVNAME" ] && [ ! -f "$WORK/product/etc/device_features/$DEVNAME.xml" ]; then
    # HyperOS feature files are donor-side resources. Reuse one from the
    # selected donor and rename it for the OnePlus codename rather than aborting
    # an otherwise complete automatic build.
    FEATURE_TEMPLATE="$(find "$WORK/product/etc/device_features" -maxdepth 1 -type f -name '*.xml' 2>/dev/null | head -n1 || true)"
    if [ -n "$FEATURE_TEMPLATE" ]; then
        cp "$FEATURE_TEMPLATE" "$WORK/product/etc/device_features/$DEVNAME.xml"
        log "    device_features donor fallback -> $DEVNAME.xml"
    fi
fi
# SHARED_DEVICE_FEATURE_OVERRIDES
if [ -n "${DEV_fod_solution:-}" ] && [ -n "$DEVNAME" ]; then
    FEATURE_FILE="$WORK/product/etc/device_features/$DEVNAME.xml"
    if [ -f "$FEATURE_FILE" ]; then
        if grep -q '<integer name="fod_solution">' "$FEATURE_FILE"; then
            sed -i -E "s#<integer name=\"fod_solution\">[^<]*</integer>#<integer name=\"fod_solution\">${DEV_fod_solution}</integer>#" "$FEATURE_FILE"
            log "    device_features: fod_solution=${DEV_fod_solution}"
        else
            sed -i "s#</features>#    <integer name=\"fod_solution\">${DEV_fod_solution}</integer>\n</features>#" "$FEATURE_FILE"
            log "    device_features: added fod_solution=${DEV_fod_solution}"
        fi
    fi
fi
prop_set "$VENDOR/build.prop" "persist.vendor.sys.fp.fod.location.X_Y" "${DEV_fod_location:-}"
prop_set "$VENDOR/build.prop" "persist.vendor.sys.fp.fod.size.width_height" "${DEV_fod_size:-}"
prop_set "$VENDOR/build.prop" "persist.vendor.sys.fp.fod.us.target" "${DEV_fod_target:-}"
prop_set "$VENDOR/build.prop" "persist.sys.miui_resolution" "${DEV_miui_resolution:-}"
# SHARED_DEVICE_PANEL_POLICY
if [ "${DEV_ltpo:-}" = "false" ]; then
    log "    panel policy: fixed-mode/non-LTPO (${DEV_refresh_rates:-120,90,60})"
    prop_set "$VENDOR/build.prop" "ro.vendor.mi_sf.ltpo.support" "false"
    prop_set "$VENDOR/build.prop" "ro.vendor.mi_sf.support_gradient_idleframerate" "false"
    prop_set "$VENDOR/build.prop" "ro.vendor.mi_sf.aod_mode_ddic_refresh_rate" "60"
    prop_set "$VENDOR/build.prop" "ro.vendor.display.primary_idle_refresh_rate" "60"
    prop_set "$VENDOR/build.prop" "ro.vendor.display.idle_default_fps" "60"
    prop_set "$VENDOR/build.prop" "ro.vendor.display.dynamic_refresh_rate" "${DEV_refresh_rates:-120,90,60}"
fi
prop_set "$PROD_BP" "persist.miui.density_v2" "${DEV_density:-}"
prop_set "$PROD_BP" "ro.sf.lcd_density" "${DEV_density:-}"
prop_set "$ODM/build.prop" "ro.product.odm.marketname" "${DEV_marketname:-}"

# Shared, version-aware finishing pass; no device-side test/log wrappers.
COMPAT_ARGS=(finish "$WORK" --device "$DEVICE")
[ -z "$APEX_ROOT" ] || COMPAT_ARGS+=(--apex-stock "$APEX_ROOT")
[ -z "$ADB_KEY" ] || COMPAT_ARGS+=(--adb-key "$ADB_KEY")
case "$FORCE_ADB" in 1|true|yes|on) COMPAT_ARGS+=(--force-adb);; esac
run "$PY" "$HERE/lib/port_compat.py" "${COMPAT_ARGS[@]}"
cp "$WORK/port_compat.json" "$HERE/build_info/port_compat.json"

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
if [ "$ASSEMBLE_ONLY" -eq 1 ]; then
    log "assembled: $WORK (images were not packed)"
    exit 0
fi
log "== packing images =="
notify_stage pack
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
    # The image now owns the complete tree. Free intermediate space before
    # the next image/ZIP; --keep-work retains trees for inspection.
    [ "$KEEP_WORK" -eq 1 ] || rm -rf "$WORK/$part"
done

# uncompressed zip
ZIP="$OUT/$NAME.zip"
log "packing uncompressed zip: $ZIP"
rm -f "$ZIP"
( cd "$OUT" && zip -0 -X -j "$ZIP" "${IMGS[@]##*/}" >/dev/null )
printf '%s\n' "$(basename "$ZIP")" > "$HERE/build_info/output_zip.txt"
log "done: $ZIP"

[ "$KEEP_WORK" -eq 1 ] || rm -rf "$DL"
