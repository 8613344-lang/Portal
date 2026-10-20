#!/usr/bin/env python3
"""Validate and package the explicitly allowed public RuStore release files.

Run with Python 3.10+ and Pillow. No Android SDK, network or signing secret is
needed here: the release task must already have exported its verified APK and
certificate report. This script never signs an APK or publishes an application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zipfile

try:
    from PIL import Image
except ImportError:
    Image = None


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PACKAGE = "ru.portal.books"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
SOURCE_SUFFIXES = {
    ".java", ".kt", ".xml", ".png", ".jpg", ".jpeg", ".webp", ".svg",
    ".json", ".html", ".css", ".js", ".txt", ".ttf", ".otf",
}
FORBIDDEN_PARTS = {
    ".git", ".gradle", ".avd", ".private", ".env", "build", "media",
    "node_modules", "__pycache__",
}
FORBIDDEN_SUFFIXES = {".jks", ".keystore", ".p12", ".pfx", ".clixml", ".db", ".sqlite3"}
FORBIDDEN_NAMES = {
    "local.properties", "keystore.properties", "key.properties", "password.txt",
    "passwords.txt", "credentials.json", "credentials.xml", "secrets.json", "secrets.xml",
}
DEBUG_CERTIFICATE_SHA256 = "999da6ac5b22539634b5ca1539330fdd8d431e2b4bd04b71ebe78cd91633b4a5"


class PackageError(Exception):
    """An input is missing, unsafe or inconsistent."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise PackageError(message)


def safe_file(relative: str | Path, *, required: bool = True) -> Path | None:
    """Accept regular non-symlink inputs inside the repository only."""
    relative = Path(relative)
    check(not relative.is_absolute() and ".." not in relative.parts,
          f"Unsafe input path: {relative}")
    lowered_parts = {part.lower() for part in relative.parts}
    check(not lowered_parts.intersection(FORBIDDEN_PARTS)
          and relative.suffix.lower() not in FORBIDDEN_SUFFIXES
          and relative.name.lower() not in FORBIDDEN_NAMES
          and not relative.name.lower().startswith(".env"),
          f"Private/build files cannot be packaged: {relative}")
    current = ROOT
    for part in relative.parts:
        current = current / part
        check(not current.is_symlink(), f"Symlink inputs cannot be packaged: {relative}")
    path = ROOT / relative
    if not path.exists() and not required:
        return None
    check(path.is_file(), f"Required file is missing: {relative.as_posix()}")
    check(path.resolve().is_relative_to(ROOT), f"Input escapes the repository: {relative}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (UnicodeError, OSError) as error:
        raise PackageError(f"Cannot read UTF-8 file: {path.relative_to(ROOT)}") from error


def load_listing() -> tuple[dict, list[str]]:
    path = safe_file("rustore/listing.json")
    try:
        listing = json.loads(read_text(path))
    except (ValueError, TypeError) as error:
        raise PackageError("rustore/listing.json is not valid JSON") from error
    check(isinstance(listing, dict), "listing.json must contain an object")
    for field, limit in (("name", 30), ("short_description", 80), ("description", 4000)):
        value = listing.get(field)
        check(isinstance(value, str) and bool(value.strip()) and len(value) <= limit,
              f"listing.{field} must contain 1–{limit} characters")
    check(listing.get("package") == EXPECTED_PACKAGE,
          f"listing.package must be {EXPECTED_PACKAGE}")
    check(isinstance(listing.get("version_name"), str)
          and re.fullmatch(r"[0-9A-Za-z.+-]+", listing["version_name"]) is not None,
          "listing.version_name is missing or unsafe")
    check(type(listing.get("version_code")) is int and listing["version_code"] > 0,
          "listing.version_code must be a positive integer")
    pending = []
    placeholder = re.compile(r"заполнить|\btodo\b|\btbd\b|your[_ -]|example\.com", re.I)
    for field, value in sorted(listing.items()):
        if isinstance(value, str) and placeholder.search(value):
            pending.append(field)
    support = listing.get("support_email", "")
    if not isinstance(support, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", support):
        pending.append("support_email")
    if not isinstance(listing.get("developer"), str) or not listing["developer"].strip():
        pending.append("developer")
    if listing.get("category_suggestion") and not listing.get("category"):
        pending.append("category (select an actual category in RuStore Console)")
    return listing, sorted(set(pending))


def validate_build_metadata(listing: dict) -> None:
    source = read_text(safe_file("android/app/build.gradle"))
    patterns = {
        "package": r"\bapplicationId\s+['\"]([^'\"]+)['\"]",
        "version_name": r"\bversionName\s+['\"]([^'\"]+)['\"]",
        "version_code": r"\bversionCode\s+(\d+)",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, source)
        check(match is not None and match.group(1) == str(listing[field]),
              f"android/app/build.gradle and listing.json disagree on {field}")


def validate_apk(listing: dict) -> tuple[list[Path], dict]:
    stem = f"PortalBooks-{listing['version_name']}-rustore"
    apk = safe_file(f"android/releases/{stem}.apk")
    checksum = safe_file(f"android/releases/{stem}.apk.sha256")
    certificate = safe_file(f"android/releases/{stem}-certificate.txt")
    check(0 < apk.stat().st_size <= 5 * 1024**3, "APK size must be 1 byte–5 GiB")
    check(zipfile.is_zipfile(apk), "Release APK is not a ZIP/APK container")
    try:
        with zipfile.ZipFile(apk) as archive:
            names = set(archive.namelist())
            check("AndroidManifest.xml" in names and "classes.dex" in names,
                  "APK must contain AndroidManifest.xml and classes.dex")
            check(archive.testzip() is None, "APK ZIP integrity check failed")
    except (zipfile.BadZipFile, RuntimeError, OSError) as error:
        raise PackageError("Cannot verify the APK ZIP container") from error
    digest = sha256(apk)
    checksum_match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\r\n]+)\s*", read_text(checksum).strip())
    check(checksum_match is not None and checksum_match.group(1).lower() == digest
          and checksum_match.group(2) == apk.name,
          "APK .sha256 file does not match the release APK")
    report = read_text(certificate)
    report_fields = {}
    for line in report.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            check(key.strip().lower() not in report_fields,
                  f"Duplicate certificate-report field: {key.strip()}")
            report_fields[key.strip().lower()] = value.strip()
    expected = {
        "package": listing["package"],
        "version": listing["version_name"],
        "version code": str(listing["version_code"]),
        "apk sha-256": digest,
        "build type": "release",
        "debuggable": "false",
        "test only": "false",
        "debug certificate": "false",
        "launcher": f"{listing['package']}.MainActivity",
    }
    for field, value in expected.items():
        check(report_fields.get(field, "").lower() == value.lower(),
              f"Certificate report must confirm '{field}: {value}'; rebuild with assembleRuStore")
    fingerprints = re.findall(r"(?mi)^Signer #\d+ SHA-256:\s*([0-9a-f]{64})\s*$", report)
    check(bool(fingerprints), "Certificate report has no signing-certificate SHA-256")
    check(not re.search(r"Android\s+Debug", report, re.I)
          and all(item.lower() != DEBUG_CERTIFICATE_SHA256 for item in fingerprints),
          "An Android Debug certificate cannot be packaged for RuStore")
    return [apk, checksum, certificate], {
        "apk_sha256": digest,
        "certificate_sha256": [item.upper() for item in fingerprints],
        "verification": "APK container and exported Gradle verification report checked; run assembleRuStore to verify cryptographic signatures with Android SDK",
    }


