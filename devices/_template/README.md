# Adding a device profile

Copy this folder to `devices/<profile-name>` and replace every placeholder in
`device.conf`. Add the target's verified `displayconfig/display_id_<panel-id>.xml`
and `device_features.xml` before using it from the bot. The porter automatically reads `ro.product.*.model` and
`ro.product.*.device` from the stock vendor/odm images, then matches
`match_models`, `match_devices`, or `model`. A profile is intentionally not
selected until those values are supplied; unmatched devices use the porter's
in-memory automatic profile instead.
