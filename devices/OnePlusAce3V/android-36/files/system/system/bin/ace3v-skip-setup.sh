#!/system/bin/sh
# Runs asynchronously from init; SettingsProvider may not be ready at boot.
# Bounded retries avoid blocking init or depending on boot_completed.
attempt=0
while [ "$attempt" -lt 120 ]; do
    provisioned=$(/system/bin/settings get global device_provisioned 2>/dev/null)
    case "$provisioned" in
        null|0|1)
            if /system/bin/settings put global device_provisioned 1 &&
               /system/bin/settings --user 0 put secure user_setup_complete 1 &&
               /system/bin/settings --user 0 put secure miui_setup_complete 1 &&
               /system/bin/settings put global development_settings_enabled 1 &&
               /system/bin/settings put global adb_enabled 1; then
                # Also cover an updated Provision APK retained in existing /data.
                /system/bin/pm disable-user --user 0 com.android.provision >/dev/null 2>&1
                /system/bin/setprop persist.vendor.usb.config adb
                /system/bin/setprop persist.sys.usb.config adb
                /system/bin/setprop sys.usb.config adb
                /system/bin/setprop sys.ace3v.provisioned 1
                /system/bin/am start --user 0 -a android.intent.action.MAIN -c android.intent.category.HOME
                exit 0
            fi
            ;;
    esac
    attempt=$((attempt + 1))
    /system/bin/sleep 2
done
/system/bin/setprop sys.ace3v.provisioned failed
exit 1
