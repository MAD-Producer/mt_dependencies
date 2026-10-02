#!/usr/bin/env python3
"""Resolve upstream releases, package changed tools and assemble the latest manifest."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "MAD-Toolbox-Dependencies sync (GitHub Actions)"
API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
PLATFORMS = ("windows-x64", "macos-arm64")
TOOLS = ("deno", "mediainfo", "ffmpeg", "bbdown", "yt-dlp")
PACKAGING_REVISION = 1


def get(url: str, *, github: bool = False) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if github:
        headers.update(API_HEADERS)
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"download failed: {url}: {exc}") from exc
    if not data:
        raise RuntimeError(f"empty response: {url}")
    return data


def get_text(url: str) -> str:
    return get(url).decode("utf-8", errors="replace")


def release(repo: str) -> dict:
    value = json.loads(
        get(f"https://api.github.com/repos/{repo}/releases/latest", github=True)
    )
    if value.get("draft") or value.get("prerelease"):
        raise RuntimeError(f"latest release for {repo} is not a stable release")
    return value


def asset(info: dict, name: str, *, suffix: bool = False) -> dict:
    matches = [
        item
        for item in info.get("assets", [])
        if (item["name"].endswith(name) if suffix else item["name"] == name)
        and item.get("browser_download_url")
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one asset {name!r} in {info.get('html_url', 'release')}"
        )
    return matches[0]


def checksum(text: str, filename: str | None = None) -> str:
    for line in text.splitlines():
        match = re.match(r"\s*([0-9a-fA-F]{64})(?:\s+[* ]?(.+))?\s*$", line)
        if match and (
            filename is None
            or match.group(2) is None
            or Path(match.group(2).strip()).name == filename
        ):
            return match.group(1).lower()
    raise RuntimeError(f"no SHA-256 checksum found for {filename or 'archive'}")


def download(url: str, destination: Path, expected: str | None = None) -> None:
    destination.write_bytes(get(url))
    if expected and sha256(destination) != expected.lower():
        raise RuntimeError(f"upstream SHA-256 mismatch: {url}")


def extract_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            path = Path(entry.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or ":" in entry.filename:
                raise RuntimeError(f"unsafe archive path: {entry.filename}")
        archive.extractall(destination)


def find_executable(directory: Path, name: str) -> Path:
    matches = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name.casefold() == name.casefold()
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} executable in the package")
    return matches[0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def zip_dir(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                permissions = 0o755 if path.stat().st_mode & 0o111 else 0o644
                info.external_attr = (0o100000 | permissions) << 16
                archive.writestr(
                    info,
                    path.read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                )
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"corrupt output ZIP member: {bad}")


def mediainfo_url(platform: str) -> tuple[str, str]:
    if platform == "windows-x64":
        page_url = "https://mediaarea.net/en/MediaInfo/Download/Windows"
        page = get_text(page_url)
        # The official CLI row names the x64 ZIP explicitly. Do not fall back to GUI/DLL links.
        match = re.search(
            r'href=["\']([^"\']*MediaInfo_CLI_[^"\']*_Windows_x64\.zip)["\']',
            page,
            re.I,
        )
        if not match:
            raise RuntimeError(
                "official Windows page no longer exposes the x64 CLI ZIP"
            )
        url = urllib.parse.urljoin(page_url, html.unescape(match.group(1)))
        version_match = re.search(r"MediaInfo_CLI_([^_]+)_Windows_x64\.zip", url, re.I)
    else:
        page_url = "https://mediaarea.net/en/MediaInfo/Download/Mac_OS"
        page = get_text(page_url)
        # The current official macOS CLI download is a DMG (not the GUI app or dylib).
        match = re.search(
            r'href=["\']([^"\']*MediaInfo_CLI_[^"\']*_Mac\.dmg)["\']', page, re.I
        )
        if not match:
            raise RuntimeError("official macOS page no longer exposes the CLI DMG")
        url = urllib.parse.urljoin(page_url, html.unescape(match.group(1)))
        version_match = re.search(r"MediaInfo_CLI_([^_]+)_Mac\.dmg", url, re.I)
    if not version_match:
        raise RuntimeError(
            "could not read MediaInfo version from official CLI filename"
        )
    return url, version_match.group(1)


def build_mediainfo(platform: str, work: Path, out: Path, upstream: dict) -> dict:
    url, version = upstream["url"], upstream["version"]
    downloaded = work / (
        "mediainfo-source.zip" if platform == "windows-x64" else "mediainfo-source.dmg"
    )
    download(url, downloaded)
    package = work / "mediainfo"
    package.mkdir()
    if platform == "windows-x64":
        extract_zip(downloaded, package)
        executable = find_executable(package, "MediaInfo.exe")
        relative = executable.relative_to(package).as_posix()
    else:
        mount = work / "mediainfo-mount"
        mount.mkdir()
        subprocess.run(
            [
                "hdiutil",
                "attach",
                "-nobrowse",
                "-readonly",
                "-mountpoint",
                str(mount),
                str(downloaded),
            ],
            check=True,
        )
        try:
            pkgs = list(mount.rglob("*.pkg"))
            if not pkgs:
                raise RuntimeError("MediaInfo CLI DMG contains no PKG payload")
            expanded = work / "expanded-pkg"
            subprocess.run(
                ["pkgutil", "--expand-full", str(pkgs[0]), str(expanded)], check=True
            )
            matches = [p for p in expanded.rglob("mediainfo") if p.is_file()]
            if not matches:
                raise RuntimeError(
                    "expanded MediaInfo package contains no executable CLI"
                )
            executable = matches[0]
            root = next((p for p in executable.parents if p.name == "Payload"), None)
            if root is None:
                usr = next((p for p in executable.parents if p.name == "usr"), None)
                root = usr.parent if usr else None
            if root is None:
                raise RuntimeError("cannot determine MediaInfo package payload root")
            shutil.copytree(root, package, dirs_exist_ok=True)
            relative = executable.relative_to(root).as_posix()
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=False)
    target = out / package_filename("mediainfo", platform, upstream)
    zip_dir(package, target)
    return {
        "tool": "mediainfo",
        "platform": platform,
        "upstream_version": version,
        "source": url,
        "file": target.name,
        "size": target.stat().st_size,
        "sha256": sha256(target),
        "executables": {"mediainfo": relative},
    }


def github_zip_tool(
    tool: str, platform: str, work: Path, out: Path, upstream: dict
) -> dict:
    url, version = upstream["url"], upstream["version"]
    downloaded = work / f"{tool}-source.zip"
    download(url, downloaded, upstream.get("sha256"))
    package = work / tool
    package.mkdir()
    extract_zip(downloaded, package)
    expected = {
        "deno": "deno.exe" if platform == "windows-x64" else "deno",
        "bbdown": "BBDown.exe" if platform == "windows-x64" else "BBDown",
    }[tool]
    executable = find_executable(package, expected)
    if platform == "macos-arm64":
        executable.chmod(executable.stat().st_mode | 0o111)
    target = out / package_filename(tool, platform, upstream)
    zip_dir(package, target)
    return {
        "tool": tool,
        "platform": platform,
        "upstream_version": version,
        "source": url,
        "file": target.name,
        "size": target.stat().st_size,
        "sha256": sha256(target),
        "executables": {tool: executable.relative_to(package).as_posix()},
    }


def build_ytdlp(platform: str, work: Path, out: Path, upstream: dict) -> dict:
    filename = "yt-dlp.exe" if platform == "windows-x64" else "yt-dlp_macos"
    url, version = upstream["url"], upstream["version"]
    package = work / "yt-dlp"
    package.mkdir()
    binary = package / filename
    download(url, binary, upstream.get("sha256"))
    if platform != "windows-x64":
        binary.chmod(binary.stat().st_mode | 0o111)
    try:
        (package / "LICENSE").write_bytes(get(upstream["licenseUrl"]))
    except RuntimeError as error:
        print(f"::warning::{error}")
    target = out / package_filename("yt-dlp", platform, upstream)
    zip_dir(package, target)
    return {
        "tool": "yt-dlp",
        "platform": platform,
        "upstream_version": version,
        "source": url,
        "file": target.name,
        "size": target.stat().st_size,
        "sha256": sha256(target),
        "executables": {"yt-dlp": filename},
    }


def build_ffmpeg_windows(work: Path, out: Path, upstream: dict) -> dict:
    version, archive_url = upstream["version"], upstream["url"]
    archive = work / "ffmpeg-source.7z"
    download(archive_url, archive, upstream["sha256"])
    extractor = shutil.which("7z") or shutil.which("7zz")
    if not extractor:
        raise RuntimeError("7-Zip CLI is required on the Windows runner")
    extracted = work / "ffmpeg-extracted"
    extracted.mkdir()
    subprocess.run(
        [extractor, "x", "-y", f"-o{extracted}", str(archive)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    binaries = list(extracted.rglob("ffmpeg.exe"))
    if not binaries:
        raise RuntimeError("Gyan archive contains no ffmpeg.exe")
    root = binaries[0].parent
    if not (root / "ffprobe.exe").is_file():
        raise RuntimeError("Gyan archive is missing ffprobe.exe beside ffmpeg.exe")
    package = work / "ffmpeg-windows"
    shutil.copytree(root, package)
    for doc in list(root.parent.glob("LICENSE*")) + list(root.parent.glob("README*")):
        if doc.is_file():
            shutil.copy2(doc, package / doc.name)
    (package / "UPSTREAM.txt").write_text(
        f"Source: https://www.gyan.dev/ffmpeg/builds/\nRelease: {version}\nBuild: release full static\n",
        encoding="utf-8",
    )
    target = out / package_filename("ffmpeg", "windows-x64", upstream)
    zip_dir(package, target)
    return {
        "tool": "ffmpeg",
        "platform": "windows-x64",
        "upstream_version": version,
        "source": archive_url,
        "build_variant": "full",
        "file": target.name,
        "size": target.stat().st_size,
        "sha256": sha256(target),
        "executables": {"ffmpeg": "ffmpeg.exe", "ffprobe": "ffprobe.exe"},
    }


def build_ffmpeg_macos(work: Path, out: Path, upstream: dict) -> dict:
    page_url = "https://ffmpeg.martin-riedl.de/"
    version = upstream["version"]
    package = work / "ffmpeg-macos"
    package.mkdir()
    records = []
    for binary in ("ffmpeg", "ffprobe"):
        source = upstream["archives"][binary]
        archive_url = source["url"]
        archive_path = work / f"{binary}.upstream.zip"
        download(archive_url, archive_path, source["sha256"])
        with zipfile.ZipFile(archive_path) as archive:
            matches = [
                entry
                for entry in archive.infolist()
                if Path(entry.filename).name == binary
            ]
            if not matches:
                raise RuntimeError(
                    f"Martin Riedl {binary} ZIP has no {binary} executable"
                )
            entry = matches[0]
            destination = package / binary
            destination.write_bytes(archive.read(entry))
            destination.chmod(0o755)
        records.append({"file": archive_url, "sha256": source["sha256"]})
    (package / "UPSTREAM.txt").write_text(
        f"Source: {page_url}\nRelease: {version}\nBuild: Release Build, macOS Apple Silicon/arm64\n",
        encoding="utf-8",
    )
    target = out / package_filename("ffmpeg", "macos-arm64", upstream)
    zip_dir(package, target)
    return {
        "tool": "ffmpeg",
        "platform": "macos-arm64",
        "upstream_version": version,
        "source": page_url,
        "build_variant": "Martin Riedl Release, Apple Silicon/arm64",
        "upstream_archives": records,
        "file": target.name,
        "size": target.stat().st_size,
        "sha256": sha256(target),
        "executables": {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"},
    }


def resolve_upstream(tool: str, platform: str, releases: dict) -> dict:
    if tool in {"deno", "bbdown", "yt-dlp"}:
        repo = {
            "deno": "denoland/deno",
            "bbdown": "nilaoda/BBDown",
            "yt-dlp": "yt-dlp/yt-dlp",
        }[tool]
        if repo not in releases:
            releases[repo] = release(repo)
        info = releases[repo]
        names = {
            "deno": "deno-x86_64-pc-windows-msvc.zip"
            if platform == "windows-x64"
            else "deno-aarch64-apple-darwin.zip",
            "bbdown": "_win-x64.zip" if platform == "windows-x64" else "_osx-arm64.zip",
            "yt-dlp": "yt-dlp.exe" if platform == "windows-x64" else "yt-dlp_macos",
        }
        selected = asset(info, names[tool], suffix=tool == "bbdown")
        source = {
            "version": info["tag_name"],
            "url": selected["browser_download_url"],
            "assetId": selected["id"],
            "updatedAt": selected.get("updated_at"),
        }
        digest = selected.get("digest") or ""
        if digest.startswith("sha256:"):
            source["sha256"] = digest.removeprefix("sha256:")
        else:
            hash_names = [selected["name"] + ".sha256sum", selected["name"] + ".sha256"]
            if tool == "yt-dlp":
                hash_names.append("SHA2-256SUMS")
            for candidate in info.get("assets", []):
                if candidate["name"] in hash_names:
                    source["sha256"] = checksum(
                        get_text(candidate["browser_download_url"]), selected["name"]
                    )
                    break
        if tool == "yt-dlp":
            source["licenseUrl"] = (
                f"https://raw.githubusercontent.com/{repo}/{info['tag_name']}/LICENSE"
            )
        return source
    if tool == "mediainfo":
        url, version = mediainfo_url(platform)
        return {"version": version, "url": url}
    if platform == "windows-x64":
        base = "https://www.gyan.dev/ffmpeg/builds/"
        version = get_text(base + "ffmpeg-release-full.7z.ver").strip()
        archive_name = f"ffmpeg-{urllib.parse.quote(version, safe='')}-full_build.7z"
        url = urllib.parse.urljoin(base, f"packages/{archive_name}")
        return {
            "version": version,
            "url": url,
            "variant": "release-full-static",
            "sha256": checksum(get_text(url + ".sha256")),
        }
    base = "https://ffmpeg.martin-riedl.de/"
    page = get_text(base)
    release_section = re.search(
        r"<h2[^>]*>\s*Download Release Build\s*</h2>(.*?)(?=<h2|\Z)", page, re.I | re.S
    )
    if not release_section:
        raise RuntimeError("Martin Riedl page has no Release Build section")
    arm_section = re.search(
        r"<h3[^>]*>\s*macOS \(Apple Silicon/arm64\)\s*</h3>(.*?)(?=<h3|\Z)",
        release_section.group(1),
        re.I | re.S,
    )
    if not arm_section:
        raise RuntimeError("Martin Riedl Release section has no Apple Silicon builds")
    links = [
        urllib.parse.urljoin(base, html.unescape(link))
        for link in re.findall(r'href=["\']([^"\']+)["\']', arm_section.group(1))
    ]
    archives = {}
    for binary in ("ffmpeg", "ffprobe"):
        url = next(
            (
                link
                for link in links
                if urllib.parse.urlparse(link).path.endswith(f"/{binary}.zip")
            ),
            None,
        )
        if url is None:
            raise RuntimeError(f"Martin Riedl arm64 Release has no {binary} ZIP")
        archives[binary] = {"url": url, "sha256": checksum(get_text(url + ".sha256"))}
    build = urllib.parse.urlparse(archives["ffmpeg"]["url"]).path.split("/")[-2]
    return {
        "version": build.split("_", 1)[-1],
        "url": base,
        "variant": "release-arm64",
        "archives": archives,
    }


def fingerprint(upstream: dict) -> str:
    content = json.dumps(
        {"packagingRevision": PACKAGING_REVISION, "upstream": upstream},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode()).hexdigest()


def package_filename(
    tool: str, platform: str, upstream: dict, content_hash: str | None = None
) -> str:
    version = re.sub(r"[^A-Za-z0-9._+-]", "-", upstream["version"])
    identity = content_hash or fingerprint(upstream)
    return f"{tool}-{version}-r{PACKAGING_REVISION}-{identity[:16]}-{platform}.zip"


def write_json(path: Path, content: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def create_plan(repository: str, output: Path, force: bool) -> dict:
    try:
        previous = release(repository)
    except RuntimeError as error:
        if (
            not isinstance(error.__cause__, urllib.error.HTTPError)
            or error.__cause__.code != 404
        ):
            raise
        previous = None
    assets = (
        {item["name"]: item for item in previous.get("assets", [])} if previous else {}
    )
    old_manifest = (
        json.loads(get(assets["version.json"]["browser_download_url"]))
        if "version.json" in assets
        else {}
    )
    releases = {}
    platforms = {}
    changed = False
    for platform in PLATFORMS:
        packages = {}
        for tool in TOOLS:
            old = old_manifest.get("platforms", {}).get(platform, {}).get(tool)
            old_asset = assets.get(old.get("fileName")) if old else None
            try:
                upstream = resolve_upstream(tool, platform, releases)
                needs_build = (
                    force
                    or not old_asset
                    or old.get("upstreamFingerprint") != fingerprint(upstream)
                )
            except RuntimeError as error:
                if not old_asset:
                    raise
                print(
                    f"::warning::{platform}/{tool}: {error}; preserving the previous valid package"
                )
                upstream = old.get("upstream", {"version": old["version"]})
                needs_build = False
            packages[tool] = {
                "upstream": upstream,
                "build": needs_build,
                "previous": old,
                "previousUrl": old_asset["browser_download_url"] if old_asset else None,
            }
            changed = changed or needs_build
            print(
                f"{platform}/{tool}: {'build' if needs_build else 'reuse'} {upstream['version']}"
            )
        platforms[platform] = packages
    plan = {"changed": changed, "repository": repository, "platforms": platforms}
    write_json(output, plan)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"changed={str(changed).lower()}\n")
    return plan


def build_platform(platform: str, plan: dict, out: Path) -> None:
    actual = (
        "windows-x64"
        if sys.platform == "win32"
        else "macos-arm64"
        if sys.platform == "darwin"
        else ""
    )
    if actual != platform:
        raise RuntimeError(f"requested {platform}, runner is {actual or sys.platform}")
    out.mkdir(parents=True, exist_ok=True)
    packages = {}
    with tempfile.TemporaryDirectory(prefix="dependency-sync-") as temporary:
        work = Path(temporary)
        for tool in TOOLS:
            item = plan["platforms"][platform][tool]
            upstream = item["upstream"]
            if not item["build"]:
                entry = item["previous"]
                name = entry["fileName"]
                if Path(name).name != name or not name.endswith(".zip"):
                    raise RuntimeError(f"invalid previous ZIP filename: {name}")
                download(item["previousUrl"], out / name, entry["sha256"])
            else:
                if tool in {"deno", "bbdown"}:
                    record = github_zip_tool(tool, platform, work, out, upstream)
                elif tool == "mediainfo":
                    record = build_mediainfo(platform, work, out, upstream)
                elif tool == "yt-dlp":
                    record = build_ytdlp(platform, work, out, upstream)
                elif platform == "windows-x64":
                    record = build_ffmpeg_windows(work, out, upstream)
                else:
                    record = build_ffmpeg_macos(work, out, upstream)
                filename = package_filename(tool, platform, upstream, record["sha256"])
                if filename != record["file"]:
                    (out / record["file"]).rename(out / filename)
                record["file"] = filename
                entry = {
                    "version": upstream["version"],
                    "fileName": record["file"],
                    "sha256": record["sha256"],
                    "size": record["size"],
                    "executables": record["executables"],
                    "upstreamFingerprint": fingerprint(upstream),
                    "upstream": upstream,
                }
            packages[tool] = entry
            print(
                f"{tool}: {entry['fileName']} {entry['size']} bytes sha256:{entry['sha256']}"
            )
    write_json(
        out / f"build-info-{platform}.json",
        {"platform": platform, "packages": packages},
    )


def assemble_manifest(directory: Path) -> dict:
    platforms = {}
    expected_files = set()
    for platform in PLATFORMS:
        record = json.loads(
            (directory / f"build-info-{platform}.json").read_text(encoding="utf-8")
        )
        packages = record["packages"]
        if set(packages) != set(TOOLS):
            raise RuntimeError(f"incomplete package set for {platform}")
        for tool, entry in packages.items():
            path = directory / entry["fileName"]
            if (
                path.parent != directory
                or path.stat().st_size != entry["size"]
                or sha256(path) != entry["sha256"]
            ):
                raise RuntimeError(f"invalid final ZIP: {entry['fileName']}")
            required = {"ffmpeg", "ffprobe"} if tool == "ffmpeg" else {tool}
            if set(entry["executables"]) != required:
                raise RuntimeError(f"missing executable metadata for {tool}")
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if any(name not in names for name in entry["executables"].values()):
                    raise RuntimeError(
                        f"missing declared executable in {entry['fileName']}"
                    )
                if "installation.json" in names:
                    raise RuntimeError("installation.json is reserved for Toolbox")
            expected_files.add(path.name)
        platforms[platform] = packages
    if {path.name for path in directory.glob("*.zip")} != expected_files:
        raise RuntimeError(
            "release directory does not contain exactly the current ten ZIPs"
        )
    manifest = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platforms": platforms,
    }
    write_json(directory / "version.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    plan_command = commands.add_parser("plan")
    plan_command.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", "MAD-Producer/mt_dependencies"),
    )
    plan_command.add_argument("--out", default="plan.json")
    plan_command.add_argument("--force", action="store_true")
    build_command = commands.add_parser("build")
    build_command.add_argument("--platform", required=True, choices=PLATFORMS)
    build_command.add_argument("--plan", default="plan.json")
    build_command.add_argument("--out", default="dist")
    manifest_command = commands.add_parser("manifest")
    manifest_command.add_argument("--directory", default="dist")
    args = parser.parse_args()
    if args.command == "plan":
        create_plan(args.repository, Path(args.out), args.force)
    elif args.command == "build":
        build_platform(
            args.platform,
            json.loads(Path(args.plan).read_text(encoding="utf-8")),
            Path(args.out).resolve(),
        )
    else:
        assemble_manifest(Path(args.directory).resolve())


if __name__ == "__main__":
    main()
