"""Run the real workflow shell blocks offline, with a closed executable PATH."""

import base64
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import unittest
import uuid

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/release.yml"
PRODUCT = "GET TEXT INPUT"
BUNDLE = Path("test/Plugins") / f"{PRODUCT}.bundle"
CONTENTS = BUNDLE / "Contents"
NATIVE = (
    "xcodebuild", "lipo", "security", "codesign", "xcrun", "ditto",
    "hdiutil", "plutil", "msbuild", "4D", "xattr", "notarytool",
)
PORTABLE = ("rm", "ls", "cp", "mkdir", "grep", "head", "sed", "base64", "cat")


def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def source_version():
    project = (ROOT / f"{PRODUCT}.xcodeproj/project.pbxproj").read_text()
    versions = re.findall(r"\bMARKETING_VERSION\s*=\s*(\d+\.\d+\.\d+);", project)
    if not versions or len(set(versions)) != 1:
        raise AssertionError("Source marketing versions must agree")
    return versions[0]


STUB = r'''
import json, os, pathlib, plistlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["NATIVE_LOG"], "a") as stream:
    stream.write(json.dumps([name, args]) + "\n")
scenario = os.environ.get("SCENARIO", "")
root = pathlib.Path.cwd()
bundle = root / os.environ["BUNDLE_PATH"]
contents = bundle / "Contents"
product = os.environ["PRODUCT_NAME"]
def stop(message, code=19):
    print(message, file=sys.stderr)
    sys.exit(code)
if name == "xcodebuild":
    stale = [
        contents / "MacOS" / product, contents / "Info.plist",
        contents / "Resources/English.lproj/InfoPlist.strings",
        contents / "_CodeSignature", bundle.parent / (product + ".build"),
    ]
    if any(path.exists() for path in stale):
        stop("stale generated outputs survived cleanup")
    if scenario == "build-fail":
        stop("xcode build failed")
    contents.mkdir(parents=True, exist_ok=True)
    if scenario != "build-missing":
        binary = contents / "MacOS" / product
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"fresh universal binary")
    metadata = plistlib.loads((root / "Info.plist").read_bytes())
    metadata["CFBundleShortVersionString"] = os.environ["TEST_VERSION"]
    (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
    localized = contents / "Resources/English.lproj/InfoPlist.strings"
    localized.parent.mkdir(parents=True, exist_ok=True)
    localized.write_bytes((root / "English.lproj/InfoPlist.strings").read_bytes())
elif name == "lipo":
    if scenario == "lipo-fail":
        stop("lipo failed")
    print(os.environ.get("ARCH_INFO", "Architectures in the fat file: arm64 x86_64"))
elif name == "security":
    if scenario == "security-fail":
        stop("keychain failure")
    if args and args[0] == "find-identity" and scenario != "no-identity":
        print('  1) ABCDEF "Developer ID Application: Offline Fixture (TEST)"')
elif name == "codesign":
    verify = "--verify" in args
    if scenario == ("verify-fail" if verify else "sign-fail"):
        stop("codesign failed")
elif name in ("ditto", "hdiutil"):
    if scenario == name + "-fail":
        stop("packaging failed")
    pathlib.Path(args[-1]).write_bytes(b"offline package, not a real archive")
elif name == "xcrun":
    if args[:2] == ["notarytool", "store-credentials"]:
        if scenario == "credentials-fail":
            stop("credentials failure")
    elif args[:2] == ["notarytool", "submit"]:
        suffix = pathlib.Path(args[2]).suffix[1:]
        status = "Accepted"
        if scenario in ("reject-" + suffix, "invalid"):
            status = "Invalid"
        if scenario == "missing-status":
            print(json.dumps({"id": "offline-id"}))
        elif scenario == "malformed":
            print("this is not JSON")
        else:
            print(json.dumps({"id": "offline-id", "status": status}))
        if scenario == "notary-exit-" + suffix:
            sys.exit(23)
    elif args[:2] == ["notarytool", "log"]:
        print("offline rejection log")
    elif args[:2] == ["stapler", "staple"]:
        if scenario == "staple-fail":
            stop("stapling failed")
    else:
        stop("unexpected xcrun invocation")
elif name == "plutil":
    try:
        value = json.load(sys.stdin)[args[args.index("-extract") + 1]]
    except (ValueError, KeyError):
        stop("invalid notary response")
    print(value)
else:
    stop("unexpected native command: " + name)
'''


