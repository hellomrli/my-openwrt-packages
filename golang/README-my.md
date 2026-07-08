# Golang for OpenWrt builds

This package tree is based on the official OpenWrt `packages/lang/golang` packaging,
but pins the Go toolchain to the current official Go stable release used by this
firmware build.

- Go version: 1.26.5
- Source: `go1.26.5.src.tar.gz` from `https://go.dev/dl/` / `https://dl.google.com/go/`
- Source SHA256: `495be4bc87176ac567392e5b4116abd98466d33d7b49d41e764ccc6976b2dc42`

Do not replace this with third-party `packages_lang_golang` unless explicitly
needed; the intended source is the official Go release channel.
