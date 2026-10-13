#!/usr/bin/env bash

set -euo pipefail

fail() {
    printf 'promote-candidate: %s\n' "$*" >&2
    exit 1
}

for name in BASE_SHA CANDIDATE_SHA BUILD_RUN_ID STAGING R2_ACCESS_KEY_ID \
    R2_SECRET_ACCESS_KEY R2_ENDPOINT R2_BUCKET AGGREGATE_FEED_TOKEN GH_TOKEN; do
    [[ -n "${!name:-}" ]] || fail "missing $name"
done
[[ "$BASE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "invalid base SHA"
[[ "$CANDIDATE_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "invalid candidate SHA"
[[ "$BUILD_RUN_ID" =~ ^[0-9]+$ ]] || fail "invalid build run ID"
[[ "$STAGING" =~ ^auto-bump-staging-[0-9]+-[0-9]+$ ]] || fail "invalid staging branch"
[[ -f publish/manifest.json ]] || fail "missing verified manifest"
jq -e --arg sha "$CANDIDATE_SHA" --argjson run "$BUILD_RUN_ID" \
    '.candidate_sha == $sha and .build_run_id == $run and (.files | length > 0)' \
    publish/manifest.json >/dev/null || fail "manifest does not identify this candidate and build"
cp publish/manifest.json publish/feed/release-manifest.json

run_state=$(gh run view "$BUILD_RUN_ID" --json status,conclusion,headSha \
    --jq '.status + " " + (.conclusion // "") + " " + .headSha')
[[ "$run_state" = "completed success $CANDIDATE_SHA" ]] || fail "build run is not successful for the candidate: $run_state"

git fetch -q origin refs/heads/main
remote_main=$(git ls-remote origin refs/heads/main | cut -f1)
remote_staging=$(git ls-remote origin "refs/heads/$STAGING" | cut -f1)
[[ "$remote_main" = "$BASE_SHA" || "$remote_main" = "$CANDIDATE_SHA" ]] || fail "main moved; candidate must be rebuilt"
if [[ "$remote_main" = "$BASE_SHA" ]]; then
    [[ "$remote_staging" = "$CANDIDATE_SHA" ]] || fail "candidate branch moved or vanished"
    git fetch -q origin "refs/heads/$STAGING"
fi
git merge-base --is-ancestor "$BASE_SHA" "$CANDIDATE_SHA" || fail "candidate does not descend from its base"

aggregate_workflow=$(GH_TOKEN="$AGGREGATE_FEED_TOKEN" gh api \
    'repos/kenzok8/openwrt-clashoo/contents/.github/workflows/aggregate-feed.yml?ref=main' \
    -H 'Accept: application/vnd.github.raw+json') || fail "cannot read aggregate workflow"
grep -Fq 'candidate_sha:' <<< "$aggregate_workflow" || fail "aggregate workflow lacks candidate identity gate"
grep -Fq 'build_run_id:' <<< "$aggregate_workflow" || fail "aggregate workflow lacks build identity gate"

version=$(git show "$CANDIDATE_SHA:ci/pins.env" | sed -n 's/^DAE_VERSION=//p')
[[ "$version" =~ ^[0-9]{4}\.[0-9]{2}\.[0-9]{2}$ ]] || fail "invalid candidate version"
tag="v${version}-${CANDIDATE_SHA:0:7}"
remote_tag=$(git ls-remote --tags origin "refs/tags/$tag^{}" | cut -f1)
if [[ -z "$remote_tag" ]]; then
    remote_tag=$(git ls-remote --tags origin "refs/tags/$tag" | cut -f1)
fi
[[ -z "$remote_tag" || "$remote_tag" = "$CANDIDATE_SHA" ]] || fail "tag $tag points to another commit"
if [[ "$remote_main" = "$CANDIDATE_SHA" && -z "$remote_tag" ]]; then
    fail "main already advanced but tag is absent; requires manual inspection"
fi

# Stage and verify the exact candidate bytes before changing main or a release.
rclone_dir=$(mktemp -d)
trap 'rm -rf "$rclone_dir"' EXIT
chmod 700 "$rclone_dir"
cat > "$rclone_dir/rclone.conf" <<EOF
[r2]
type = s3
provider = Cloudflare
access_key_id = ${R2_ACCESS_KEY_ID}
secret_access_key = ${R2_SECRET_ACCESS_KEY}
endpoint = ${R2_ENDPOINT}
acl = private
no_check_bucket = true
EOF
chmod 600 "$rclone_dir/rclone.conf"
export RCLONE_CONFIG="$rclone_dir/rclone.conf"
stage="r2:${R2_BUCKET}/openwrt-feed/daed-candidates/${CANDIDATE_SHA}"
probe="r2:${R2_BUCKET}/openwrt-feed/daed-candidates/.write-probe-${GITHUB_RUN_ID:-$BUILD_RUN_ID}"
rclone copyto publish/manifest.json "$probe"
rclone deletefile "$probe"
rclone copy publish/ "$stage/" --immutable --fast-list
rclone check publish/ "$stage/" --download --fast-list

# The unified feed also carries luci-app-daede from jell. Its version must not
# supersede this candidate after main and the tag have moved.
for candidate in publish/feed/*/*/luci-app-daede*.apk publish/feed/*/*/luci-app-daede*.ipk; do
    [[ -f "$candidate" ]] || continue
    relative=${candidate#publish/feed/}
    directory=${relative%/*}
    candidate_name=${candidate##*/}
    rclone lsf "r2:${R2_BUCKET}/openwrt-feed/jell/${directory}/" \
        --files-only --include 'luci-app-daede*' > "$rclone_dir/jell-package-list" || \
        fail "cannot inspect jell packages in $directory"
    while IFS= read -r existing_name; do
        [[ -n "$existing_name" ]] || continue
        newest=$(printf '%s\n%s\n' "$candidate_name" "$existing_name" | sort -V | tail -1)
        [[ "$newest" = "$candidate_name" ]] || \
            fail "jell has a newer luci-app-daede in $directory: $existing_name"
    done < "$rclone_dir/jell-package-list"
done

state="r2:${R2_BUCKET}/openwrt-feed/daed-release-state"
pending="$state/pending.json"
manifest_digest=$(sha256sum publish/manifest.json | cut -d' ' -f1)
jq -n --arg sha "$CANDIDATE_SHA" --arg base "$BASE_SHA" \
    --arg staging "$STAGING" --arg tag "$tag" \
    --arg digest "$manifest_digest" --argjson run "$BUILD_RUN_ID" \
    '{candidate_sha:$sha,base_sha:$base,staging:$staging,tag:$tag,build_run_id:$run,manifest_sha256:$digest}' \
    > "$rclone_dir/pending.json"
if rclone lsf "$state/" --files-only | grep -Fxq pending.json; then
    rclone cat "$pending" > "$rclone_dir/existing-pending.json"
    cmp -s "$rclone_dir/pending.json" "$rclone_dir/existing-pending.json" || \
        fail "another release is pending"
else
    rclone copyto "$rclone_dir/pending.json" "$pending" --immutable
    rclone cat "$pending" > "$rclone_dir/existing-pending.json"
    cmp -s "$rclone_dir/pending.json" "$rclone_dir/existing-pending.json" || \
        fail "pending release state differs after upload"
fi

if [[ "$remote_main" = "$BASE_SHA" ]]; then
    git config user.name "github-actions[bot]"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git tag -a "$tag" "$CANDIDATE_SHA" -m "Release $tag"
    git -c credential.helper='!gh auth git-credential' push --atomic \
        --force-with-lease="refs/heads/main:$BASE_SHA" origin \
        "$CANDIDATE_SHA:refs/heads/main" "refs/tags/$tag:refs/tags/$tag"
    [[ "$(git ls-remote origin refs/heads/main | cut -f1)" = "$CANDIDATE_SHA" ]] || fail "main did not advance to candidate"
fi

notes="$rclone_dir/release-notes.md"
{
    printf '# openwrt-daede %s\n\n' "$tag"
    printf 'Pinned upstream dae/daed release from candidate `%s`.\n\n' "$CANDIDATE_SHA"
    printf 'All 16 SDK targets passed the patch audit and package gate. Build run: %s.\n' "$BUILD_RUN_ID"
} > "$notes"
if ! gh release view "$tag" >/dev/null 2>&1; then
    gh release create "$tag" --draft --verify-tag --target "$CANDIDATE_SHA" \
        --title "Release $tag" --notes-file "$notes"
fi
release_target=$(gh release view "$tag" --json targetCommitish --jq '.targetCommitish')
[[ "$release_target" = "$CANDIDATE_SHA" || "$release_target" = "$tag" ]] || fail "Release target differs from candidate"
for asset in publish/release/* publish/manifest.json; do
    [[ -f "$asset" ]] || fail "missing release asset"
    name=${asset##*/}
    if gh release view "$tag" --json assets --jq '.assets[].name' | grep -Fxq "$name"; then
        existing="$rclone_dir/$name"
        gh release download "$tag" --pattern "$name" --output "$existing"
        cmp -s "$asset" "$existing" || fail "Release asset differs: $name"
    else
        gh release upload "$tag" "$asset"
    fi
done
{
    find publish/release -maxdepth 1 -type f -printf '%f\n'
    printf 'manifest.json\n'
} | sort > "$rclone_dir/expected-assets"
gh release view "$tag" --json assets --jq '.assets[].name' | sort > "$rclone_dir/actual-assets"
diff -u "$rclone_dir/expected-assets" "$rclone_dir/actual-assets" || fail "Release has unexpected or missing assets"

feed="r2:${R2_BUCKET}/openwrt-feed/daed"
rclone sync publish/feed/ "$feed/" --checksum --fast-list --transfers 8 --checkers 16
rclone check publish/feed/ "$feed/" --download --fast-list

aggregate_url=$(GH_TOKEN="$AGGREGATE_FEED_TOKEN" gh workflow run aggregate-feed.yml \
    -R kenzok8/openwrt-clashoo --ref main \
    -f candidate_sha="$CANDIDATE_SHA" -f build_run_id="$BUILD_RUN_ID")
aggregate_run=${aggregate_url##*/}
[[ "$aggregate_run" =~ ^[0-9]+$ ]] || fail "cannot identify aggregate feed run: $aggregate_url"
GH_TOKEN="$AGGREGATE_FEED_TOKEN" gh run watch "$aggregate_run" \
    -R kenzok8/openwrt-clashoo --exit-status
gh release edit "$tag" --draft=false --prerelease=false
rclone copyto "$rclone_dir/pending.json" "$state/completed/${CANDIDATE_SHA}.json" --immutable
rclone cat "$state/completed/${CANDIDATE_SHA}.json" > "$rclone_dir/completed.json"
cmp -s "$rclone_dir/pending.json" "$rclone_dir/completed.json" || fail "completion state differs"
rclone deletefile "$pending"
printf 'promoted %s from build %s\n' "$tag" "$BUILD_RUN_ID"
