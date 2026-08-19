#!/usr/bin/env python3
"""Patch port.sh on the Actions runner with CI-only safety/auto-probe hooks.

The repository source stays readable while Actions can consume a generated
stock-ROM profile when no verified devices/<profile> matches.
"""
from pathlib import Path
import os
import sys

path = Path(sys.argv[1] if len(sys.argv) > 1 else "port.sh")
text = path.read_text("utf-8")
force_adb = os.environ.get("FORCE_ADB", "0").strip().lower() in {"1", "true", "yes", "on"}

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

# A device may keep the MiuiCamera that belongs to the selected donor instead
# of forcing the generic Google-Drive replacement.  This is useful when the
# replacement APK expects a different camera-vendor metadata layout (Ace 3V
# currently crashes CameraSetup with "invalid buffer length 68").
camera_marker = "# ACTIONS_DEVICE_CAMERA_SOURCE\n"
if camera_marker not in text:
    old_camera = r'''log "[12] removing product/priv-app/MiuiCamera (replaced from RES)"
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
'''
    new_camera = r'''# ACTIONS_DEVICE_CAMERA_SOURCE
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
'''
    if old_camera not in text:
        raise SystemExit("could not locate MiuiCamera replacement block")
    text = text.replace(old_camera, new_camera, 1)

# Device-specific FeatureParser overrides.  Keep the large donor-derived XML
# readable in git while allowing verified scalar values to live in device.conf.
# In particular, Ace 3V originally used fod_solution=2 based on an unverified
# assumption.  Current Xiaomi optical-FOD profiles (e.g. houji) use solution 3.
feature_marker = "# ACTIONS_DEVICE_FEATURE_OVERRIDES\n"
if feature_marker not in text:
    anchor = 'prop_set "$VENDOR/build.prop" "persist.vendor.sys.fp.fod.location.X_Y" "${DEV_fod_location:-}"\n'
    if anchor not in text:
        raise SystemExit("could not locate FOD scalar override anchor")
    block = r'''# ACTIONS_DEVICE_FEATURE_OVERRIDES
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
'''
    text = text.replace(anchor, block + anchor, 1)

# fixes/vendor.build.prop originated with the LTPO OnePlus 13 profile.  Do not
# leave its 10/30Hz LTPO policy enabled on a fixed-mode panel such as Ace 3V;
# that causes the FOD session to bounce between 60 and 120 Hz while the finger
# is down. Device profiles can opt out with ltpo=false.
panel_marker = "# ACTIONS_DEVICE_PANEL_POLICY\n"
if panel_marker not in text:
    anchor = 'prop_set "$PROD_BP" "persist.miui.density_v2" "${DEV_density:-}"\n'
    if anchor not in text:
        raise SystemExit("could not locate density scalar override anchor")
    block = r'''# ACTIONS_DEVICE_PANEL_POLICY
if [ "${DEV_ltpo:-}" = "false" ]; then
    log "    panel policy: fixed-mode/non-LTPO (${DEV_refresh_rates:-120,90,60})"
    prop_set "$VENDOR/build.prop" "ro.vendor.mi_sf.ltpo.support" "false"
    prop_set "$VENDOR/build.prop" "ro.vendor.mi_sf.support_gradient_idleframerate" "false"
    prop_set "$VENDOR/build.prop" "ro.vendor.mi_sf.aod_mode_ddic_refresh_rate" "60"
    prop_set "$VENDOR/build.prop" "ro.vendor.display.primary_idle_refresh_rate" "60"
    prop_set "$VENDOR/build.prop" "ro.vendor.display.idle_default_fps" "60"
    prop_set "$VENDOR/build.prop" "ro.vendor.display.dynamic_refresh_rate" "${DEV_refresh_rates:-120,90,60}"
fi
'''
    text = text.replace(anchor, block + anchor, 1)

# Optional Actions-only debug mode.  This is intentionally injected after all
# device scalar overrides and before SELinux/fs-config synthesis so the new rc
# file receives normal system_file metadata when the images are repacked.
force_marker = "# ACTIONS_FORCE_ADB\n"
if force_adb and force_marker not in text:
    anchor = 'prop_set "$ODM/build.prop" "ro.product.odm.marketname" "${DEV_marketname:-}"\n'
    if anchor not in text:
        raise SystemExit("could not locate device override anchor for force-ADB patch")
    block = r'''

# ACTIONS_FORCE_ADB
log "[DEBUG] forcing ADB for early boot diagnostics"
PROP_DEFAULT="$SYS/etc/prop.default"
[ -f "$PROP_DEFAULT" ] || PROP_DEFAULT="$SYS/build.prop"
prop_set "$PROP_DEFAULT" "ro.debuggable" "1"
prop_set "$PROP_DEFAULT" "ro.secure" "0"
prop_set "$PROP_DEFAULT" "ro.adb.secure" "0"
prop_set "$SYS/build.prop" "persist.sys.usb.config" "adb"
prop_set "$PROD_BP" "persist.sys.usb.config" "adb"
prop_set "$VENDOR/build.prop" "persist.vendor.usb.config" "adb"
mkdir -p "$SYS/etc/init"
cat > "$SYS/etc/init/hyperos_force_adb.rc" <<'EOF'
# Generated by HyperOS_OnePlus GitHub Actions debug mode.
# Keep USB in ADB-only mode so a userspace boot hang can still be diagnosed.
on early-init
    setprop persist.sys.usb.config adb
    setprop persist.vendor.usb.config adb

on boot
    setprop persist.sys.usb.config adb
    setprop persist.vendor.usb.config adb
    setprop sys.usb.config adb
    start adbd

on post-fs-data
    setprop persist.sys.usb.config adb
    setprop persist.vendor.usb.config adb
    setprop sys.usb.config adb
    start adbd
EOF
'''
    text = text.replace(anchor, anchor + block, 1)

path.write_text(text, "utf-8")
print(f"patched {path}; force_adb={int(force_adb)}")
