#!/usr/bin/env python3
"""Guard the release-size settings that keep the Android app lean."""

from pathlib import Path
import importlib.util
import re
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BUILD_GRADLE = (ROOT / "android-app/android/app/build.gradle").read_text()
ROOT_BUILD_GRADLE = (ROOT / "android-app/android/build.gradle").read_text()
VARIABLES_GRADLE = (ROOT / "android-app/android/variables.gradle").read_text()
GRADLE_PROPERTIES = (ROOT / "android-app/android/gradle.properties").read_text()
RELEASE_SCRIPT = (ROOT / "tools/build-play-release.sh").read_text()
PROGUARD_RULES = (ROOT / "android-app/android/app/proguard-rules.pro").read_text()
WRAPPER_PROPERTIES = (
    ROOT / "android-app/android/gradle/wrapper/gradle-wrapper.properties"
).read_text()
ASSET_VERIFIER_PATH = ROOT / "tools/verify-release-assets.py"
DRIVE_PLUGIN = (
    ROOT
    / "android-app/android/app/src/main/java/dev/aiengg/potholereporter/plugin/DriveModePlugin.kt"
).read_text()
failures = []


def check(label, condition):
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(label)


release_match = re.search(r"buildTypes\s*\{[\s\S]*?release\s*\{([\s\S]*?)\n\s*}\n\s*}", BUILD_GRADLE)
release_block = release_match.group(1) if release_match else ""

check("release code shrinking is enabled",
      re.search(r"\bminifyEnabled\s+true\b", release_block)
      and not re.search(r"\bminifyEnabled\s+false\b", release_block))
check("release resource shrinking is enabled",
      re.search(r"\bshrinkResources\s+true\b", release_block))
check("release uses the optimizing Android rules",
      "getDefaultProguardFile('proguard-android-optimize.txt')" in release_block)
check("R8 uses code-aware resource shrinking",
      re.search(r"^android\.r8\.optimizedResourceShrinking=true$", GRADLE_PROPERTIES, re.MULTILINE))
check("Play release validation requires an R8 mapping",
      "R8 mapping is missing; release code shrinking is not active" in RELEASE_SCRIPT
      and "BUNDLE-METADATA/com.android.tools.build.obfuscation/proguard.map" in RELEASE_SCRIPT)
check("Play release validates the AGP 8.13 packaged manifest",
      "packaged_manifests/release/processReleaseManifestForPackage/AndroidManifest.xml"
      in RELEASE_SCRIPT)
check("Gradle distribution is pinned to the official 8.14.3 complete-ZIP digest",
      "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.14.3-all.zip"
      in WRAPPER_PROPERTIES
      and "distributionSha256Sum=ed1a8d686605fd7c23bdf62c7fc7add1c5b23b2bbc3721e661934ef4a4911d7c"
      in WRAPPER_PROPERTIES)
check("Kotlin compiler, KSP and runtime use one compatible release line",
      "kotlin-gradle-plugin:2.1.0" in ROOT_BUILD_GRADLE
      and "ksp.gradle.plugin:2.1.0-1.0.29" in ROOT_BUILD_GRADLE
      and "kotlinVersion = '2.1.0'" in VARIABLES_GRADLE)
check("declared CameraX and coroutines match the resolved Capacitor graph",
      "cameraXVersion = '1.4.2'" in VARIABLES_GRADLE
      and "coroutinesVersion = '1.10.2'" in VARIABLES_GRADLE)
check("dependency inspection does not demand release signing credentials",
      "releaseArtifactTaskPrefixes" in BUILD_GRADLE
      and "releaseArtifactTaskPrefixes.any { taskName.startsWith(it) }" in BUILD_GRADLE)
check("Capacitor plugin metadata and callbacks survive release obfuscation",
      "-keep @interface com.getcapacitor.annotation.CapacitorPlugin { *; }" in PROGUARD_RULES
      and "-keep @interface com.getcapacitor.annotation.Permission { *; }" in PROGUARD_RULES
      and "-keep @com.getcapacitor.annotation.CapacitorPlugin class * extends com.getcapacitor.Plugin { *; }"
      in PROGUARD_RULES
      and "@com.getcapacitor.annotation.PermissionCallback <methods>;" in PROGUARD_RULES
      and "@com.getcapacitor.annotation.ActivityCallback <methods>;" in PROGUARD_RULES
      and "@Keep\n@CapacitorPlugin(" in DRIVE_PLUGIN
      and "@Keep\n    @PermissionCallback\n    fun drivePermissionsResult" in DRIVE_PLUGIN)


