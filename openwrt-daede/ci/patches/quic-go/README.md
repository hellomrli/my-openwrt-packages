# quic-go perf patches (self-owned)

Authoritative, reproducible copy of the olicesx quic-go performance delta so the
build no longer *depends* on the olicesx repo staying alive. See
`../../PERF-PATCHES.md` for the full lineage and refresh procedure.

## What these are

The 4 commits that turn the `enhanced-with-fixes` base into the shipped
`perf/node-pooling-v2` tree. Total surface: 5 files.

| order | patch | effect |
|-------|-------|--------|
| 0001 | UDP GSO handling | fix single-segment send issues |
| 0002 | B-tree node pooling + frame sorter | the main perf win |
| 0003 | return stream frames to pool on cancellation | alloc reduction |
| 0004 | RTT sample only for last ack-eliciting packet | correctness/perf |

## Base they apply onto

Generated against `kenzok8/quic-go` (fork of `olicesx/quic-go`):

- base branch `main` @ `33005db9cba0598e664c74026a440ffbf1bf0108`
- applying 0001..0004 reproduces tree `e60bd44f6d544db01700fa5979468fbb99c7f23a`,
  i.e. byte-for-byte the `perf/node-pooling-v2` tip `e0d255ff807c...`
  (verified with `git am` + tree-hash compare, Go 1.26 `go build ./...` clean).

## Apply

```sh
git checkout -B perf <base>          # <base> = the quic-go base you maintain
git am ci/patches/quic-go/0001-*.patch ci/patches/quic-go/0002-*.patch \
       ci/patches/quic-go/0003-*.patch ci/patches/quic-go/0004-*.patch
```

If a future base has drifted and a hunk fails, fall back to `git apply --3way`
or port the change by hand — the surface is tiny (frame_sorter.go,
internal/ackhandler/sent_packet_handler.go, internal/utils/tree/tree.go,
send_stream.go, sys_conn_oob.go).
