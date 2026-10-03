# Android 15 role compatibility overlay

This immutable, resource-only overlay targets the known Google PermissionController
module recorded in `profile.json`. The original signed APEX and APK stay intact.

`roles.xml` preserves the donor role definitions and guards
`android.permission.REPOSITION_SELF_WINDOWS` with `minSdkVersion="37"`.
All 64 role string references resolve to resources inside this overlay. External
references to the target APK previously produced an unresolved runtime package ID
and crashed `RequestRoleFragment` with `Resources$NotFoundException` when an app
requested the default browser role.

`roles_overlays.xml` maps only `xml/roles`; the localized strings are supporting
overlay resources and do not replace the target's strings individually.
`localized_role_strings.json` contains the donor translations by resource
configuration, including the default configuration under the empty key. The
compiled APK preserves 5,312 values across 174 configurations. Artificial
en-XA/ar-XB grammatical-gender variants are omitted; normal translations are
preserved.

To rebuild, emit one `res/values[-configuration]/role_strings.xml` per JSON entry,
copy `roles.xml` and `roles_overlays.xml` into `res/xml`, compile/link with AAPT2
and the donor framework-res APK, then sign the resource-only APK and update the
SHA256 in `profile.json`. Do not link role string references against the target
APK. Verify every compiled role string reference resolves inside the overlay and
round-trip the localized strings before updating the profile.

The corrected APK has passed compilation, translation round-trip, signature,
native idmap creation and ROM metadata checks. Opening the actual role dialog
after installing the corrected ROM remains to be verified.
