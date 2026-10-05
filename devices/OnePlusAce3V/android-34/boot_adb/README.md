# Android 14 boot ADB without host authentication

This profile is for the recorded fuxi HyperOS 1 Android 14 donor's signed
`com.android.adbd.capex`. It preserves the APEX and replaces only the four-byte
ARM64 authentication-property gate at file/virtual offset `0xa21cc` in its
daemon: `a0020034` (CBZ) becomes `1f2003d5` (NOP). Source and patched executable
hashes plus the original CAPEX hash are recorded in `profile.json`.

The startup disassembly and Android 14 `daemon/main.cpp` show this gate skips
reading `ro.adb.secure` when the user build is locked. The patch allows the
configured `ro.adb.secure=0` to apply. It leaves `drop_privileges` unchanged:
`ro.secure=1`, `ro.debuggable=0` and `service.adb.root=0` retain shell UID2000.

Init bind-mounts `/system/bin/ace3v-adbd` onto the active APEX daemon after
`apex.all.ready=true`, then restarts the standard service. Qualcomm FunctionFS
startup and guarded gadget-binding events remain. The executable uses
root:root, 0755 and `adbd_exec`; one init mounton rule is added before compiling
the enforcing policy. Unsupported CAPEX hashes fail the build.

The binary difference and enforcing policy have been checked locally. ADB
connection, shell UID and complete boot still need testing on the device;
Android 15 runtime observations do not count as Android 14 validation.

Source: https://android.googlesource.com/platform/packages/modules/adb/+/refs/tags/android-14.0.0_r1/daemon/main.cpp
