# MAD Toolbox Dependencies

Independent binary dependency releases for MAD Toolbox. Repository: `MAD-Producer/mt_dependencies`.
The workflow downloads upstream binaries; it does not compile Toolbox or install tools into a runner's system directories.

## Package set and sources

Each published Release contains **twelve ZIPs, `version.json`, and one merged tracker list** (`trackers-merged.txt`): six packages for Windows x64 and six for macOS arm64, plus the public BT tracker lists consumed by the Toolbox Aria2 page.

| Package          | Windows x64                                                         | macOS arm64                                                                                                         |
| ---------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Deno             | `denoland/deno` stable Release                                      | `denoland/deno` stable Release                                                                                      |
| MediaInfo CLI    | Official MediaArea CLI ZIP                                          | Official MediaArea CLI DMG/PKG payload                                                                              |
| FFmpeg + ffprobe | Gyan **Release Full static**, not git master/shared/Essentials      | Martin Riedl **Release / Apple Silicon**, not Snapshot                                                              |
| BBDown           | `nilaoda/BBDown` stable Release, `_win-x64.zip` asset               | `_osx-arm64.zip` asset                                                                                              |
| yt-dlp           | Official `yt-dlp.exe`                                               | Official `yt-dlp_macos`                                                                                             |
| Aria2            | `aria2/aria2` stable Release, official `win-64bit-build1.zip` asset | Compiled on the runner from the official GPG-signed source tarball (AppleTLS), never a snapshot or community repack |

Python and musicdl remain system-installed and are not included. BBDown's archived upstream may legitimately have no newer Release.
Windows and macOS versions of the same tool may differ. FFprobe is part of the FFmpeg package, not a sixth package.
Aria2 publishes no official macOS binary, so the macOS package is the documented exception: it is compiled on the runner from the official `release-<version>` source tarball.

## Workflow

**Sync dependency releases** runs on source/workflow pushes to `main`, manual dispatch, and daily at **02:17 UTC / 10:17 Asia/Shanghai**.
Manual `force` rebuilds available packages; normally unchanged ZIPs are reused byte-for-byte from the previous Release.

1. `resolve` selects concrete upstream files and checksums, compares upstream fingerprints and packaging revision against the previous manifest, and fetches the two public tracker lists. A tracker-list digest change alone counts as a change and triggers a Release.
2. If unchanged, stop without publishing. If an upstream lookup temporarily fails, retain its previous valid package and log a warning; the first publication cannot omit a package. The same rule applies to tracker lists.
3. `package` runs on Windows and macOS, builds changed packages, downloads unchanged ZIPs, and produces platform artifacts.
4. `publish` merges both artifacts, checks declared files, sizes and SHA-256, and generates the complete manifest (tracker lists are written from the plan, or re-downloaded from the previous Release when upstream was unreachable).
5. Create a **new draft**, upload all fifteen assets, confirm uploaded names/sizes, then publish and explicitly mark it latest.

Drafts are queried and published by their Release ID; querying by tag before publication may return 404 because the tag is not created yet.
Failures do not replace the current public Release. Failed drafts may remain for diagnosis; historical public Releases are not automatically deleted.
The concurrency group does not cancel a running publication. Only the publish job receives `contents: write`; other jobs are read-only.
Actions use the built-in `GITHUB_TOKEN`; do not add a PAT or OpenList administrator credentials.

Required checks are limited to successful downloads, published upstream hashes when available, archive structure and required files.
No video downloading, transcoding, strict `--version` parsing, TUF, rollback UI or compatibility matrix is run.
Missing upstream checksums are not a universal rejection condition. SHA-256 is integrity checking, not publisher authentication.

## `version.json` contract — schema 1