def validate_image(path: Path, *, kind: str) -> dict:
    check(Image is not None, "Pillow is required: python -m pip install Pillow")
    size_limit = (5 if kind == "tablet" else 3) * 1024**2
    check(path.stat().st_size <= size_limit, f"Image exceeds {size_limit // 1024**2} MiB: {path.name}")
    try:
        with Image.open(path) as image:
            check(image.format in {"PNG", "JPEG"}, f"Only PNG/JPEG images are supported: {path.name}")
            image.load()
            width, height = image.size
            if kind == "icon":
                check(image.size == (512, 512), "Store icon must be exactly 512×512 px")
                check(image.convert("RGBA").getchannel("A").getextrema() == (255, 255),
                      "Store icon must be opaque across its entire area")
            else:
                check(min(width, height) <= 2160 and max(width, height) <= 3840,
                      f"Screenshot exceeds 2160×3840 px: {path.name}")
                check((width < height) if kind == "phone" else (width > height),
                      f"Expected portrait phone / landscape tablet screenshot: {path.name}")
                check(min(width, height) >= 320, f"Screenshot is too small: {path.name}")
            return {"path": path.relative_to(ROOT).as_posix(), "width": width, "height": height}
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise PackageError(f"Cannot decode image: {path.relative_to(ROOT)}") from error


