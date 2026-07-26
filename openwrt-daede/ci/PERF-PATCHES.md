# Performance fork maintenance — quic-go / outbound

This repo's "aggressive" build replaces dae's QUIC + outbound deps with
performance forks. Upstream (olicesx) has gone quiet, so this file is the
self-maintenance playbook: what the perf delta actually is, and how to refresh
it ourselves when needed.

Pins live in `ci/pins.env`; the build pulls them from the `kenzok8/*` mirrors
(synced from `olicesx/*` by `.github/workflows/auto-bump.yml`).

## Dependency lineage

```
official quic-go (v0.60.x, moves weekly)
        │  (dae uses a very old, heavily-patched base — APIs differ by ~38 minor versions)
        ▼
daeuniverse/quic-go  branch "sid"  (2025-02, the maintained dae base)
        │  olicesx cherry-picks newer upstream fixes on top
        ▼
olicesx/quic-go  branch "enhanced-with-fixes"  (base ~2026-02, module renamed github.com/olicesx/quic-go)
        │  + 3 perf commits
        ▼
olicesx/quic-go  branch "perf/node-pooling-v2"  (= our base 33005db + the 4 patches)
```

Key facts that shape any "update":

- `olicesx/quic-go` is **not** a GitHub fork of `quic-go/quic-go` (`fork:false`,
  `parent:null`, module renamed). Its history does **not** share commits with
  official quic-go, so you cannot `git rebase` onto an official tag — refreshing
  means **cherry-picking / backporting specific upstream fixes** onto
  `enhanced-with-fixes`, not moving the base.
- There is **no fresher ready-made base** in the dae→olicesx chain:
  `enhanced-with-fixes` (2026-02) is already ahead of `daeuniverse/quic-go@sid`
  (2025-02). To go fresher you must do the cherry-pick work yourself.
- dae-core (`kdae`) is tied to this old quic-go API. Bumping to official v0.60
  would mean porting dae-core, not just the dep — out of scope.

## The perf delta we must preserve

`olicesx/quic-go`  `main` → `perf/node-pooling-v2` = **4 commits, 5 files**:

| commit | date | what |
|--------|------|------|
| `bb65418` | 2026-02-26 | fix: improve UDP GSO handling (single-segment sends) — this is the `enhanced-with-fixes` HEAD |
| `254bec0d` | 2026-04-28 | perf: B-tree node pooling + frame sorter optimizations |
| `7d0a3176` | 2026-04-28 | fix: return stream frames to pool on cancellations |
| `e0d255ff` | 2026-04-28 | fix: only generate RTT sample for last ack-eliciting packet |

Files touched (the entire perf surface):

```
frame_sorter.go
internal/ackhandler/sent_packet_handler.go
internal/utils/tree/tree.go            (B-tree pool — the big one, +57/-25)
send_stream.go
sys_conn_oob.go                        (UDP GSO)
```

Because the surface is tiny and self-contained, reapplying these commits on top
of any refreshed base is a ~1h job.

**Archived, self-owned copy:** these 4 commits are stored as reproducible patch
files in `ci/patches/quic-go/` (verified: `git am` onto base `33005db` rebuilds
the exact shipped tree, Go 1.26 `go build` clean). So the perf delta survives
even if the olicesx repo disappears — apply them onto whatever base we maintain.
See `ci/patches/quic-go/README.md`.

### outbound

`olicesx/outbound` `main` (= `daeuniverse/outbound@main`, 2025-07) →
`perf/complete-optimizations` = **130 commits, 215 files** (sticky-ip, ss2022,
anytls, reality fixes, memory-safety, …). This is a large, living fork and
`daeuniverse/outbound` upstream is itself near-dormant (newest branch 2026-02),
so self-rebasing outbound is **high effort, low value** — ride olicesx, do not
maintain locally.

## How to refresh quic-go ourselves (when a real fix lands)

Trigger: a security/correctness fix in official quic-go that matters to us, or
auto-bump's staleness alert (see below) firing for a long time.

The build no longer fetches the olicesx perf branch — `assemble-{dae,daed}-src.yml`
fetch `QUICGO_BASE_COMMIT` from `kenzok8/quic-go` and `git am ci/patches/quic-go/*.patch`.
So a refresh means changing the base and/or the patch files, not chasing a branch.

1. Decide what to refresh:
   - **a new upstream fix** → backport it as a new patch file in `ci/patches/quic-go/`
     (take the official commit's diff, apply to the matching file by hand — no shared
     history, so not a clean cherry-pick), or
   - **a newer base** → bump `QUICGO_BASE_COMMIT` in `ci/pins.env`, then re-test that
     the 4 existing patches still apply (regenerate them if a hunk drifts).
2. Verify locally: `git clone kenzok8/quic-go`, checkout the base, `git am` the full
   `ci/patches/quic-go/*.patch` set, confirm it applies and `go build ./...` is clean.
3. Build-gate before merging (mandatory — never ship an unbuilt tree): on a staging
   branch run `assemble-daed-src.yml` + `assemble-dae-src.yml` then
   `test-daed-build.yml` — it must `go build` dae-core+wing clean.
4. Only after a green gate: merge to main.

## Staleness / CVE alert

`auto-bump.yml`'s `perf-staleness` job warns (and opens a tracking issue) when our
quic-go base ages past 60 days while official quic-go has newer releases — so we
notice instead of silently aging. Treat a fired alert as "check whether the new
upstream release carries a fix worth backporting per the steps above," not "must
act immediately."