The publisher generates this file; do not upload a hand-edited template. This abbreviated example shows field names, not real hashes:

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-10-03T00:00:00Z",
  "trackers": {
    "merged": {
      "version": "2026-11-15",
      "fileName": "trackers-merged.txt",
      "sha256": "FULL_64_CHARACTER_SHA256",
      "size": 12345
    }
  },
  "platforms": {
    "windows-x64": {
      "aria2": {
        "version": "UPSTREAM_VERSION",
        "fileName": "aria2-UPSTREAM_VERSION-r1-HASH_PREFIX-windows-x64.zip",
        "sha256": "FULL_64_CHARACTER_SHA256",
        "size": 12345,
        "executables": { "aria2": "aria2c.exe" },
        "upstreamFingerprint": "PUBLISHER_SOURCE_FINGERPRINT",
        "upstream": { "url": "OFFICIAL_SOURCE_URL" }
      },
      "ffmpeg": {
        "version": "UPSTREAM_VERSION",
        "fileName": "ffmpeg-UPSTREAM_VERSION-r1-HASH_PREFIX-windows-x64.zip",
        "sha256": "FULL_64_CHARACTER_SHA256",
        "size": 12345,
        "executables": {
          "ffmpeg": "ffmpeg.exe",
          "ffprobe": "ffprobe.exe"
        },
        "upstreamFingerprint": "PUBLISHER_SOURCE_FINGERPRINT",
        "upstream": {
          "version": "UPSTREAM_VERSION",
          "url": "OFFICIAL_SOURCE_URL"
        }
      }
    }
  }
}
```

- `platforms` (both `windows-x64` and `macos-arm64`) contains all six tools: `deno`, `mediainfo`, `ffmpeg`, `bbdown`, `yt-dlp`, `aria2`. There is no separate top-level `aria2` section.
- Clients select supported package keys before deserializing package contents. Unknown tools and unused platforms do not prevent supported tools from loading. The five core tools remain required; Aria2 is optional in the reader.
- Within `schemaVersion: 1`, add new tools under `platforms`, optional fields, or new top-level sections. Do not rename/remove existing fields or make optional fields required. Breaking changes require a new `schemaVersion`; clients reject unsupported versions explicitly.
- This layout targets the updated client; users must upgrade. The publisher can still reuse ZIPs from previous releases that stored Aria2 at the top level, but always writes the unified layout.
- `version` is the upstream version string, not a global batch version or guaranteed SemVer.
- `fileName` is a basename of an uploaded ZIP. It includes the packaging revision and ZIP content hash prefix, so changed bytes receive a different CDN filename.
- `sha256` is the full lowercase hexadecimal SHA-256 of the final ZIP; `size` is its byte count.
- `executables` maps tool identifiers to actual relative paths inside that ZIP, using `/` separators. Do not assume every binary is at the ZIP root.
- `upstreamFingerprint` and `upstream` are publisher bookkeeping/provenance; Toolbox can ignore them.
- `trackers` maps the merged list identifier (`merged`) to a plain-text asset next to the manifest. `version` is the UTC fetch date, not an upstream release; `sha256`/`size` cover the exact asset bytes, and clients must verify them. The lists refresh with the daily sync; Toolbox fetches these lists only through the self-hosted CDN and falls back to its local cache if the CDN is unavailable.
- `generatedAt` is informational UTC time, not an update/expiry gate. Clients compare their installed ZIP identity/hash to the selected latest package, including packaging-only fixes.

ZIP timestamps are fixed to the ZIP format's 1980 epoch to avoid timestamp-only content changes. Mac executable permissions are retained/restored.
For a packaging change, increment `PACKAGING_REVISION` in `scripts/sync.py` and push. `installation.json` is reserved for Toolbox's local installation record and must not be included in a ZIP.

## OpenList

Mount this public repository using **GitHub Releases** at `/mt_dependencies`, with `repo_structure: MAD-Producer/mt_dependencies` and `show_all_version: false`.
Assets are flat in the mount root. Do not alter the existing `/mt` application updater mount.

If the administrator assigns the directory share ID `mt_dependencies`, the intended share download paths are:

```text
https://openlist.frameneo.com/sd/mt_dependencies/version.json
https://openlist.frameneo.com/sd/mt_dependencies/<fileName>
```

`<fileName>` covers the ZIPs and the `trackers-*.txt` lists alike; Toolbox resolves tracker asset names through the manifest's `trackers` section before downloading.

OpenList sharing, proxy/CDN routing and cache refresh are administrator tasks, not performed by this workflow.
Use short caching for the manifest and longer caching for content-identified ZIPs. Verify the actual share route after the first public Release; a GitHub publication alone does not prove CDN delivery.

## Local commands and validation

```sh
python scripts/sync.py plan --out plan.json
python scripts/sync.py build --platform windows-x64 --plan plan.json --out dist
python scripts/sync.py build --platform macos-arm64 --plan plan.json --out dist
python scripts/sync.py manifest --directory dist
```

Run each build on its matching operating system; macOS DMG extraction uses `hdiutil`/`pkgutil`, Windows Full extraction uses 7-Zip, and the macOS Aria2 package compiles the official source tarball (Xcode toolchain, no extra dependencies).
The manifest command expects both platform outputs in one directory. Local syntax/fixture checks do not prove the live Actions run or CDN route.
Keep source/license materials that can be obtained from upstream; this repository does not claim redistribution licensing has been reviewed.

The merged list combines XIU2 and ngosang, deduplicating complete announce URLs. If one source fails, the other is used; if both fail, the previous merged asset is retained.
