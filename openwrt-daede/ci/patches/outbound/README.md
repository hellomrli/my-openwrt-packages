# outbound patches (self-owned)

Fixes we carry on top of `OUTBOUND_COMMIT` because upstream has not merged them.
The assemble workflows apply every `NNNN-*.patch` here with `git apply` right
after fetching outbound; an empty directory is skipped.

## Current state: one patch

| patch | what | why it is here |
|-------|------|----------------|
| 0002 | REALITY client sends version bytes `[26,7,28]` instead of `[1,8,10]` | Xray-core 26.7.11+ defaults the server's `minClientVer` to `26.3.27`, so our version bytes are rejected; see below |

### 0001: absorbed upstream (2026.10.18 bump, `OUTBOUND_COMMIT=1cf3439`)

The SSR obfs fix from 0001 is now in the perf line itself, so the patch was
deleted. Upstream implements it as `netproxy.UnwrapIntrinsicConn`
(`netproxy/unwrap.go`, peels the full wrapper chain up to depth 8) used by
`TcpConn.Write`, plus the regression test
`TestTcpConnFirstWriteReachesSSRObfsCipher` in `ssr_obfs_test.go` (real SSR
transport stack, covers the buffered-wrapper case). The old patch's local
one-layer `unwrapConn` helper and its `ssr_cipher_test.go` are superseded.

History: the fix first existed upstream as a side commit (`d5d9708`, later
rebased to `5797872`) that was never merged into the perf branch we track,
which is why it was carried as a patch (and why it regressed on 2026-08-13
when a bump moved the pin to a perf-branch commit without it). The r11
optimization line folded it into `perf/complete-optimizations`, so the pin now
carries it. If a future pin ever drops it, this note and the upstream test are
the trail — re-add the patch.

### 0002 background

The REALITY client writes its own three version bytes into the encrypted
handshake. Xray-core 26.7.11+ (commit `af7eb68`) defaults the server's
`realitySettings.minClientVer` to `26.3.27` when unset, so 26.7.x servers using
that default reject dae/daed REALITY connections into the fallback decoy
(`REALITY: processed invalid connection`). Sending `[26,7,28]` passes any
current default gate. This is a temporary compatibility measure, not a protocol
requirement:

- Xray-core main has already reverted the default gate in commit `47cfe999`,
  but no release carries that change yet, and 26.7.x servers already deployed
  keep it.
- Re-evaluate (and delete) this patch only when outbound itself adopts an
  appropriate version-byte strategy, or when the project's compatibility scope
  explicitly drops 26.7.x servers — not merely because a newer Xray release
  appears. A server operator can still reject us by explicitly configuring a
  lower `maxClientVer`; that is their choice and nothing on our side fixes it.

The REALITY protocol library is identical between Xray-core 26.3.27 and
26.7.28, so patched clients remain fully compatible with older servers.

## Apply by hand

```sh
git checkout -B carry <OUTBOUND_COMMIT>
git apply ci/patches/outbound/*.patch
go test ./protocol/shadowsocks_stream/ ./transport/tls/
```

## When upstream absorbs a patch

`git apply` fails loudly and the assemble build stops. Inspect the failing patch
before deleting it: for 0001, confirm the wrapper-peeling fix is in the new
base (as `netproxy.UnwrapIntrinsicConn`, not the old local `unwrapConn`
helper); for 0002, follow the re-evaluation conditions above.