GATE = r'''
import json, os, sys
name = __file__.split("/")[-1]
with open(os.environ["NATIVE_LOG"], "a") as stream:
    stream.write(json.dumps([name, sys.argv[1:]]) + "\n")
scenario = os.environ.get("SCENARIO", "")
if name == "source_version.py":
    sys.exit(31 if scenario == "bundle-metadata-fail" else 0)
command = sys.argv[1]
if scenario == command + "-fail":
    sys.exit(32)
if command == "preflight":
    with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
        stream.write("version=" + os.environ["TEST_VERSION"] + "\n")
        stream.write("tag=v" + os.environ["TEST_VERSION"] + "\n")
'''


class NativeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".github/tests" / (".native-fixture-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.bin = self.root / "closed-bin"
        self.bin.mkdir()
        self.bash = shutil.which("bash")
        self.pwsh = shutil.which("pwsh")
        self.assertTrue(self.bash, "Bash is required, not skipped")
        self.assertTrue(self.pwsh, "PowerShell is required, not translated or skipped")
        for name in PORTABLE:
            executable = shutil.which(name)
            self.assertTrue(executable, f"Missing explicit portable utility {name}")
            (self.bin / name).symlink_to(executable)
        (self.bin / "python3").symlink_to(sys.executable)
        for name in NATIVE:
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + STUB)
            path.chmod(0o755)
        self.log = self.root / "native.jsonl"
        self.version = source_version()
        self.env = {
            "PATH": str(self.bin), "HOME": str(self.root),
            "LANG": "C", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
            "NATIVE_LOG": str(self.log), "PRODUCT_NAME": PRODUCT,
            "BUNDLE_PATH": str(BUNDLE), "TEST_VERSION": self.version,
            "GITHUB_ENV": str(self.root / "github-env"),
            "GITHUB_OUTPUT": str(self.root / "github-output"),
            "RUNNER_TEMP": str(self.root / "runner"),
            "KEYCHAIN_PATH": str(self.root / "runner/build.keychain-db"),
            "SELECTED_SHA": "a" * 40, "CERT_BASE64": base64.b64encode(b"fake").decode(),
            "CERT_PASSWORD": "offline", "KEYCHAIN_PASSWORD": "offline",
            "APPLE_ID": "offline@example.invalid", "TEAM_ID": "OFFLINE",
            "APP_PASSWORD": "offline",
        }
        (self.root / "runner").mkdir()
        for path in ("Info.plist", "English.lproj/InfoPlist.strings"):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        scripts = self.root / ".github/scripts"
        scripts.mkdir(parents=True)
        for name in ("source_version.py", "release.py"):
            (scripts / name).write_text(GATE)
        self.jobs = workflow()["jobs"]
        self.steps = {
            step["name"]: step
            for job in self.jobs.values() for step in job["steps"] if "run" in step
        }

    def put(self, path, data=b"keep this exact resource"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] \
            if self.log.exists() else []

    def run_step(self, name, success=True, **extra):
        step = self.steps[name]
        script = step["run"]
        replacements = {
            "env.PRODUCT_NAME": PRODUCT, "env.BASE_DIR": "test",
            "env.MACOS_DEPLOYMENT_TARGET": "11.0",
            "needs.version.outputs.version": self.version,
        }
        for key, value in replacements.items():
            script = script.replace("${{ " + key + " }}", value)
        self.assertNotIn("${{", script, "Every expression must be explicitly resolved")
        for native in NATIVE:
            self.assertNotRegex(script, r"/[^\s\"']*/" + re.escape(native) + r"\b",
                                "Native commands must resolve through the closed stub PATH")
        shell = step.get("shell", "bash")
        if shell == "pwsh":
            command = [self.pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script]
        else:
            self.assertEqual(shell, "bash", "CMD must remain static; never translate or execute it")
            command = [self.bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script]
        result = subprocess.run(
            command, cwd=self.root, env={**self.env, **extra}, text=True,
            capture_output=True, timeout=30,
        )
        if success is not None:
            self.assertEqual(result.returncode == 0, success, name + "\n" + result.stdout + result.stderr)
        if result.returncode == 0:
            envfile = Path(self.env["GITHUB_ENV"])
            if envfile.exists():
                for line in envfile.read_text().splitlines():
                    key, value = line.split("=", 1)
                    self.env[key] = value
        return result

    def test_closed_path_has_only_allowlisted_utilities_and_native_stubs(self):
        self.assertEqual(
            set(path.name for path in self.bin.iterdir()), set(PORTABLE) | set(NATIVE) | {"python3"},
        )
        for name in NATIVE:
            target = self.bin / name
            self.assertFalse(target.is_symlink())
            self.assertEqual(shutil.which(name, path=self.env["PATH"]), str(target))
            self.assertIn("unexpected native command", target.read_text())
        for forbidden in ("git", "gh", "curl", "clang", "make", "xcode-select"):
            self.assertIsNone(shutil.which(forbidden, path=self.env["PATH"]))
        result = subprocess.run(
            [self.bash, "--noprofile", "--norc", "-c", "command -v xcodebuild; command -v clang"],
            env=self.env, cwd=self.root, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), str(self.bin / "xcodebuild"))
        result = subprocess.run(
            [self.pwsh, "-NoProfile", "-NonInteractive", "-Command",
             "(Get-Command xcodebuild -CommandType Application).Source"],
            env=self.env, cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.bin / "xcodebuild"))

    def test_windows_cleanup_is_selective_and_idempotent(self):
        stale = [
            Path(f"test/plugins/{PRODUCT}.bundle/contents/windows64/{PRODUCT}.4DX"),
            Path("Objs/Release/object.obj"),
        ]
        keep = [
            CONTENTS / "Resources/resource.rsrc", CONTENTS / "MacOS" / PRODUCT,
            CONTENTS / "Windows/helper.dll", CONTENTS / "Windows64/dependency.dll",
            Path(f"test/plugins/{PRODUCT}.bundle/contents/windows64/dependency.dll"),
            Path("test/Plugins/Default/sibling.resource"), Path("test/plugins/deps/lib.a"),
            Path("Objs/Debug/debug.obj"),
        ]
        for path in stale + keep:
            self.put(path)
        self.run_step("clean stale outputs")
        self.run_step("clean stale outputs")
        for path in stale:
            self.assertFalse((self.root / path).exists(), path)
        for path in keep:
            self.assertEqual((self.root / path).read_bytes(), b"keep this exact resource", path)
        self.assertEqual(self.calls(), [])

    def test_windows_normalization_requires_exact_vcxproj_output_not_recursive_decoy(self):
        self.put(f"decoy/Plugins/{PRODUCT}.bundle/{PRODUCT}.4DX", b"decoy")
        self.run_step("normalize windows output to canonical case", success=False)
        self.put(f"test/plugins/{PRODUCT}.bundle/contents/windows64/{PRODUCT}.4DX", b"real MSBuild output")
        self.run_step("normalize windows output to canonical case")
        self.assertEqual(
            (self.root / CONTENTS / "Windows64" / f"{PRODUCT}.4DX").read_bytes(),
            b"real MSBuild output",
        )
        self.run_step("normalize windows output to canonical case")
        self.assertEqual(self.calls(), [])

    def test_mac_cleanup_preserves_bundle_dependencies_windows_and_default_resources(self):
        generated = [
            CONTENTS / "MacOS" / PRODUCT, CONTENTS / "Info.plist",
            CONTENTS / "Resources/English.lproj/InfoPlist.strings",
            CONTENTS / "_CodeSignature/CodeResources",
            Path(f"test/Plugins/{PRODUCT}.build/stale.o"),
        ]
        keep = [
            CONTENTS / "Resources/manifest.json", CONTENTS / "Resources/other.lproj/data",
            CONTENTS / "Windows64" / f"{PRODUCT}.4DX",
            CONTENTS / "Windows64/dependency.dll", CONTENTS / "Windows/legacy.4DX",
            CONTENTS / "Frameworks/dependency.framework/data",
            Path("test/Plugins/Default/sibling.resource"), Path("test/plugins/deps/lib.a"),
            Path("test/Plugins/Another.bundle/Contents/Resources/data"),
        ]
        for path in generated:
            self.put(path, b"stale")
        for path in keep:
            self.put(path)
        self.run_step("build macOS plugin (universal)")
        for path in keep:
            self.assertEqual((self.root / path).read_bytes(), b"keep this exact resource", path)
        self.assertEqual((self.root / CONTENTS / "MacOS" / PRODUCT).read_bytes(), b"fresh universal binary")
        self.assertFalse((self.root / CONTENTS / "_CodeSignature").exists())
        self.assertFalse((self.root / f"test/Plugins/{PRODUCT}.build").exists())
        args = self.calls()[0][1]
        self.assertEqual(self.calls()[0][0], "xcodebuild")
        self.assertEqual(args[args.index("-configuration") + 1], "Default")
        self.assertEqual(args[args.index("-project") + 1], f"{PRODUCT}.xcodeproj")
        self.assertEqual(args[args.index("-target") + 1], PRODUCT)
        for flag in (
            "ARCHS=arm64 x86_64", "ONLY_ACTIVE_ARCH=NO", "CODE_SIGNING_ALLOWED=NO",
            "MACOSX_DEPLOYMENT_TARGET=11.0",
            f"CONFIGURATION_BUILD_DIR={self.root}/test/Plugins",
        ):
            self.assertIn(flag, args)
        self.assertEqual(args[-1], "build")

    def test_mac_build_failure_and_missing_binary_fail_closed(self):
        self.run_step("build macOS plugin (universal)", success=False, SCENARIO="build-fail")
        self.run_step("build macOS plugin (universal)", SCENARIO="build-missing")
        self.run_step("verify universal binary", success=False)
        self.assertFalse(any(name == "source_version.py" for name, _ in self.calls()))

    def test_architecture_tokens_are_exact_but_extra_architectures_are_allowed(self):
        self.put(CONTENTS / "MacOS" / PRODUCT)
        for info, accepted in (
            ("Architectures in file: arm64 x86_64", True),
            ("Architectures in file: x86_64 arm64 i386", True),
            ("Architectures in file:\tarm64\tx86_64", True),
            ("arm64e x86_64", False), ("arm64 x86_64h", False),
            ("prearm64 x86_64", False), ("arm64 x86_64_suffix", False),
            ("arm64", False), ("x86_64", False), ("", False),
        ):
            with self.subTest(info=info):
                self.log.unlink(missing_ok=True)
                self.run_step("verify universal binary", success=accepted, ARCH_INFO=info)
                self.assertEqual(any(name == "source_version.py" for name, _ in self.calls()), accepted)
        self.run_step("verify universal binary", success=False, SCENARIO="lipo-fail")
        self.run_step("verify universal binary", success=False, SCENARIO="bundle-metadata-fail")

    def test_exact_windows_artifact_merge_rejects_missing_empty_and_nested_decoys(self):
        self.put(f"winbuild/nested/{PRODUCT}.4DX", b"decoy")
        self.run_step("merge Windows binaries into the bundle", success=False)
        self.put(f"winbuild/{PRODUCT}.4DX", b"")
        self.run_step("merge Windows binaries into the bundle", success=False)
        resource = self.put(CONTENTS / "Resources/keep")
        self.put(f"winbuild/{PRODUCT}.4DX", b"exact artifact")
        self.run_step("merge Windows binaries into the bundle")
        self.assertEqual((self.root / CONTENTS / "Windows64" / f"{PRODUCT}.4DX").read_bytes(), b"exact artifact")
        self.assertEqual(resource.read_bytes(), b"keep this exact resource")
        self.assertEqual(self.env["BUNDLE_PATH"], str(BUNDLE))

    def test_certificate_import_and_notary_credentials_are_stubbed(self):
        self.run_step("import Developer ID certificate")
        self.run_step("store notarytool credentials")
        self.assertEqual((self.root / "runner/certificate.p12").read_bytes(), b"fake")
        operations = [args[0] for name, args in self.calls() if name == "security"]
        self.assertEqual(operations, [
            "create-keychain", "set-keychain-settings", "unlock-keychain", "import",
            "list-keychain", "set-key-partition-list",
        ])
        self.assertEqual(self.calls()[-1][1][:2], ["notarytool", "store-credentials"])
        self.run_step("import Developer ID certificate", success=False, SCENARIO="security-fail")
        self.run_step("store notarytool credentials", success=False, SCENARIO="credentials-fail")

    def test_signing_requires_identity_sign_success_and_strict_verify(self):
        self.run_step("codesign")
        signs = [args for name, args in self.calls() if name == "codesign"]
        self.assertEqual(len(signs), 2)
        for flag in ("--deep", "--timestamp", "--force", "--options=runtime", "--sign"):
            self.assertIn(flag, signs[0])
        for flag in ("--verify", "--deep", "--strict"):
            self.assertIn(flag, signs[1])
        self.assertEqual(signs[0][-1], str(BUNDLE))
        for scenario, expected_calls in (("no-identity", 0), ("sign-fail", 1), ("verify-fail", 2)):
            with self.subTest(scenario=scenario):
                self.log.unlink()
                self.run_step("codesign", success=False, SCENARIO=scenario)
                self.assertEqual(sum(name == "codesign" for name, _ in self.calls()), expected_calls)

    def test_package_names_formats_and_both_accepted_notarizations_then_staple(self):
        self.run_step("package, notarize & staple")
        zipname = f"{PRODUCT}-{self.version}.zip"
        dmgname = f"{PRODUCT}-{self.version}.dmg"
        calls = self.calls()
        self.assertEqual(calls[0], ["ditto", ["-c", "-k", "--keepParent", str(BUNDLE), zipname]])
        self.assertEqual(calls[1], ["hdiutil", [
            "create", "-format", "UDBZ", "-volname", PRODUCT, "-srcfolder", str(BUNDLE), dmgname,
        ]])
        submits = [args for name, args in calls if name == "xcrun" and args[:2] == ["notarytool", "submit"]]
        self.assertEqual([args[2] for args in submits], [zipname, dmgname])
        for args in submits:
            self.assertIn("--wait", args)
            self.assertEqual(args[args.index("--output-format") + 1], "json")
            self.assertEqual(args[args.index("--keychain-profile") + 1], "notarytool-profile")
            self.assertEqual(args[args.index("--keychain") + 1], self.env["KEYCHAIN_PATH"])
        self.assertEqual(calls[-1], ["xcrun", ["stapler", "staple", dmgname]])
        self.assertEqual(self.env["ZIP_PATH"], zipname)
        self.assertEqual(self.env["DMG_PATH"], dmgname)
        self.assertTrue((self.root / zipname).is_file())
        self.assertTrue((self.root / dmgname).is_file())

    def test_future_source_version_flows_to_both_exact_archive_names(self):
        major, minor, patch = map(int, self.version.split("."))
        self.version = f"{major}.{minor}.{patch + 1}"
        self.env["TEST_VERSION"] = self.version
        self.run_step("validate source and publication availability")
        self.run_step("package, notarize & staple")
        self.assertIn(f"version={self.version}", Path(self.env["GITHUB_OUTPUT"]).read_text())
        self.assertEqual(self.env["ZIP_PATH"], f"{PRODUCT}-{self.version}.zip")
        self.assertEqual(self.env["DMG_PATH"], f"{PRODUCT}-{self.version}.dmg")
        submissions = [args[2] for name, args in self.calls()
                       if name == "xcrun" and args[:2] == ["notarytool", "submit"]]
        self.assertEqual(submissions, [self.env["ZIP_PATH"], self.env["DMG_PATH"]])

    def test_notary_requires_exit_zero_and_accepted_json_and_successful_staple(self):
        for scenario in (
            "ditto-fail", "hdiutil-fail", "notary-exit-zip", "notary-exit-dmg",
            "reject-zip", "reject-dmg", "missing-status", "malformed", "staple-fail",
        ):
            with self.subTest(scenario=scenario):
                self.log.unlink(missing_ok=True)
                Path(self.env["GITHUB_ENV"]).unlink(missing_ok=True)
                self.run_step("package, notarize & staple", success=False, SCENARIO=scenario)
                staples = [args for name, args in self.calls() if name == "xcrun" and args[:2] == ["stapler", "staple"]]
                self.assertEqual(len(staples), int(scenario == "staple-fail"))
                self.assertFalse(Path(self.env["GITHUB_ENV"]).exists())
                if scenario in ("reject-zip", "notary-exit-zip", "missing-status", "malformed"):
                    submits = [args for name, args in self.calls() if name == "xcrun" and args[:2] == ["notarytool", "submit"]]
                    self.assertEqual(len(submits), 1)

    def test_actual_preflight_and_post_staple_final_check_gate_publication(self):
        self.run_step("validate source and publication availability")
        self.run_step("build macOS plugin (universal)")
        self.run_step("verify universal binary")
        self.put(f"winbuild/{PRODUCT}.4DX", b"windows artifact")
        self.run_step("merge Windows binaries into the bundle")
        self.run_step("codesign")
        self.run_step("package, notarize & staple")
        self.run_step("create GitHub Release")
        calls = self.calls()
        self.assertEqual(calls[0], ["release.py", ["preflight", "--sha", "a" * 40]])
        self.assertEqual(calls[-2], ["release.py", ["check", "--sha", "a" * 40]])
        self.assertEqual(calls[-1], ["release.py", [
            "publish", "--sha", "a" * 40,
            f"{PRODUCT}-{self.version}.zip", f"{PRODUCT}-{self.version}.dmg",
        ]])
        self.assertEqual(calls[-3][1][:2], ["stapler", "staple"])
        self.log.unlink()
        self.run_step("validate source and publication availability", success=False, SCENARIO="preflight-fail")
        self.assertEqual([name for name, _ in self.calls()], ["release.py"])
        self.log.unlink()
        self.run_step("create GitHub Release", success=False, SCENARIO="check-fail")
        self.assertEqual(self.calls(), [["release.py", ["check", "--sha", "a" * 40]]])

    def test_pipeline_failure_never_reaches_publication(self):
        order = (
            "validate source and publication availability",
            "build macOS plugin (universal)", "verify universal binary",
            "merge Windows binaries into the bundle", "codesign",
            "package, notarize & staple", "create GitHub Release",
        )
        self.put(f"winbuild/{PRODUCT}.4DX", b"windows artifact")
        failures = {
            "preflight-fail": order[0], "build-fail": order[1],
            "bundle-metadata-fail": order[2], "sign-fail": order[4],
            "verify-fail": order[4], "notary-exit-zip": order[5],
            "notary-exit-dmg": order[5], "reject-zip": order[5],
            "reject-dmg": order[5], "staple-fail": order[5], "check-fail": order[6],
        }
        for scenario, expected_step in failures.items():
            with self.subTest(scenario=scenario):
                self.log.unlink(missing_ok=True)
                failed_step = None
                for name in order:
                    result = self.run_step(name, success=None, SCENARIO=scenario)
                    if result.returncode:
                        failed_step = name
                        break
                self.assertEqual(failed_step, expected_step)
                self.assertFalse(any(name == "release.py" and args[0] == "publish"
                                     for name, args in self.calls()))
                if scenario == "preflight-fail":
                    self.assertEqual([name for name, _ in self.calls()], ["release.py"])

    def test_real_source_verifier_integration_checks_fresh_bundle_and_localized_versions(self):
        helper = ROOT / ".github/scripts/source_version.py"
        self.assertTrue(helper.is_file(), "Real source_version.py must be available; no integration skip")
        shutil.copyfile(helper, self.root / ".github/scripts/source_version.py")
        project = Path(f"{PRODUCT}.xcodeproj/project.pbxproj")
        target = self.root / project
        target.parent.mkdir()
        shutil.copyfile(ROOT / project, target)
        self.run_step("build macOS plugin (universal)")
        self.run_step("verify universal binary")
        localized = self.root / CONTENTS / "Resources/English.lproj/InfoPlist.strings"
        localized.write_text(localized.read_text().replace(self.version, "999.998.997"))
        self.run_step("verify universal binary", success=False)
        shutil.copyfile(ROOT / "English.lproj/InfoPlist.strings", localized)
        metadata = self.root / CONTENTS / "Info.plist"
        values = plistlib.loads(metadata.read_bytes())
        values["CFBundleShortVersionString"] = "999.998.997"
        metadata.write_bytes(plistlib.dumps(values))
        self.run_step("verify universal binary", success=False)
        metadata.unlink()
        self.run_step("verify universal binary", success=False)
        shutil.copyfile(ROOT / "English.lproj/InfoPlist.strings", self.root / "English.lproj/InfoPlist.strings")
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.github/scripts'); "
             "import source_version as source; source.bump('patch'); print(source.read())"],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        major, minor, patch = map(int, self.version.split("."))
        self.version = f"{major}.{minor}.{patch + 1}"
        self.assertEqual(result.stdout.strip().splitlines()[-1], self.version)
        self.env["TEST_VERSION"] = self.version
        self.run_step("build macOS plugin (universal)")
        self.run_step("verify universal binary")
        self.run_step("package, notarize & staple")
        self.assertEqual(self.env["ZIP_PATH"], f"{PRODUCT}-{self.version}.zip")
        self.assertEqual(self.env["DMG_PATH"], f"{PRODUCT}-{self.version}.dmg")


if __name__ == "__main__":
    unittest.main()