def load_asset_verifier():
    spec = importlib.util.spec_from_file_location("release_asset_verifier", ASSET_VERIFIER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load release asset verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def rejects(operation, expected):
    try:
        operation()
    except Exception as error:
        return expected in str(error)
    return False


check("Play release invokes symmetric source and release-artifact verification",
      RELEASE_SCRIPT.count('python3 "$RELEASE_ASSET_VERIFIER"') == 2
      and "--static static --www" in RELEASE_SCRIPT
      and "--docs docs --packaged" in RELEASE_SCRIPT
      and '--aab "$AAB_PATH" --apk "$APK_PATH"' in RELEASE_SCRIPT)
check("release flow builds and verifies signed AAB and APK artifacts together",
      ":app:bundleRelease :app:assembleRelease" in RELEASE_SCRIPT
      and 'APK_PATH=$ANDROID_ROOT/app/build/outputs/apk/release/app-release.apk'
      in RELEASE_SCRIPT
      and '"$APKSIGNER" verify --verbose --print-certs "$APK_PATH"' in RELEASE_SCRIPT
      and "Verified using v2 scheme (APK Signature Scheme v2): true" in RELEASE_SCRIPT
      and "APK signer does not match the registered Pothole Reporter upload certificate"
      in RELEASE_SCRIPT)

try:
    verifier = load_asset_verifier()
    verifier.verify_source_trees(
        ROOT / "static",
        ROOT / "android-app/www",
        ROOT / "docs",
        ROOT / "android-app/android/app/src/main/assets/public",
    )
    current_mirrors_valid = True
except Exception:
    current_mirrors_valid = False
check("current static, docs, www and packaged asset mirrors are exact", current_mirrors_valid)

fixture_checks = []
try:
    with tempfile.TemporaryDirectory() as temporary:
        fixture = Path(temporary)
        static = fixture / "static"
        www = fixture / "www"
        docs = fixture / "docs"
        packaged = fixture / "packaged"
        for root in (static, www, docs, packaged):
            write(root / "app.js", b"app")
        write(docs / "privacy.html", b"hosted")
        write(docs / "packs/v1/sample.json", b"pack")
        for generated in verifier.CORDOVA_GENERATED_ASSETS:
            write(packaged / generated, b"generated")
        verifier.verify_source_trees(static, www, docs, packaged)

        write(www / "stale.js", b"stale")
        fixture_checks.append(rejects(
            lambda: verifier.verify_source_trees(static, www, docs, packaged),
            "static-to-www mirror file set differs",
        ))
        (www / "stale.js").unlink()

        write(docs / "app.js", b"drift")
        fixture_checks.append(rejects(
            lambda: verifier.verify_source_trees(static, www, docs, packaged),
            "static-to-docs mirror content differs",
        ))
        write(docs / "app.js", b"app")

        write(packaged / "stale.js", b"stale")
        fixture_checks.append(rejects(
            lambda: verifier.verify_source_trees(static, www, docs, packaged),
            "www-to-Android mirror file set differs",
        ))
        (packaged / "stale.js").unlink()

        good_aab = fixture / "good.aab"
        with zipfile.ZipFile(good_aab, "w") as archive:
            for path in packaged.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(packaged).as_posix()
                    archive.writestr(verifier.AAB_PUBLIC_PREFIX + relative, path.read_bytes())
        verifier.verify_aab(packaged, good_aab)

        good_apk = fixture / "good.apk"
        with zipfile.ZipFile(good_apk, "w") as archive:
            for path in packaged.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(packaged).as_posix()
                    archive.writestr(verifier.APK_PUBLIC_PREFIX + relative, path.read_bytes())
        verifier.verify_apk(packaged, good_apk)

        stale_aab = fixture / "stale.aab"
        with zipfile.ZipFile(stale_aab, "w") as archive:
            for path in packaged.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(packaged).as_posix()
                    archive.writestr(verifier.AAB_PUBLIC_PREFIX + relative, path.read_bytes())
            archive.writestr(verifier.AAB_PUBLIC_PREFIX + "stale.js", b"stale")
        fixture_checks.append(rejects(
            lambda: verifier.verify_aab(packaged, stale_aab),
            "AAB public assets file set differs",
        ))

        stale_apk = fixture / "stale.apk"
        with zipfile.ZipFile(stale_apk, "w") as archive:
            for path in packaged.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(packaged).as_posix()
                    archive.writestr(verifier.APK_PUBLIC_PREFIX + relative, path.read_bytes())
            archive.writestr(verifier.APK_PUBLIC_PREFIX + "stale.js", b"stale")
        fixture_checks.append(rejects(
            lambda: verifier.verify_apk(packaged, stale_apk),
            "APK public assets file set differs",
        ))
except Exception:
    fixture_checks.append(False)
check("asset verifier rejects stale source, hosted, packaged, AAB and APK entries",
      fixture_checks == [True, True, True, True, True])

if failures:
    print(f"\nFAIL: {len(failures)} Android release optimization check(s) failed")
    sys.exit(1)
print("\nANDROID RELEASE OPTIMIZATION TEST PASS")
