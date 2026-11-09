"""Contracts derived from the actual workflow and native project configuration."""

import ast
from pathlib import Path
import re
import subprocess
import unittest
import xml.etree.ElementTree as ET

import yaml


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = "GET TEXT INPUT"


def load(path):
    return yaml.safe_load(path.read_text())


class RepositoryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflows = {
            path.name: load(path) for path in (ROOT / ".github/workflows").glob("*.yml")
        }
        cls.build = cls.workflows["release.yml"]
        cls.original = yaml.safe_load(subprocess.run(
            ["git", "--no-pager", "show", "HEAD:.github/workflows/release.yml"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout)
        cls.project = (ROOT / f"{PRODUCT}.xcodeproj/project.pbxproj").read_text()

    def test_exact_two_manual_only_workflows_with_only_bump_mode_input(self):
        self.assertEqual(len(self.workflows), 2)
        self.assertEqual(
            {workflow["name"] for workflow in self.workflows.values()},
            {"Build", "Bump"},
        )
        for filename, workflow in self.workflows.items():
            with self.subTest(workflow=filename):
                triggers = workflow.get("on", workflow.get(True))
                self.assertEqual(set(triggers), {"workflow_dispatch"})
                if workflow["name"] == "Build":
                    self.assertIsNone(triggers["workflow_dispatch"])
                    self.assertNotIn("inputs.", (ROOT / ".github/workflows" / filename).read_text())
                else:
                    self.assertEqual(set(triggers["workflow_dispatch"]), {"inputs"})
                    inputs = triggers["workflow_dispatch"]["inputs"]
                    self.assertEqual(set(inputs), {"mode"})
                    self.assertEqual(inputs["mode"]["type"], "choice")
                    self.assertEqual(inputs["mode"]["options"], ["patch", "minor", "major"])
                    self.assertEqual(inputs["mode"]["default"], "patch")
                    self.assertIs(inputs["mode"]["required"], True)

    def test_every_checkout_uses_immutable_selected_sha(self):
        for filename, workflow in self.workflows.items():
            for job in workflow["jobs"].values():
                checkouts = [step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
                self.assertEqual(len(checkouts), 1, filename)
                self.assertEqual(checkouts[0]["with"]["ref"], "${{ github.sha }}")
                if workflow["name"].startswith("Bump"):
                    self.assertEqual(checkouts[0]["with"]["fetch-depth"], 0)

    def test_concurrency_is_constant_cross_ref_non_cancelling(self):
        bump_groups = set()
        for workflow in self.workflows.values():
            concurrency = workflow["concurrency"]
            self.assertIs(concurrency["cancel-in-progress"], False)
            group = concurrency["group"]
            self.assertIsInstance(group, str)
            self.assertTrue(group.strip())
            self.assertNotIn("${{", group)
            if workflow["name"].startswith("Bump"):
                bump_groups.add(group)
        self.assertEqual(len(bump_groups), 1)
        self.assertNotIn(self.build["concurrency"]["group"], bump_groups)

    def test_bump_dispatches_only_selected_source_mode_without_build_or_tag(self):
        for workflow in self.workflows.values():
            if not workflow["name"].startswith("Bump"):
                continue
            self.assertEqual(set(workflow["jobs"]), {"bump"})
            job = workflow["jobs"]["bump"]
            scripts = "\n".join(step.get("run", "") for step in job["steps"])
            self.assertIn('--mode "$MODE"', scripts)
            self.assertIn('test "$REF_TYPE" = branch', scripts)
            helpers = [step for step in job["steps"] if ".github/scripts/release.py bump" in step.get("run", "")]
            self.assertEqual(len(helpers), 1)
            self.assertEqual(helpers[0]["env"], {
                "SELECTED_SHA": "${{ github.sha }}",
                "SELECTED_BRANCH": "${{ github.ref_name }}",
                "REF_TYPE": "${{ github.ref_type }}",
                "MODE": "${{ inputs.mode }}",
            })
            self.assertNotRegex(scripts, r"\b(?:xcodebuild|msbuild|codesign|notarytool|workflow_dispatch|repository_dispatch)\b")
            self.assertNotRegex(scripts, r"\bgit\s+(?:tag|push)\b")
            self.assertNotRegex(scripts, r"\bgh\s+(?:release|workflow|api)\b")

    def test_native_job_topology_is_windows_x64_then_macos_default_universal(self):
        jobs = self.build["jobs"]
        self.assertEqual(set(jobs), {"version", "build-windows", "build-macos"})
        self.assertEqual(jobs["build-windows"]["runs-on"], "windows-latest")
        self.assertEqual(jobs["build-windows"]["needs"], "version")
        self.assertEqual(jobs["build-macos"]["runs-on"], "macos-latest")
        self.assertEqual(jobs["build-macos"]["needs"], ["version", "build-windows"])
        for job in jobs.values():
            self.assertNotIn("strategy", job)
        current = [step for step in jobs["build-windows"]["steps"] if step.get("shell") == "cmd"]
        original = [step for step in self.original["jobs"]["build-windows"]["steps"] if step.get("shell") == "cmd"]
        self.assertEqual(current, original, "The original CMD MSBuild block is preserved verbatim, not translated")
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["run"].strip(),
            'msbuild "%PRODUCT_NAME%.sln" /p:Configuration=Release /p:Platform=x64 '
            '/p:PlatformToolset=v143 /p:WindowsTargetPlatformVersion=10.0 /m')
        mac = next(step["run"] for step in jobs["build-macos"]["steps"] if step.get("name") == "build macOS plugin (universal)")
        for flag in ('-configuration Default', 'ARCHS="arm64 x86_64"', "ONLY_ACTIVE_ARCH=NO",
                     "CODE_SIGNING_ALLOWED=NO", 'CONFIGURATION_BUILD_DIR="$PWD/$PLUGINS_DIR"'):
            self.assertIn(flag, mac)

    def test_exact_windows_outdir_artifact_and_download_contract(self):
        namespace = {"m": "http://schemas.microsoft.com/developer/msbuild/2003"}
        project = ET.parse(ROOT / f"{PRODUCT}.vcxproj")
        def property_value(name):
            values = [element.text for element in project.findall(f".//m:{name}", namespace)
                      if element.attrib.get("Condition") == "'$(Configuration)|$(Platform)'=='Release|x64'"]
            self.assertEqual(len(values), 1)
            return values[0]
        self.assertEqual(property_value("OutDir"),
                         r"$(ProjectDir)test\plugins\$(ProjectName).bundle\contents\windows64" + "\\")
        self.assertEqual(property_value("TargetExt"), ".4DX")
        windows = self.build["jobs"]["build-windows"]["steps"]
        upload = next(step for step in windows if step.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(upload["with"], {
            "name": "windows-binaries",
            "path": "${{ env.BASE_DIR }}/Plugins/${{ env.PRODUCT_NAME }}.bundle/Contents/Windows64/${{ env.PRODUCT_NAME }}.4DX",
            "if-no-files-found": "error",
        })
        mac = self.build["jobs"]["build-macos"]["steps"]
        download = next(step for step in mac if step.get("uses", "").startswith("actions/download-artifact@"))
        self.assertEqual(download["with"], {"name": "windows-binaries", "path": "winbuild"})
        normalization = next(step["run"] for step in windows if step.get("name") == "normalize windows output to canonical case")
        self.assertIn('$source = "$base/plugins/$product.bundle/contents/windows64/$product.4DX"', normalization)
        self.assertNotRegex(normalization, r"Get-ChildItem\s+-Recurse")
        merge = next(step["run"] for step in mac if step.get("name") == "merge Windows binaries into the bundle")
        self.assertIn('SRC="winbuild/${PRODUCT}.4DX"', merge)
        self.assertNotRegex(merge, r"\bfind\b")

    def test_original_secrets_environment_and_native_setup_are_preserved(self):
        self.assertEqual(self.build["env"], self.original["env"])
        def secret_names(workflow):
            return set(re.findall(r"\$\{\{\s*secrets\.([A-Z_]+)\s*\}\}", yaml.safe_dump(workflow)))
        self.assertEqual(secret_names(self.build), secret_names(self.original))
        self.assertEqual(secret_names(self.build), {
            "APPLE_DEVELOPER_ID_CERTIFICATE", "APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD",
            "KEYCHAIN_PASSWORD", "NOTARYTOOL_APPLE_ID", "NOTARYTOOL_TEAM_ID", "NOTARYTOOL_PASSWORD",
        })
        current = self.build["jobs"]["build-macos"]["steps"]
        original = self.original["jobs"]["build-macos"]["steps"]
        for name in ("import Developer ID certificate", "store notarytool credentials"):
            self.assertEqual(next(step for step in current if step.get("name") == name),
                             next(step for step in original if step.get("name") == name))
        setup = lambda value: [step for step in value["jobs"]["build-windows"]["steps"]
                               if step.get("uses", "").startswith("microsoft/setup-msbuild@")]
        self.assertEqual(setup(self.build), setup(self.original))

    def test_source_localization_is_wired_into_actual_xcode_resources_phase(self):
        project = self.project
        reference = re.search(
            r"(\w+)\s*/\* English \*/ = \{isa = PBXFileReference;[^\n]*"
            r"path = English\.lproj/InfoPlist\.strings;", project,
        )
        self.assertIsNotNone(reference)
        variant = re.search(
            r"(\w+)\s*/\* InfoPlist\.strings \*/ = \{\s*isa = PBXVariantGroup;\s*"
            r"children = \(\s*" + reference[1] + r"\s*/\* English \*/", project,
        )
        self.assertIsNotNone(variant)
        buildfile = re.search(
            r"(\w+)\s*/\* InfoPlist\.strings in Resources \*/ = "
            r"\{isa = PBXBuildFile; fileRef = " + variant[1], project,
        )
        self.assertIsNotNone(buildfile)
        phase = re.search(
            r"(\w+)\s*/\* Resources \*/ = \{\s*isa = PBXResourcesBuildPhase;"
            r"[\s\S]*?files = \(([\s\S]*?)\);", project,
        )
        self.assertIsNotNone(phase)
        self.assertIn(buildfile[1] + " /* InfoPlist.strings in Resources */", phase[2])
        target = re.search(r"isa = PBXNativeTarget;[\s\S]*?buildPhases = \(([\s\S]*?)\);", project)
        self.assertIsNotNone(target)
        self.assertIn(phase[1] + " /* Resources */", target[1])
        self.assertIn("Cocoa.framework in Frameworks", project)
        self.assertIn('"$(PROJECT_DIR)/lib"', project)
        self.assertIn('"$(PROJECT_DIR)/a"', project)
        self.assertIn("INFOPLIST_FILE = Info.plist;", project)

    def test_preflight_before_native_final_check_after_staple_before_publish(self):
        jobs = self.build["jobs"]
        preflights = [step for step in jobs["version"]["steps"] if "preflight" in step.get("run", "")]
        self.assertEqual(len(preflights), 1)
        self.assertEqual(preflights[0]["env"]["SELECTED_SHA"], "${{ github.sha }}")
        self.assertIn('release.py preflight --sha "$SELECTED_SHA"', preflights[0]["run"])
        self.assertEqual(jobs["version"]["outputs"], {
            "tag": "${{ steps.set.outputs.tag }}", "version": "${{ steps.set.outputs.version }}",
        })
        steps = jobs["build-macos"]["steps"]
        names = [step.get("name") for step in steps]
        self.assertLess(names.index("verify universal binary"), names.index("codesign"))
        self.assertLess(names.index("codesign"), names.index("package, notarize & staple"))
        self.assertLess(names.index("package, notarize & staple"), names.index("create GitHub Release"))
        publication = next(step for step in steps if step.get("name") == "create GitHub Release")
        self.assertEqual(publication["env"]["SELECTED_SHA"], "${{ github.sha }}")
        script = publication["run"]
        self.assertLess(script.index("release.py check"), script.index("release.py publish"))
        self.assertIn('release.py check --sha "$SELECTED_SHA"', script)
        self.assertIn('release.py publish --sha "$SELECTED_SHA" "$ZIP_PATH" "$DMG_PATH"', script)

    def test_original_release_notes_semantics_are_empty_not_generated(self):
        original = self.original["jobs"]["build-macos"]["steps"][-1]
        self.assertEqual(original["uses"], "softprops/action-gh-release@v2")
        self.assertEqual(set(original["with"]), {"tag_name", "name", "files"})
        helper = (ROOT / ".github/scripts/release.py").read_text()
        tree = ast.parse(helper)
        release_payloads = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = [key.value if isinstance(key, ast.Constant) else None for key in node.keys]
                if "tag_name" in keys and "target_commitish" in keys:
                    release_payloads.append(dict(zip(keys, node.values)))
        self.assertEqual(len(release_payloads), 1)
        payload = release_payloads[0]
        required = {"tag_name", "target_commitish", "name", "draft", "prerelease", "body"}
        self.assertTrue(required <= set(payload))
        self.assertFalse(set(payload) - required - {"generate_release_notes"})
        self.assertEqual(ast.literal_eval(payload["body"]), "")
        self.assertIs(ast.literal_eval(payload["draft"]), False)
        self.assertIs(ast.literal_eval(payload["prerelease"]), False)
        if "generate_release_notes" in payload:
            self.assertIs(ast.literal_eval(payload["generate_release_notes"]), False)
        for text in (helper, yaml.safe_dump(self.build)):
            self.assertNotIn("--generate-notes", text)


if __name__ == "__main__":
    unittest.main()
