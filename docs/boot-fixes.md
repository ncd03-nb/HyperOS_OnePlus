# Shared Ace 3V boot fixes

`port.sh` and `.github/workflows/port.yml` both call `lib/port_compat.py finish`.
SDK34-36 always install no-auth boot ADB with normal shell privileges. The old
`--force-adb` flag remains accepted but does not grant root on these builds.

## Donor and region handling

The installer first checks the optional hash-verified daemon cache. Otherwise
it finds the donor's adbd APEX without assuming Google's or Xiaomi's filename:
`.capex`, a compressed `.apex`, an uncompressed `.apex`, or a flattened directory.
`debugfs` or `fsck.erofs` reads `/bin/adbd` from the payload. Run `requirements.sh`
to install these tools. The signed package and all of its libraries stay intact.

`adbd_patch.py` validates ARM64 ELF and resolves the `auth_required` byte using
ELF symbols, code addresses and GOT relocations. It clears the initializer and
changes stores to this exact byte into stores of zero. This also handles vendor
overriding `ro.adb.secure=1`. It does not patch the privilege-drop code or library
dependencies. `ro.secure=1`, `ro.debuggable=0` and `service.adb.root=0` are retained.
The build report records source/result hashes and every patched offset. An ELF
without a verifiable auth state is rejected; a foreign daemon is never guessed.

The shared init flow binds the daemon after APEX activation and retries after
the vendor's `zygote-start` FunctionFS setup. It does not wait for bind mounts,
force `sys.usb.ffs.ready`, or depend on SetupWizard/`sys.boot_completed`.
Runtime resetprop/xeutoolbox commands touching ADB security are removed; unrelated
property changes remain. SDK34/35 keep their enforcing policy. SDK36 retains
the existing policy choice and does not add a global setenforce command.

## Display regression from the October 12 device log

The installed SDK36 panel XML started at 7/8191 (`0.000854597`), but the donor
framework requested 3/8191 (`0.000366256`). `DisplayDeviceConfig` threw
`Min or max values are invalid` repeatedly in system_server during bootanimation.
The installed XML hash matched this repository's SDK36 profile, identifying the
port profile as the source of this crash.

SDK34/35/36 profiles now add a zero-brightness, zero-nits anchor while retaining
all measured points. The shared installer validates finite, monotonic values and
coverage of [0, 1] before replacing product's display configuration. This prevents
that particular lower-bound exception when another donor region uses a different
minimum. It does not guarantee that every other donor framework change is compatible.

## Validation

Run `python3 -m unittest discover -s tests -v` on Linux. Tests include real cached
ARM64 daemons, GOT/RELR addressing, idempotency, exact changed-byte boundaries,
compressed/uncompressed regional packages, malformed inputs, shell properties,
init triggers and the previously failing brightness range.

The October 12 manual SDK36 auth-store patch was observed on the device as an
authorized transport with shell UID2000 even with vendor `ro.adb.secure=1`.
The generalized build patch additionally clears the auth initializer. A build
report keeps `runtime_boot_verified=false` until that output is tested on-device.
