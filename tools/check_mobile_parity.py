"""Static parity checks for the independent Web and mobile Flutter clients."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_LIB = ROOT / "frontend" / "lib"
MOBILE_LIB = ROOT / "mobile_app" / "lib"

IDENTICAL_SHARED_FILES = (
    "app_settings.dart",
    "app_theme.dart",
    "edit_models.dart",
    "editor_canvas.dart",
    "editor_localizations.dart",
    "editor_panels.dart",
    "speech_models.dart",
    "tool_dock.dart",
)

LOCALES = ("app_en.arb", "app_zh.arb", "app_zh_TW.arb")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def api_methods(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    interface = source.split("class ApiException", 1)[0]
    names = set(re.findall(r"\bFuture(?:<[^;{]+>)?\s+(\w+)\s*\(", interface))
    names.update(re.findall(r"\bString\s+(buildImageUrl)\s*\(", interface))
    return names


def editor_tools(path: Path) -> tuple[str, ...]:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"enum\s+EditorTool\s*\{([^}]+)\}", source)
    if not match:
        fail(f"EditorTool enum missing from {path}")
    return tuple(item.strip() for item in match.group(1).split(",") if item.strip())


def main() -> int:
    for relative in IDENTICAL_SHARED_FILES:
        web = (WEB_LIB / relative).read_bytes()
        mobile = (MOBILE_LIB / relative).read_bytes()
        if web != mobile:
            fail(f"shared file differs: {relative}")
    print(f"PASS: {len(IDENTICAL_SHARED_FILES)} shared Dart files are byte-identical")

    web_methods = api_methods(WEB_LIB / "api_service.dart")
    mobile_methods = api_methods(MOBILE_LIB / "api_service.dart")
    if web_methods != mobile_methods:
        fail(
            "EditorApi contract differs: "
            f"missing={sorted(web_methods - mobile_methods)}, "
            f"extra={sorted(mobile_methods - web_methods)}"
        )
    print(f"PASS: EditorApi contracts match ({len(web_methods)} methods)")

    web_tools = editor_tools(WEB_LIB / "editor_controller.dart")
    mobile_tools = editor_tools(MOBILE_LIB / "editor_controller.dart")
    if web_tools != mobile_tools:
        fail(f"EditorTool differs: web={web_tools}, mobile={mobile_tools}")
    print(f"PASS: EditorTool sets match ({', '.join(web_tools)})")

    for locale in LOCALES:
        web = json.loads((WEB_LIB / "l10n" / locale).read_text(encoding="utf-8"))
        mobile = json.loads(
            (MOBILE_LIB / "l10n" / locale).read_text(encoding="utf-8")
        )
        missing = set(web) - set(mobile)
        changed = {key for key in web.keys() & mobile.keys() if web[key] != mobile[key]}
        if missing or changed:
            fail(
                f"localization regression in {locale}: "
                f"missing={sorted(missing)}, changed={sorted(changed)}"
            )
        print(
            f"PASS: {locale} preserves {len(web)} Web entries and adds "
            f"{len(set(mobile) - set(web))} mobile entries"
        )

    manifest = (
        ROOT / "mobile_app" / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")
    for permission in (
        "android.permission.INTERNET",
        "android.permission.RECORD_AUDIO",
    ):
        if permission not in manifest:
            fail(f"Android permission missing: {permission}")

    plist = (ROOT / "mobile_app" / "ios" / "Runner" / "Info.plist").read_text(
        encoding="utf-8"
    )
    for key in (
        "NSPhotoLibraryUsageDescription",
        "NSPhotoLibraryAddUsageDescription",
        "NSMicrophoneUsageDescription",
        "NSLocalNetworkUsageDescription",
    ):
        if key not in plist:
            fail(f"iOS usage description missing: {key}")
    print("PASS: required Android permissions and iOS usage descriptions exist")

    print("Mobile parity checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
