"""Fetch upstream GitHub releases and update apps.json for AltStore.
doc: https://faq.altstore.io/developers/make-a-source
repo: https://github.com/bggRGjQaUbCoE/PiliPlus
"""

import io
import json
import plistlib
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SOURCE_REPO = "bggRGjQaUbCoE/PiliPlus"
SOURCE_JSON = Path("apps.json")

REPO_NAME = "PiliPlus"
REPO_SUBTITLE = "Unofficial PiliPlus AltStore source"
REPO_DESCRIPTION = "Auto-updated AltStore source for PiliPlus"
REPO_ICON_URL = "https://github.com/bggRGjQaUbCoE/PiliPlus/raw/main/assets/images/logo/logo.png"
REPO_SCREENSHOT1 = "https://raw.githubusercontent.com/bggRGjQaUbCoE/PiliPlus/refs/heads/main/assets/screenshots/home.png"
REPO_SCREENSHOT2 = "https://raw.githubusercontent.com/bggRGjQaUbCoE/PiliPlus/refs/heads/main/assets/screenshots/bangumi.png"
REPO_SCREENSHOT3 = "https://raw.githubusercontent.com/bggRGjQaUbCoE/PiliPlus/refs/heads/main/assets/screenshots/dynamic.png"
REPO_WEBSITE = "https://github.com/bebound/PiliPlus-AltStore-Repo"
REPO_TINT_COLOR = "#00AEEF"

APP_NAME = "PiliPlus"
# Fallback bundle ID used only if reading from IPA fails entirely.
APP_BUNDLE_ID_FALLBACK = "com.example.piliplus"
APP_DEVELOPER = "bggRGjQaUbCoE"
APP_SUBTITLE = "Latest PiliPlus release"
APP_DESCRIPTION = "PiliPlus iOS app builds from GitHub releases"
APP_TINT_COLOR = "#00AEEF"
APP_MIN_OS = "14.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso_datetime(value: str) -> str:
    """Return an ISO-8601 datetime string; fall back to *now* if empty."""
    if not value:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value.replace(".000", "")


def short_date(value: str) -> str:
    """Return only the date portion (YYYY-MM-DD) of an ISO datetime."""
    return iso_datetime(value).split("T", 1)[0]


def extract_build_version(filename: str) -> str:
    """Extract build number from IPA filename like ``PiliPlus_ios_2.0.6+4915.ipa``."""
    if not filename:
        return ""
    match = re.search(r"\+(\d+)\.ipa$", filename, re.IGNORECASE)
    return match.group(1) if match else ""


def extract_version_from_filename(filename: str) -> str:
    """Extract version string from IPA filename like ``PiliPlus_ios_2.0.7+4946.ipa``.

    The IPA filename version matches ``CFBundleShortVersionString`` inside the
    IPA, which is what AltStore validates against. This is more reliable than
    the GitHub release tag (e.g. tag ``2.0.7.1`` may contain an IPA with
    internal version ``2.0.7``).
    """
    if not filename:
        return ""
    match = re.search(r"_ios_([\d.]+?)\+\d+\.ipa$", filename, re.IGNORECASE)
    return match.group(1) if match else ""


def get_bundle_id_from_ipa(url: str, timeout: int = 120) -> str:
    """Download an IPA and read ``CFBundleIdentifier`` from its ``Info.plist``.

    Raises if the IPA can't be downloaded or parsed. Caller is responsible
    for handling failures (e.g. falling back to a known value).
    """
    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "altstore-repo-updater"},
    )
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        # The Info.plist we want lives at Payload/<AppName>.app/Info.plist
        # (exactly two path separators). Nested frameworks have their own
        # Info.plist files which we must skip.
        plist_name = None
        for name in z.namelist():
            if (
                name.startswith("Payload/")
                and name.endswith(".app/Info.plist")
                and name.count("/") == 2
            ):
                plist_name = name
                break
        if not plist_name:
            raise RuntimeError(f"Info.plist not found inside IPA: {url}")

        with z.open(plist_name) as f:
            info = plistlib.load(f)

    bundle_id = info.get("CFBundleIdentifier")
    if not bundle_id:
        raise RuntimeError(f"CFBundleIdentifier missing in Info.plist: {url}")
    return bundle_id


def detect_bundle_id(releases: list[dict]) -> str:
    """Walk through releases (newest first) and read bundle ID from the first
    IPA we can successfully download. Returns the fallback if all attempts fail.
    """
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        assets = release.get("assets") or []
        for asset in assets:
            name = (asset.get("name") or "").lower()
            if not name.endswith(".ipa"):
                continue
            url = asset.get("browser_download_url", "")
            if not url:
                continue
            try:
                bid = get_bundle_id_from_ipa(url)
                print(f"detected bundle id from {asset.get('name')}: {bid}")
                return bid
            except Exception as exc:
                print(f"failed to read bundle id from {asset.get('name')}: {exc}")
                continue
    print(f"warning: falling back to hardcoded bundle id {APP_BUNDLE_ID_FALLBACK}")
    return APP_BUNDLE_ID_FALLBACK


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def init() -> dict:
    """Bootstrap a fresh source skeleton."""
    return {
        "name": REPO_NAME,
        "subtitle": REPO_SUBTITLE,
        "description": REPO_DESCRIPTION,
        "iconURL": REPO_ICON_URL,
        "website": REPO_WEBSITE,
        "tintColor": REPO_TINT_COLOR,
        "apps": [],
        "news": [],
    }


