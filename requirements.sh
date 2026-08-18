#!/usr/bin/env bash
# Installs deps for Arch/Debian/Ubuntu/Fedora and fetches payload-dumper-rust
# (optional; the porter has a built-in fallback). Run before ./port.sh.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# packages, by role, resolved per distro below
PKGS_COMMON="python3 unzip zip tar coreutils findutils"

need_sudo() { if [ "$(id -u)" -ne 0 ]; then echo "sudo"; fi; }
SUDO="$(need_sudo)"

detect_distro() {
    if [ -r /etc/os-release ]; then
        . /etc/os-release
        echo "${ID:-unknown} ${ID_LIKE:-}"
    else
        echo "unknown"
    fi
}

install_packages() {
    local id_line; id_line="$(detect_distro)"
    echo "== detected: $id_line =="
    case "$id_line" in
        *arch*|*manjaro*|*cachyos*|*endeavour*)
            $SUDO pacman -Sy --needed --noconfirm python aria2 curl unzip zip tar
            ;;
        *debian*|*ubuntu*|*mint*|*pop*)
            $SUDO apt-get update
            $SUDO apt-get install -y python3 aria2 curl unzip zip tar
            ;;
        *fedora*|*rhel*|*centos*)
            $SUDO dnf install -y python3 aria2 curl unzip zip tar
            ;;
        *)
            echo "unsupported distro; install manually: python3 aria2 curl unzip zip tar"
            return 1
            ;;
    esac
}

# rhythmcache/payload-dumper-rust — reads payload.bin or a ROM zip directly.
PDR_TAG="payload-dumper-rust-v0.8.4"
PDR_ASSET="payload_dumper-linux-x86_64.zip"
install_payload_dumper() {
    local dest="$HERE/bin/Linux/x86_64/payload_dumper"
    if [ -x "$dest" ] || command -v payload_dumper >/dev/null 2>&1; then
        echo "== payload_dumper already present =="
        return 0
    fi
    local url="https://github.com/rhythmcache/payload-dumper-rust/releases/download/${PDR_TAG}/${PDR_ASSET}"
    local tmp; tmp="$(mktemp -d)"
    echo "== fetching payload-dumper-rust ${PDR_TAG} =="
    if curl -fsSL "$url" -o "$tmp/pdr.zip"; then
        unzip -o -q "$tmp/pdr.zip" -d "$tmp"
        local bin; bin="$(find "$tmp" -name 'payload_dumper' -type f | head -n1)"
        if [ -n "$bin" ]; then
            mkdir -p "$(dirname "$dest")"
            install -m 0755 "$bin" "$dest"
            echo "   installed -> $dest"
        fi
    else
        echo "   download failed; the porter will use the built-in Python extractor"
    fi
    rm -rf "$tmp"
}

install_packages
install_payload_dumper
chmod +x "$HERE/bin/Linux/x86_64/mkfs.erofs" "$HERE/bin/Linux/x86_64/extract.erofs" 2>/dev/null || true
echo "== requirements installed. now run:  ./port.sh --stock <op13-rom> --hyperos <hyperos-rom> =="
