# gxmobile-scan / luci-app-gxmobile

Guangxi Mobile IPTV scanner for the x86_64 OpenWrt/ImmortalWrt IPTV profile.

The original Go source was recovered from `/home/lain/codex/gxmobile-scan`.
Its `releases/prebuilt/gxmobile-scan-linux` binary matches the router's
`gxmobile-scan` 1.1.0-r2 byte-for-byte. The installed package declares MIT.
The original source and router files are archived separately; this directory
contains the maintained source, not a binary blob or device credentials.

The 1.2 series adds a LuCI/rpcd interface, unified scheduling, bounded probes,
validated requests, race-free snapshots, kernel locking, checked atomic file
writes, safe failure handling and bitrate measurements using HLS durations.
The embedded baseline preserves the existing 172 channel names. Full details,
limitations and upgrade instructions are in [docs/iptv.md](https://github.com/hellomrli/my-ImmortalWrt/blob/703242e55e1af88cee5baf81a3b8f5beb84dc015/docs/iptv.md).

The backend is Linux-only and currently packaged for x86_64. CID values exceed
signed 32-bit integers. Standalone Windows builds from the original utility are
outside the scope of this OpenWrt package.

```sh
cd src
go test -race ./...
go vet ./...
CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' .
```

Configuration: `/etc/config/gxmobile`. Persistent data: `/etc/gxmobile`.
Public LAN playlist copy: `/www/gxmobile`. Fixed API: `127.0.0.1:8081`.
The scheduler uses the standard BusyBox `flock` applet (or util-linux flock).
