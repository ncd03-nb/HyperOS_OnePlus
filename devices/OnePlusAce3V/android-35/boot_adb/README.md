# Android 15 boot ADB without host authentication

This directory ships donor-specific daemons for Ace 3V HyperOS Android 15.
It sets `ro.adb.secure=0`, retaining `ro.secure=1`, `ro.debuggable=0`,
`service.adb.root=0`, shell UID2000 and SELinux Enforcing.

The default profile's locked user-build daemon skips the authentication property. The
patched ARM64 executable replaces only the four-byte instruction at file
offset `0xa8d50`: `a0020034` becomes `1f2003d5` (NOP). Privilege-dropping code
is unchanged. `profile.json` records original and patched executable hashes
and the exact signed compressed APEX hash. The porter rejects another donor
APEX or a modified profile asset rather than mixing incompatible binaries.

The signed APEX remains unchanged. Init bind-mounts `/system/bin/ace3v-adbd`
onto `/apex/com.android.adbd/bin/adbd` after `apex.all.ready=true` and restarts
the original service. This retains its APEX linker namespace and service
definition. One SELinux rule allows init to mount onto `adbd_exec`; it is
added before compiling the enforcing policy. The source executable is
root-owned 0755, labeled `adbd_exec` in both image metadata and runtime
file contexts.

The existing Qualcomm FunctionFS startup and guarded gadget-binding events
remain. ADB starts before setup and boot completion, without a host key or
authorization dialog. ADB security resetprop/xeutoolbox calls are removed;
other donor property resets remain. The daemon/profile carries no host keys.

The same executable and init flow were tested in the local ROM with working
no-auth ADB, shell UID2000 and Enforcing. This is evidence for the recorded
donor only. New donor support requires examining and verifying that donor's
daemon and dependencies, not applying the fixed offset to an unknown binary.

The porter checks `profile.json` and `variants/*/profile.json`, selecting exactly
one complete APEX filename/hash match. Missing or ambiguous matches fail before
ADB configuration changes. A matching daemon asset must also pass its SHA256.

`variants/hyperos2-nuwa-os2.0.219.0` supports stock nuwa
`OS2.0.219.0.VMBCNXM` / SDK35 using `com.android.adbd.capex` with SHA256
`d7562ff524e9250d99d5e42c4f4873905ac73568eebb6282b1a82cfe2dbb8076`.
Its original adbd is extracted from that APEX, not from the default Google APEX.
At offset `0xa2224`, `80020034` (`cbz w0`, after the debuggable query) becomes
`1f2003d5` (NOP), allowing the existing `ro.adb.secure` property lookup.
The property result is stored to `auth_required`; the minijail UID/GID2000 calls
and all other executable bytes remain unchanged. The daemon continues using the
libraries from its original signed APEX. Those library hashes are recorded in
the variant descriptor. Static inspection and build checks are complete;
R1 device checks now confirm boot-animation ADB, shell UID2000, SELinux Enforcing
and security properties 1/0/0. R2 adds the missing product privileged permission
grants and now completes boot, confirmed by the user and `sys.boot_completed=1`.

The SDK35 porter also removes the full SetupWizard directory and its image
metadata, disables setupwizard mode, and keeps minimal Provision plus the
system-UID provisioning service. It does not disable PermissionController.
Known PermissionController `RANGING` / `checkOpRawNoThrow` compatibility
crashes remain outside this change.
