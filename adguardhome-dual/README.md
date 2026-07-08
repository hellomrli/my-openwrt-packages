# adguardhome-dual

Dual AdGuardHome runtime package for the `dnsmasq + daed + dual ADH` router layout.

Installed services:

- `adh-direct`: DNS `127.0.0.1:50530`, Web `192.168.50.1:50080`
- `adh-proxy`: DNS `127.0.0.1:50531`, Web `192.168.50.1:50081`

The package intentionally does **not** depend on `luci-app-adguardhome`, because a single LuCI app cannot manage this two-instance layout cleanly.

Runtime files preserved by the firmware sysupgrade list:

- `/etc/AdGuardHome-direct.yaml`
- `/etc/AdGuardHome-proxy.yaml`
- `/usr/bin/AdGuardHome`
- `/etc/init.d/adh-direct`
- `/etc/init.d/adh-proxy`

The default YAML templates keep ports and DNS routing behavior from the current router, but do not commit the router's existing web login password hash.
