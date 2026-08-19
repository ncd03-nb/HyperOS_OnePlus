#!/usr/bin/env python3
"""Patch port.sh on the Actions runner with CI-only safety/auto-probe hooks.

The repository source stays readable while Actions can consume a generated
stock-ROM profile when no verified devices/<profile> matches.
"""
from pathlib import Path
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else "port.sh")
text = path.read_text("utf-8")

# GNU cp refuses to write through a dangling destination symlink. RES overlays
# intentionally replace destination entries, so remove that entry first.
old_cp = '        cp -a "$part". "$WORK/$pname/"'
new_cp = '        cp -a --remove-destination "$part". "$WORK/$pname/"'
if old_cp in text:
    text = text.replace(old_cp, new_cp, 1)
elif new_cp not in text:
    raise SystemExit("could not locate RES overlay cp command")

# When auto-probe produced a device.conf, use it only in the existing fallback
# path (i.e. after verified profile matching already failed). This preserves all
# hand-tuned profiles while making unknown OnePlus devices data-driven.
needle = "configure_automatic_profile() {\n"
marker = "    # ACTIONS_AUTO_PROFILE_CONF\n"
if marker not in text:
    if needle not in text:
        raise SystemExit("could not locate configure_automatic_profile()")
    hook = r'''configure_automatic_profile() {
    # ACTIONS_AUTO_PROFILE_CONF
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
'''
    text = text.replace(needle, hook, 1)

path.write_text(text, "utf-8")
print(f"patched {path}")
