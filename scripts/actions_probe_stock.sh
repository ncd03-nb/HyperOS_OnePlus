#!/usr/bin/env bash
# GitHub Actions helper: download and inspect the stock OnePlus/OPPO ROM before
# port.sh runs.  The resulting device.conf is consumed only when no verified
# devices/<profile> matches the stock identifiers.

set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
PDR="$HERE/bin/Linux/x86_64/payload_dumper"
EXTRACT="$HERE/bin/Linux/x86_64/extract.erofs"

SRC="${1:?usage: actions_probe_stock.sh <stock-rom> [out-dir]}"
OUT="${2:-$HERE/.auto_probe}"
DL="$OUT/download"
IMG="$OUT/images"
ROOT="$OUT/root"
REPORT="$OUT/report"
mkdir -p "$DL" "$IMG" "$ROOT" "$REPORT"

log() { printf '[AUTO-PROBE] %s\n' "$*"; }
warn() { printf '::warning::[AUTO-PROBE] %s\n' "$*" >&2; }

decrypt_oplus_link() {
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
        log "OPlus link decryption failed; retrying ($attempt/5)" >&2
        sleep 2
    done
    return 1
}

resolve_stock() {
    local src="$1" url out
    case "$src" in
        http://*|https://*)
            url="$src"
            if [[ "$url" == *"downloadCheck"* ]]; then
                log "resolving protected OPlus OTA URL" >&2
                url="$(decrypt_oplus_link "$url")" || { warn "could not resolve protected OPlus URL"; return 1; }
            fi
            case "${url%%\?*}" in
                *.bin) out="$DL/stock.bin" ;;
                *) out="$DL/stock.zip" ;;
            esac
            log "downloading stock ROM once for probe + build" >&2
            if command -v aria2c >/dev/null 2>&1; then
                aria2c -x16 -s16 -o "$(basename "$out")" -d "$DL" "$url" >&2
            elif command -v curl >/dev/null 2>&1; then
                curl -L --fail -o "$out" "$url" >&2
            else
                wget -O "$out" "$url" >&2
            fi
            printf '%s\n' "$out"
            ;;
        *)
            printf '%s\n' "$src"
            ;;
    esac
}

STOCK="$(resolve_stock "$SRC")"
[ -e "$STOCK" ] || { warn "stock input not found: $STOCK"; exit 1; }

# payload-dumper-rust is installed by requirements.sh and reads a ROM zip or
# payload.bin directly.  Extract each partition independently so a missing
# optional partition never aborts the probe.
PARTS=(vendor odm my_product system_ext product)
EXTRACTED=0
PAYLOAD_CACHE=""

ensure_payload_cache() {
    [ -n "$PAYLOAD_CACHE" ] && { printf '%s\n' "$PAYLOAD_CACHE"; return 0; }
    case "$STOCK" in
        *.zip)
            local member
            member="$(unzip -Z1 "$STOCK" 2>/dev/null | grep -m1 '\(^\|/\)payload\.bin$' || true)"
            [ -n "$member" ] || return 1
            PAYLOAD_CACHE="$DL/payload.bin"
            unzip -p "$STOCK" "$member" > "$PAYLOAD_CACHE"
            ;;
        *) PAYLOAD_CACHE="$STOCK" ;;
    esac
    printf '%s\n' "$PAYLOAD_CACHE"
}

for part in "${PARTS[@]}"; do
    partdir="$IMG/$part"
    mkdir -p "$partdir"
    image=""

    if [ -d "$STOCK" ]; then
        [ -f "$STOCK/$part.img" ] && image="$STOCK/$part.img"
    elif [ -x "$PDR" ] || command -v payload_dumper >/dev/null 2>&1; then
        tool="$PDR"; [ -x "$tool" ] || tool="$(command -v payload_dumper)"
        if "$tool" "$STOCK" -o "$partdir" -i "$part" >/dev/null 2>&1; then
            [ -f "$partdir/$part.img" ] && image="$partdir/$part.img"
        fi
    else
        payload="$(ensure_payload_cache || true)"
        if [ -n "$payload" ] && "$PY" "$HERE/lib/payload_extractor.py" -o "$partdir" -p "$part" "$payload" >/dev/null 2>&1; then
            [ -f "$partdir/$part.img" ] && image="$partdir/$part.img"
        fi
    fi

    if [ -z "$image" ]; then
        warn "$part.img is not present; continuing without it"
        continue
    fi

    log "unpacking stock $part.img for metadata"
    if "$EXTRACT" -i "$image" -x -s -f -o "$ROOT" >/dev/null 2>&1; then
        EXTRACTED=$((EXTRACTED + 1))
    else
        warn "failed to unpack $part.img; continuing"
    fi
done

[ "$EXTRACTED" -gt 0 ] || { warn "no stock partitions could be unpacked for probing"; exit 1; }

log "scanning unpacked stock ROM"
"$PY" "$HERE/scripts/probe_unpacked.py" "$ROOT" --out-dir "$REPORT" --shell > "$REPORT/probe.env"
cat "$REPORT/probe_report.txt"

# GitHub step outputs.  The build reuses the already-downloaded stock archive,
# avoiding a second network download.
if [ -n "${GITHUB_OUTPUT:-}" ]; then
    {
        printf 'stock_path=%s\n' "$STOCK"
        printf 'probe_conf=%s\n' "$REPORT/device.conf.generated"
        printf 'probe_json=%s\n' "$REPORT/probe.json"
        printf 'probe_report=%s\n' "$REPORT/probe_report.txt"
    } >> "$GITHUB_OUTPUT"
fi

# Large probe images/root are no longer needed. Keep only the tiny reports.
rm -rf "$IMG" "$ROOT"
log "probe complete: $REPORT"
