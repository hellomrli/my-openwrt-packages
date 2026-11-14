# adguardhome-dual

This package provides two AdGuardHome instances for an x86_64 OpenWrt router using separate direct and proxy DNS backends.

[Repository documentation](../docs/index.md)

## Choose the deployment model

Current my-ImmortalWrt master uses the official `adguardhome` package plus an overlay for the two instances. It does not install this package. Avoid mixing both models, which define the same services and paths.

This package's templates expose Web UI on `192.168.50.1` without a committed login hash. Configure authentication and access before enabling it on a shared network. The current firmware overlay starts unauthenticated Web UI on loopback instead.

## Inspect the services

| Service | DNS | Web UI |
|---|---|---|
| `adh-direct` | `127.0.0.1:50530` | `192.168.50.1:50080` |
| `adh-proxy` | `127.0.0.1:50531` | `192.168.50.1:50081` |

The package avoids `luci-app-adguardhome`, whose single-instance management does not match this layout. It installs its own runtime and service wrappers; inspect the [Makefile](Makefile) and `files/` before choosing it. From the repository root:

```bash
sed -n '1,120p' adguardhome-dual/Makefile
```

## Preserve configuration

The firmware's upgrade list must include both `/etc/AdGuardHome-direct.yaml` and `/etc/AdGuardHome-proxy.yaml`, plus any independently maintained runtime and service files. The former deployment also preserved `/usr/bin/AdGuardHome` and both `/etc/init.d/adh-*` scripts.

A package README cannot add those paths to an already running firmware's backup. Generate and verify an external backup before changing deployment models.
