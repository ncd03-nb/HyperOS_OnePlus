#!/usr/bin/env bash
# Installs deps for Arch/Debian/Ubuntu/Fedora and fetches payload-dumper-go
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

# ssut/payload-dumper-go — MIT. Static Go binary, no runtime deps.
PDG_VERSION="1.3.0"
PDG_ARCH="linux_amd64"
install_payload_dumper() {
    local dest="$HERE/bin/Linux/x86_64/payload-dumper-go"
    if [ -x "$dest" ] || command -v payload-dumper-go >/dev/null 2>&1; then
        echo "== payload-dumper-go already present =="
        return 0
    fi
    local url="https://github.com/ssut/payload-dumper-go/releases/download/${PDG_VERSION}/payload-dumper-go_${PDG_VERSION}_${PDG_ARCH}.tar.gz"
    local tmp; tmp="$(mktemp -d)"
    echo "== fetching payload-dumper-go ${PDG_VERSION} =="
    if curl -fsSL "$url" -o "$tmp/pdg.tar.gz"; then
        tar -xzf "$tmp/pdg.tar.gz" -C "$tmp"
        local bin; bin="$(find "$tmp" -name 'payload-dumper-go' -type f | head -n1)"
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
