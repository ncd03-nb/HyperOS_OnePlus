# MIUI14 Android 13 / Ace 3V

This profile carries the fixes verified with MIUI14 V14.0.27.0.TMRCNXM
(marble, SDK33), OnePlus Ace 3V SDK34 vendor/ODM, through DSU. Stock still
supplies vendor/ODM. The same `port.sh` and `port_compat.finish` dispatch are
used locally and in Actions. `--apex-stock` is not required for SDK33.

```bash
./port.sh --device OnePlusAce3V --stock /path/to/ace3v-android14.zip \
  --hyperos /path/to/miui14-marble.zip
```

The original case was a toolbuild port with generic `ossi` properties. Binary
hashes, rather than that generic device name, identify the supported libraries.
Other SDK33 donors or newer hardware bases fail with the mismatching path/hash
before this finishing pass writes compatibility files. Supporting a new binary
requires auditing its ABI and adding a verified profile; offsets from this one
must not be reused blindly.

## Applied fixes

- Preserve signed donor ART, runtime, Wi-Fi and adbd APEX. Derive the no-auth
  daemon from the actual donor; retain normal shell UID2000, `ro.secure=1`,
  `ro.debuggable=0` and SELinux Enforcing. No resetprop security overrides,
  permissive domains, root shell or parallel diagnostic adbd.
- Supply the real platform 34.0 mapping; namespace the old `vendor_service`
  attribute; compile and stamp the final vendor/ODM policy with zero permissive
  types. Restore the real PerfService domain mapping and its narrow HIDL/ready
  property permissions, fixing the app-start ANR that prevented keyboard input.
  Repeated identical vendor property-context entries are deduplicated; conflicting
  labels or value types still fail rather than silently changing an access route.
- Keep the A13 Binder NDK library. Provide two API34 functions through the
  audited donor-specific adapter, used only by allocator and sensor bridge.
  `sources/binder-compat.cpp` and its version script describe the implementation.
- Derive a primary-only zygote64 init file from the donor, keep arm64-only ABI,
  restore exact AIDL hardware service labels, and express the existing VINTF HALs
  in the A13 parser's schema without changing their target level or HAL contents.
- Skip physical-slot `update_verifier_nonencrypted` only in DSU. Physical boot
  keeps its original verifier and command order; BoringSSL guards are retained.
- Fix the donor SensorService LIGHT/PRESSURE dispatch and libvintf's Android14
  GKI FCM bound, preserving other sensor conversions and compatibility checks.
- Catch only ServiceSpecificException for optional Xiaomi supplicant commands;
  preserve successful results and unexpected exceptions. Bridge the stock
  supplicant V2 state callback to the donor's legacy serializer with real
  state/SSID/BSSID/FILS values, preserving PAC/canary/unwind and returned status.
- Use an empty unknown charging value when the Xiaomi MiCharge HAL is absent;
  preserve actual responses and all other DEX methods/hidden-API flags. Remove
  only caches for the changed framework JARs.
- Use the original SDK34 VNDK camera metadata C implementation: the same 48
  exported APIs, all 294 old tags and 17 additional standard tags. This fixes
  rejected metadata and the WebView camera-enumeration crash during Google login.
- Extract eight exact embedded Google modules from the unchanged signed donor
  GmsCore into its factory `m/container` directory. Independent modules remain.
- Remove SetupWizard and the optional startup MP3; retain boot animation and
  use the tested system-UID provisioning/slider helper and fixed-mode panel data.
  Optional OEM bind mounts do not acquire waits or sleeps.

## Package integrity and provenance

`profile.json` records input/output SHA256 and payload hashes. `patches/` are
BSDIFF40 deltas; the build applies them using bounded Python stdlib decoding.
`sources/` documents the adapter, callback and Java changes; `evidence/` contains
the narrow runtime/emulation checks. No original full ROM or private key is
included. `payloads/34.0.cil` is the actual API34 mapping, not renamed API33 CIL.

The VNDK34 APEX originated from the previously verified stock package SHA256
`dff3b689da21f14bfea267365ebba1d4e4fe67bd1496a165c66f9dcb187f923d`.
VNDK34 and Widevine need an APK-container minimum SDK of 33. Their tested outer
containers were re-signed; the original AVB-signed payload, embedded APEX public
key and APEX manifests are unchanged. No APEX/AVB verification bypass is added.
Only the already-tested public packages/delta are shipped; signing keys stay
outside the repository. The camera metadata implementation comes from that
unchanged VNDK payload.

## Validation limits

The device booted with Enforcing and shell UID2000. The user confirmed Gboard
typing, the original Wi-Fi network plus browsing, and a Google email entry screen
that stays open. Actual Google account sign-in was not performed by the agent.
The tested userdata also has the identical GmsCore installed as an updated system
app; the factory-only module layout has not been tested with fresh userdata.
Full camera, NFC, audio and other hardware acceptance remains outside this test.
These are validation results for the recorded profile, not a boot guarantee for
every SDK33 ROM. The build does not flash/reboot a device or clear account data.