def select_files(listing: dict) -> tuple[list[Path], dict]:
    files, report = validate_apk(listing)
    mandatory = [
        "rustore/listing.json", "rustore/icon-512.png",
        "docs/RUSTORE.md", "docs/ANDROID.md", "docs/ACCOUNTS.md",
        "docs/DEPLOYMENT.md", "docs/TESTING.md", "rustore/privacy-draft.md",
        "docs/VIDEO.md",
        "android/releases/README.md",
        "android/build.gradle", "android/settings.gradle", "android/gradle.properties",
        "android/app/build.gradle", "android/gradlew", "android/gradlew.bat",
        "android/gradle/wrapper/gradle-wrapper.jar",
        "android/gradle/wrapper/gradle-wrapper.properties", "tools/package_rustore.py",
    ]
    files.extend(safe_file(name) for name in mandatory)
    for name in (
        "docs/SIGNING.md", "rustore/MODERATION.md", "rustore/README.md",
        "android/create-release-key.ps1", "android/build-rustore.ps1",
    ):
        path = safe_file(name, required=False)
        if path is not None:
            files.append(path)
    images = [validate_image(safe_file("rustore/icon-512.png"), kind="icon")]
    for device in ("phone", "tablet"):
        directory = ROOT / "rustore" / device
        check(directory.is_dir() and not directory.is_symlink(), f"Missing screenshots: rustore/{device}")
        screenshots = sorted(path for path in directory.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
        check(3 <= len(screenshots) <= 10, f"Provide 3–10 screenshots in rustore/{device}")
        for screenshot in screenshots:
            screenshot = safe_file(screenshot.relative_to(ROOT))
            images.append(validate_image(screenshot, kind=device))
            files.append(screenshot)
    # Only this explicitly allowed Android source subtree is walked, never the repository.
    source_root = ROOT / "android" / "app" / "src"
    check(source_root.is_dir() and not source_root.is_symlink(), "Android app source is missing")
    for directory, children, names in os.walk(source_root, followlinks=False):
        for child in children:
            check(not (Path(directory) / child).is_symlink(), "Android source contains a symlink")
        children[:] = sorted(child for child in children if child.lower() not in FORBIDDEN_PARTS)
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix.lower() in SOURCE_SUFFIXES:
                files.append(safe_file(path.relative_to(ROOT)))
    check(any(path.name == "MainActivity.java" for path in files), "MainActivity.java was not selected")
    report["images"] = images
    return sorted(set(files), key=lambda path: path.relative_to(ROOT).as_posix()), report


def make_archive(output: Path, files: list[Path], listing: dict, pending: list[str], report: dict) -> str:
    check(output.suffix.lower() == ".zip", "--output must name a .zip file")
    output.parent.mkdir(parents=True, exist_ok=True)
    relative_files = {path.relative_to(ROOT).as_posix(): path for path in files}
    report.update({
        "package": listing["package"], "version_name": listing["version_name"],
        "version_code": listing["version_code"],
        "status": "manual_fields_pending" if pending else "prepared_for_owner_review",
        "pending_manual_fields": pending,
        "publication": "Not submitted; owner must review store requirements and moderation access",
        "files_sha256": {name: sha256(path) for name, path in relative_files.items()},
    })
    instructions = (
        f"Qicko / RuStore kit {listing['version_name']} ({listing['version_code']})\n\n"
        "Upload only android/releases/PortalBooks-" + listing["version_name"] + "-rustore.apk.\n"
        "Read docs/RUSTORE.md for compilation and publication steps.\n"
        "No private signing key or password is included. Keep the owner key for future updates.\n"
        "Android SDK verifies the signed artifact during assembleRuStore; this packaging step checks the exported report and hashes.\n"
        "This archive has not been submitted to RuStore and does not guarantee moderation approval.\n\n"
        "Pending manual fields: " + (", ".join(pending) if pending else "none detected; review the complete listing") + "\n"
    ).encode("utf-8")
    generated = {"README.txt": instructions, "package-report.json": (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".rustore-kit-", suffix=".zip", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(set(relative_files) | set(generated)):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (0o100755 if name == "android/gradlew" else 0o100644) << 16
                data = relative_files[name].read_bytes() if name in relative_files else generated[name]
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        digest = sha256(temporary)
        temporary.replace(output)
        temporary = None
        output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
        return digest
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Output ZIP path (default: rustore/qicko-rustore-kit-<version>.zip)")
    args = parser.parse_args(argv)
    try:
        check(Image is not None, "Pillow is required: python -m pip install Pillow")
        listing, pending = load_listing()
        validate_build_metadata(listing)
        files, report = select_files(listing)
        output = args.output or ROOT / "rustore" / f"qicko-rustore-kit-{listing['version_name']}.zip"
        output = output.absolute()
        digest = make_archive(output, files, listing, pending, report)
        print(f"Prepared: {output}")
        print(f"SHA-256: {digest}")
        print(f"Included {len(files)} allowlisted public files; no signing secrets.")
        if pending:
            print("Pending manual fields (not ready to submit unchanged): " + ", ".join(pending))
        else:
            print("Review docs/RUSTORE.md and moderation access before submitting.")
        return 0
    except (PackageError, OSError, zipfile.BadZipFile) as error:
        print(f"RuStore package not created: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
