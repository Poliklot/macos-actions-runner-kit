#!/usr/bin/env python3
"""Resolve a real generic iOS build destination, without CocoaPods or signing.

SDK version metadata alone can exist while Xcode's platform support is missing.
All project/DerivedData files are temporary; no signing, device deployment or
package dependencies. Xcode resolves its own installed platform support.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile


def project(root: Path) -> Path:
    ids = [f"{n:024X}" for n in range(10)]
    objects = {
        ids[1]: {"isa": "PBXProject", "buildConfigurationList": ids[6],
                 "compatibilityVersion": "Xcode 14.0", "developmentRegion": "en",
                 "hasScannedForEncodings": 0, "knownRegions": ["en"], "mainGroup": ids[2],
                 "productRefGroup": ids[3], "projectDirPath": "", "projectRoot": "", "targets": [ids[5]]},
        ids[2]: {"isa": "PBXGroup", "children": [ids[3]], "sourceTree": "<group>"},
        ids[3]: {"isa": "PBXGroup", "children": [ids[4]], "name": "Products", "sourceTree": "<group>"},
        ids[4]: {"isa": "PBXFileReference", "explicitFileType": "wrapper.application",
                 "includeInIndex": 0, "path": "Probe.app", "sourceTree": "BUILT_PRODUCTS_DIR"},
        ids[5]: {"isa": "PBXNativeTarget", "buildConfigurationList": ids[7], "buildPhases": [],
                 "buildRules": [], "dependencies": [], "name": "Probe", "productName": "Probe",
                 "productReference": ids[4], "productType": "com.apple.product-type.application"},
        ids[6]: {"isa": "XCConfigurationList", "buildConfigurations": [ids[8]],
                 "defaultConfigurationIsVisible": 0, "defaultConfigurationName": "Release"},
        ids[7]: {"isa": "XCConfigurationList", "buildConfigurations": [ids[9]],
                 "defaultConfigurationIsVisible": 0, "defaultConfigurationName": "Release"},
        ids[8]: {"isa": "XCBuildConfiguration", "name": "Release", "buildSettings": {"SDKROOT": "iphoneos"}},
        ids[9]: {"isa": "XCBuildConfiguration", "name": "Release", "buildSettings": {
            "SDKROOT": "iphoneos", "SUPPORTED_PLATFORMS": "iphoneos iphonesimulator",
            "IPHONEOS_DEPLOYMENT_TARGET": "13.0", "PRODUCT_NAME": "Probe",
            "PRODUCT_BUNDLE_IDENTIFIER": "example.local.platform-probe", "GENERATE_INFOPLIST_FILE": "YES",
            "CODE_SIGNING_ALLOWED": "NO", "CODE_SIGNING_REQUIRED": "NO"}},
    }
    destination = root / "Probe.xcodeproj"
    schemes = destination / "xcshareddata/xcschemes"
    schemes.mkdir(parents=True)
    (destination / "project.pbxproj").write_bytes(plistlib.dumps({
        "archiveVersion": "1", "classes": {}, "objectVersion": "56",
        "objects": objects, "rootObject": ids[1]}, fmt=plistlib.FMT_XML))
    (schemes / "Probe.xcscheme").write_text(f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme version="1.3"><BuildAction><BuildActionEntries>
<BuildActionEntry buildForRunning="YES" buildForArchiving="YES">
<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{ids[5]}"
BuildableName="Probe.app" BlueprintName="Probe" ReferencedContainer="container:Probe.xcodeproj"/>
</BuildActionEntry></BuildActionEntries></BuildAction></Scheme>
''', encoding="utf-8")
    return destination


def available(env: dict | None = None) -> bool:
    with tempfile.TemporaryDirectory(prefix="ios-platform-probe-") as directory:
        root = Path(directory)
        path = project(root)
        try:
            result = subprocess.run([
                "xcodebuild", "-project", str(path), "-scheme", "Probe", "-configuration", "Release",
                "-sdk", "iphoneos", "-destination", "generic/platform=iOS", "-showBuildSettings",
                "-derivedDataPath", str(root / "DerivedData"), "CODE_SIGNING_ALLOWED=NO",
                "CODE_SIGNING_REQUIRED=NO"], env=env, cwd=root, capture_output=True, timeout=60)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False


if __name__ == "__main__":
    if available(dict(os.environ)):
        print("iOS generic device destination is available; signing was not accessed.")
    else:
        raise SystemExit("iOS platform is not build-ready. Install iOS support for the selected Xcode in Settings > Components.")
