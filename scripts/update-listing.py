#!/usr/bin/env python3
"""Update Google Play Store listing from Android string resources."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from xml.dom.minidom import parseString


SCOPE = "https://www.googleapis.com/auth/androidpublisher"

APPS = [
    {"key": "pro", "name": "Pro", "package": "de.szalkowski.activitylauncher.pro", "prefix": "description"},
    {"key": "free", "name": "Free/OSS", "package": "de.szalkowski.activitylauncher", "prefix": "description-free"},
]

TOKEN_DIR = Path.home() / ".config" / "activitylauncher"
OAUTH_TOKEN_PATH = TOKEN_DIR / "oauth-token.json"


def _google_imports():
    """Lazy imports for google API client libraries."""
    global build, HttpError
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError


def _get_oauth_credentials(client_secrets: str | None):
    if client_secrets:
        secrets_path = Path(client_secrets)
    else:
        env_path = os.getenv("GOOGLE_CLIENT_SECRETS")
        if env_path:
            secrets_path = Path(env_path)
        else:
            raise SystemExit(
                "No service account credentials found and no OAuth client "
                "secret provided.\n"
                "Either:\n"
                "  1. Use --keyfile with a service account JSON key\n"
                "  2. Set GOOGLE_APPLICATION_CREDENTIALS or SERVICE_ACCOUNT\n"
                "  3. Use --client-secrets with an OAuth 2.0 Client ID JSON\n"
                "     (get one from "
                "https://console.cloud.google.com/apis/credentials)"
            )

    if not secrets_path.exists():
        raise SystemExit(f"Client secrets file not found: {secrets_path}")

    from google.oauth2.credentials import Credentials as OAuthCreds
    from google_auth_oauthlib.flow import InstalledAppFlow

    if OAUTH_TOKEN_PATH.exists():
        try:
            cached = json.loads(OAUTH_TOKEN_PATH.read_text())
            creds = OAuthCreds.from_authorized_user_info(
                cached, scopes=[SCOPE]
            )
            if creds.valid:
                print(f"  Using cached OAuth token ({OAUTH_TOKEN_PATH})")
                return creds
        except Exception:
            OAUTH_TOKEN_PATH.unlink(missing_ok=True)

    print("  Starting OAuth browser flow...")
    flow = InstalledAppFlow.from_client_secrets_file(
        str(secrets_path), scopes=[SCOPE]
    )
    creds = flow.run_local_server(port=0, open_browser=True)

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    OAUTH_TOKEN_PATH.write_text(creds.to_json())
    print(f"  OAuth token saved to {OAUTH_TOKEN_PATH}")

    return creds


def get_credentials(keyfile: str | None = None, client_secrets: str | None = None):
    _google_imports()

    if keyfile:
        from google.oauth2.service_account import Credentials as SACreds
        return SACreds.from_service_account_file(keyfile, scopes=[SCOPE])

    env_keyfile = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if env_keyfile:
        from google.oauth2.service_account import Credentials as SACreds
        return SACreds.from_service_account_file(env_keyfile, scopes=[SCOPE])

    service_account_json = os.getenv("SERVICE_ACCOUNT")
    if service_account_json:
        from google.oauth2.service_account import Credentials as SACreds
        return SACreds.from_service_account_info(
            json.loads(service_account_json), scopes=[SCOPE]
        )

    import google.auth

    try:
        creds, project = google.auth.default(scopes=[SCOPE])
        if creds.valid:
            return creds
    except Exception:
        pass

    return _get_oauth_credentials(client_secrets)


def parse_strings_xml(path: Path) -> dict[str, str]:
    dom = parseString(path.read_text(encoding="utf-8"))
    result = {}
    for element in dom.childNodes[0].getElementsByTagName("string"):
        key = element.getAttribute("name")
        value = element.firstChild.data if element.firstChild else ""
        result[key] = value
    return result


def android_locale_to_bcp47(dirname: str) -> str:
    """Convert Android resource dir name (e.g. values-de-rDE) to BCP-47 (de-DE)."""
    suffix = dirname.removeprefix("values")
    if not suffix:
        return "en"
    suffix = suffix.lstrip("-")
    parts = suffix.split("-r")
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}-{parts[1]}"


def load_string_resources(project_root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}

    for path in sorted(project_root.glob("app/src/main/res/values-*/strings.xml")):
        locale = android_locale_to_bcp47(path.parent.name)
        resources = parse_strings_xml(path)
        result[locale] = {
            "title": resources.get("app_name", ""),
            "shortDescription": resources.get("short_description", ""),
        }

    for locale in list(result.keys()):
        if "-" in locale:
            lang = locale.split("-")[0]
            if lang not in result:
                result[lang] = result[locale]

    if "en" not in result:
        default_path = project_root / "app/src/main/res/values/strings.xml"
        if default_path.exists():
            resources = parse_strings_xml(default_path)
            result["en"] = {
                "title": resources.get("app_name", ""),
                "shortDescription": resources.get("short_description", ""),
            }

    return result


def find_strings(
    locale: str, strings: dict[str, dict[str, str]]
) -> dict[str, str]:
    if locale in strings:
        return dict(strings[locale])
    lang = locale.split("-")[0]
    if lang in strings:
        return dict(strings[lang])
    return dict(strings.get("en", {"title": "", "shortDescription": ""}))


def update_listings(
    service,
    edit_id: str,
    package_name: str,
    strings: dict[str, dict[str, str]],
    descriptions_dir: Path,
    prefix: str = "description",
) -> None:
    _google_imports()

    pattern = f"{prefix}-*.txt"
    paths = sorted(descriptions_dir.glob(pattern))

    if not paths:
        print(f"  No files matching '{pattern}' in {descriptions_dir}")
        return

    for path in paths:
        stem = path.stem
        locale = stem.removeprefix(prefix).lstrip("-")

        if not locale or locale.startswith("xxx") or locale.split("-")[0] in ("free",):
            print(f"  Skipping {path.name}")
            continue

        full_description = path.read_text(encoding="utf-8")
        listing = find_strings(locale, strings)
        listing["fullDescription"] = full_description

        short = listing.get("shortDescription", "")
        if len(short) > 80:
            print(f"  Skipping {locale}: shortDescription too long ({len(short)} chars, max 80)")
            continue

        print(f"  Updating {locale}...", end=" ")
        try:
            service.edits().listings().update(
                editId=edit_id,
                packageName=package_name,
                language=locale,
                body=listing,
            ).execute()
            print("OK")
        except HttpError as e:
            print(f"FAILED: {e}")
            continue


def _select_apps() -> list[dict]:
    print("Which app(s) do you want to update?")
    for i, app in enumerate(APPS, 1):
        print(f"  {i}. {app['name']} ({app['package']})")
    print(f"  {len(APPS) + 1}. Both")

    while True:
        try:
            choice = input(f"\nChoice [1-{len(APPS) + 1}]: ").strip()
            num = int(choice)
            if 1 <= num <= len(APPS):
                return [APPS[num - 1]]
            elif num == len(APPS) + 1:
                return APPS
        except (ValueError, IndexError):
            pass
        print(f"Invalid choice. Enter 1-{len(APPS) + 1}.")


def _update_app(
    service, app: dict, strings: dict, descriptions_dir: Path, dry_run: bool
) -> None:
    package = app["package"]
    prefix = app["prefix"]
    label = app["name"]

    print(f"\n{'='*60}")
    print(f"{label} ({package}) — prefix: {prefix}")
    print(f"{'='*60}")

    if dry_run:
        pattern = f"{prefix}-*.txt"
        print(f"\nDry run for {label}:")
        for path in sorted(descriptions_dir.glob(pattern)):
            locale = path.stem.removeprefix(prefix).lstrip("-")
            if not locale or locale.startswith("xxx") or locale.split("-")[0] in ("free",):
                continue
            entry = find_strings(locale, strings)
            print(
                f"  {locale}: "
                f"title={entry.get('title')!r}, "
                f"shortDescription={entry.get('shortDescription')!r}, "
                f"fullDescription={len(path.read_text(encoding='utf-8'))} chars"
            )
        return

    print("  Creating edit...")
    try:
        edit = service.edits().insert(body={}, packageName=package).execute()
    except HttpError as e:
        print(f"  Failed to create edit: {e}")
        raise

    edit_id = edit["id"]
    print(f"  Edit ID: {edit_id}")

    print("  Updating listings...")
    update_listings(service, edit_id, package, strings, descriptions_dir, prefix)

    print("  Committing edit...")
    try:
        service.edits().commit(editId=edit_id, packageName=package).execute()
    except HttpError as e:
        print(f"  Failed to commit edit: {e}")
        print("  Discarding edit...")
        try:
            service.edits().delete(editId=edit_id, packageName=package).execute()
        except HttpError:
            pass
        raise

    print(f"  {label} listing committed.\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update Google Play Store listings from Android resources."
    )
    parser.add_argument("--keyfile", help="Path to service account JSON key file")
    parser.add_argument(
        "--client-secrets",
        help="OAuth 2.0 Client ID JSON file (for desktop auth flow)",
    )
    parser.add_argument(
        "--package",
        help="Package name (skips interactive prompt; requires --prefix)",
    )
    parser.add_argument(
        "--prefix",
        help="Description file prefix (required with --package)",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="Project root (default: auto-detected from CWD by looking for marker paths)",
    )
    parser.add_argument(
        "--descriptions-dir",
        type=Path,
        help="Directory with description files (default: <project-root>/descriptions)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without making API calls",
    )
    args = parser.parse_args()

    if args.project_root:
        project_root = args.project_root.resolve()
    else:
        project_root = Path.cwd()
        markers = ["app/src/main/res/values/strings.xml", "descriptions", "flake.nix"]
        for _ in range(10):
            if any((project_root / m).exists() for m in markers):
                break
            parent = project_root.parent
            if parent == project_root:
                raise SystemExit("Could not detect project root. Use --project-root.")
            project_root = parent

    descriptions_dir = (
        args.descriptions_dir.resolve()
        if args.descriptions_dir
        else project_root / "descriptions"
    )

    if not descriptions_dir.is_dir():
        raise SystemExit(f"Descriptions directory not found: {descriptions_dir}")

    print(f"Project root:  {project_root}")
    print(f"Descriptions:  {descriptions_dir}")

    print("\nLoading string resources...")
    strings = load_string_resources(project_root)
    print(f"  Found {len(strings)} locales")

    if args.package or args.prefix:
        if not args.package or not args.prefix:
            raise SystemExit("--package and --prefix must be used together.")
        apps = [{"key": "custom", "name": "Custom", "package": args.package, "prefix": args.prefix}]
    else:
        apps = _select_apps()

    if args.dry_run:
        print()

    credentials = None
    service = None

    for app in apps:
        if not args.dry_run:
            if credentials is None:
                print("\nAuthenticating...")
                credentials = get_credentials(
                    keyfile=args.keyfile, client_secrets=args.client_secrets
                )
                service = build("androidpublisher", "v3", credentials=credentials)
        _update_app(service, app, strings, descriptions_dir, args.dry_run)

    if args.dry_run:
        return

    print("All done! Store listing edits and app releases are reviewed")
    print("independently by Google Play.")


if __name__ == "__main__":
    main()