def new_app_entry(bundle_id: str) -> dict:
    """Return a fresh app dict with no versions."""
    return {
        "name": APP_NAME,
        "bundleIdentifier": bundle_id,
        "developerName": APP_DEVELOPER,
        "subtitle": APP_SUBTITLE,
        "localizedDescription": APP_DESCRIPTION,
        "iconURL": REPO_ICON_URL,
        "screenshots": [REPO_SCREENSHOT1, REPO_SCREENSHOT2, REPO_SCREENSHOT3],
        "tintColor": APP_TINT_COLOR,
        "versions": [],
        "news": [],
    }


# ---------------------------------------------------------------------------
# GitHub Releases API
# ---------------------------------------------------------------------------

def fetch_releases() -> list[dict]:
    """Return all releases from the upstream repo, paginating through all pages."""
    releases: list[dict] = []
    url: str | None = f"https://api.github.com/repos/{SOURCE_REPO}/releases?per_page=100"

    while url:
        resp = requests.get(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "altstore-repo-updater",
            },
            timeout=30,
        )
        resp.raise_for_status()
        releases.extend(resp.json())

        # Follow the "next" link if present.
        url = resp.links.get("next", {}).get("url")

    return releases


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    releases = fetch_releases()

    # Detect the real bundle ID by reading the latest available IPA.
    bundle_id = detect_bundle_id(releases)

    data = init()
    data["apps"].append(new_app_entry(bundle_id))
    app = data["apps"][0]

    added = 0
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue

        tag = release.get("tag_name") or ""
        # The tag is used for the news entry URL and identifier.
        # The "version" field on each version_item must match the IPA's
        # internal CFBundleShortVersionString, which is encoded in the
        # filename (e.g. 2.0.7 inside PiliPlus_ios_2.0.7+4946.ipa).
        tag_version = tag[1:] if tag.startswith("v") else tag
        if not tag_version:
            continue

        assets = release.get("assets") or []
        ipa_assets = [
            a for a in assets if (a.get("name") or "").lower().endswith(".ipa")
        ]
        if not ipa_assets:
            continue

        for asset in ipa_assets:
            asset_name = asset.get("name") or ""
            download_url = asset.get("browser_download_url", "")

            release_body = (
                (release.get("body") or "").strip() or APP_DESCRIPTION
            )
            build_version = extract_build_version(asset_name)
            # Prefer the version encoded in the IPA filename. Fall back to
            # the GitHub tag if the filename doesn't match the expected pattern.
            ipa_version = extract_version_from_filename(asset_name) or tag_version

            version_item: dict = {
                "version": ipa_version,
                "date": short_date(release.get("published_at", "")),
                "localizedDescription": release_body,
                "downloadURL": download_url,
                "size": asset.get("size", 0),
                "minOSVersion": APP_MIN_OS,
            }
            if build_version:
                # Insert buildVersion right after version.
                version_item = {
                    "version": ipa_version,
                    "buildVersion": build_version,
                    "date": version_item["date"],
                    "localizedDescription": release_body,
                    "downloadURL": download_url,
                    "size": version_item["size"],
                    "minOSVersion": APP_MIN_OS,
                }

            app.setdefault("versions", []).append(version_item)
            added += 1

        # Add a news entry for this release. News uses the GitHub tag name
        # so the entry lines up with the release page on GitHub.
        data.setdefault("news", []).append({
            "title": f"{APP_NAME} {tag_version}",
            "identifier": f"release-{tag_version}",
            "caption": release.get("name") or f"Version {tag_version} released",
            "date": short_date(release.get("published_at", "")),
            "tintColor": APP_TINT_COLOR,
            "notify": True,
            "appID": bundle_id,
            "url": f"https://github.com/{SOURCE_REPO}/releases/tag/{tag}",
        })

    # Sort versions by date (desc), then version string (desc).
    app["versions"] = sorted(
        app.get("versions", []),
        key=lambda v: (v.get("date", ""), v.get("version", "")),
        reverse=True,
    )

    # Sort news by date (desc).
    data["news"] = sorted(
        data.get("news", []),
        key=lambda n: n.get("date", ""),
        reverse=True,
    )

    SOURCE_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f'latest version is {app["versions"][0]["version"]}')


if __name__ == "__main__":
    main()
