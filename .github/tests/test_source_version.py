"""Source-only, byte-preserving version tests; fixtures never leave this worktree."""

import importlib.util
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location(
    "tested_source_version", ROOT / ".github/scripts/source_version.py"
)
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".github/tests" / (".source-fixture-" + uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.copy_source()
        self.current = source.read(self.root)
        major, minor, patch_version = map(int, self.current.split("."))
        self.drift = f"{major + 7}.{minor}.{patch_version}"

    def copy_source(self):
        for name in source.SOURCE_PATHS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)

    def snapshot(self):
        return {
            name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
            for name in source.SOURCE_PATHS if (path := self.root / name).exists()
        }

    def plist(self, **values):
        path = self.root / source.PLIST
        data = plistlib.loads(path.read_bytes())
        data.update(values)
        path.write_bytes(plistlib.dumps(data))

    def project_value(self, key, value, count=None):
        path = self.root / source.PROJECT
        text = source.text(path)
        fields = source.project_settings(text)[key]
        for field in reversed(fields if count is None else fields[:count]):
            text = text[:field.start] + value + text[field.end:]
        path.write_text(text)

    def assert_rejected(self, error=ValueError):
        before = self.snapshot()
        with patch.object(Path, "write_bytes") as write:
            with self.assertRaises(error):
                source.read(self.root)
            with self.assertRaises(error):
                source.bump("patch", self.root)
            write.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def next_version(self, mode):
        parts = list(map(int, self.current.split(".")))
        index = ("major", "minor", "patch").index(mode)
        parts[index] += 1
        parts[index + 1:] = [0] * (2 - index)
        return ".".join(map(str, parts))

    def test_actual_coordinated_contract_and_optional_build_absence(self):
        self.assertEqual(tuple(map(str, source.SOURCE_PATHS)), (
            "GET TEXT INPUT.xcodeproj/project.pbxproj", "Info.plist",
            "English.lproj/InfoPlist.strings",
        ))
        self.assertEqual(source.SETTING_COUNT, 6)
        self.assertEqual(source.KEYS, ("MARKETING_VERSION", "CURRENT_PROJECT_VERSION"))
        data = plistlib.loads((self.root / source.PLIST).read_bytes())
        self.assertEqual(data["CFBundleShortVersionString"], "$(MARKETING_VERSION)")
        self.assertNotIn("CFBundleVersion", data)
        localized = (self.root / source.LOCALIZED).read_bytes()
        self.assertIn(b"\r", localized)
        self.assertNotIn(b"\n", localized)
        self.assertFalse(localized.startswith(b"\xef\xbb\xbf"))

    def test_all_bump_modes_change_only_version_bytes_and_preserve_modes(self):
        for mode in ("patch", "minor", "major"):
            with self.subTest(mode=mode):
                self.copy_source()
                before = self.snapshot()
                expected = self.next_version(mode)
                self.assertEqual(source.bump(mode, self.root), expected)
                self.assertEqual(source.read(self.root), expected)
                for name, (data, permissions) in before.items():
                    path = self.root / name
                    self.assertEqual(path.read_bytes(), data.replace(
                        self.current.encode(), expected.encode()))
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), permissions)
                self.assertNotIn("CFBundleVersion",
                                 plistlib.loads((self.root / source.PLIST).read_bytes()))

    def test_supported_references_preserved_and_optional_build_literals_co_bump(self):
        for reference in source.REFERENCES:
            for marketing in ("$(MARKETING_VERSION)", "${MARKETING_VERSION}"):
                with self.subTest(reference=reference, marketing=marketing):
                    self.copy_source()
                    self.project_value("CURRENT_PROJECT_VERSION", f'"{marketing}"')
                    self.plist(CFBundleShortVersionString=reference, CFBundleVersion=reference)
                    before = self.snapshot()
                    new = source.bump("patch", self.root)
                    self.assertEqual(source.read(self.root), new)
                    for name, (data, _) in before.items():
                        self.assertEqual((self.root / name).read_bytes(),
                                         data.replace(self.current.encode(), new.encode()))
        self.copy_source()
        self.plist(CFBundleShortVersionString=self.current, CFBundleVersion=self.current)
        new = source.bump("minor", self.root)
        data = plistlib.loads((self.root / source.PLIST).read_bytes())
        self.assertEqual(data["CFBundleShortVersionString"], new)
        self.assertEqual(data["CFBundleVersion"], new)

    def test_invalid_missing_and_drifted_project_settings_reject_without_writes(self):
        invalid = (
            "", "1.2", "14.3", "77", "v1.2.3", "01.2.3", "1.2.3-beta", self.drift,
            "$(UNKNOWN)", "${UNKNOWN}",
        )
        for key in source.KEYS:
            for value in invalid:
                for count in (1, source.SETTING_COUNT):
                    with self.subTest(key=key, value=value, count=count):
                        self.copy_source()
                        self.project_value(key, value, count)
                        self.assert_rejected()
            self.copy_source()
            path = self.root / source.PROJECT
            path.write_text(source.text(path).replace(f"{key} = {self.current};", "", 1))
            self.assert_rejected()
        for key in source.KEYS:
            for reference in (f"$({key})", f"${{{key}}}"):
                self.copy_source()
                self.project_value(key, f'"{reference}"')
                self.assert_rejected()

    def test_known_marketing_to_current_references_resolve_and_are_preserved(self):
        for reference in ("$(CURRENT_PROJECT_VERSION)", "${CURRENT_PROJECT_VERSION}"):
            for quoted in (False, True):
                with self.subTest(reference=reference, quoted=quoted):
                    self.copy_source()
                    self.project_value("MARKETING_VERSION",
                                       f'"{reference}"' if quoted else reference)
                    self.plist(CFBundleShortVersionString=reference, CFBundleVersion=reference)
                    before = self.snapshot()
                    self.assertEqual(source.read(self.root), self.current)
                    new = source.bump("minor", self.root)
                    self.assertEqual(source.read(self.root), new)
                    for name, (data, permissions) in before.items():
                        path = self.root / name
                        self.assertEqual(path.read_bytes(),
                                         data.replace(self.current.encode(), new.encode()))
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), permissions)

    def test_cross_key_cycles_and_reference_backed_drift_reject_without_writes(self):
        for marketing in ("$(CURRENT_PROJECT_VERSION)", "${CURRENT_PROJECT_VERSION}"):
            for current in ("$(MARKETING_VERSION)", "${MARKETING_VERSION}"):
                self.copy_source()
                self.project_value("MARKETING_VERSION", f'"{marketing}"')
                self.project_value("CURRENT_PROJECT_VERSION", f'"{current}"')
                self.assert_rejected()
        self.copy_source()
        self.project_value("MARKETING_VERSION", '"$(CURRENT_PROJECT_VERSION)"')
        self.project_value("CURRENT_PROJECT_VERSION", self.drift, 1)
        self.assert_rejected()

    def test_each_configuration_can_use_either_acyclic_reference_direction(self):
        path = self.root / source.PROJECT
        project = " ".join(
            (f'MARKETING_VERSION = "$(CURRENT_PROJECT_VERSION)"; '
             f'CURRENT_PROJECT_VERSION = {self.current};') if index % 2 else
            (f'MARKETING_VERSION = {self.current}; '
             'CURRENT_PROJECT_VERSION = "${MARKETING_VERSION}";')
            for index in range(source.SETTING_COUNT)
        )
        path.write_text(project)
        new = source.bump("patch", self.root)
        self.assertEqual(path.read_bytes(), project.replace(self.current, new).encode())
        self.assertEqual(source.read(self.root), new)

    def test_inline_quoted_and_unquoted_project_duplicates_reject_before_writes(self):
        path = self.root / source.PROJECT
        original = source.text(path)
        for key in source.KEYS:
            for quoted in (False, True):
                for value in (self.current, f'"{self.current}"', self.drift,
                              f'"{self.drift}"', "$(UNKNOWN)", '"$(UNKNOWN)"'):
                    for location in ("after-version", "after-other"):
                        with self.subTest(key=key, quoted=quoted, value=value, location=location):
                            name = f'"{key}"' if quoted else key
                            duplicate = f"{name} = {value};"
                            if location == "after-version":
                                existing = f"{key} = {self.current};"
                                mutated = original.replace(existing, existing + " " + duplicate, 1)
                            else:
                                mutated = original + '\nOTHER = "keep"; ' + duplicate + "\n"
                            path.write_text(mutated)
                            self.assert_rejected()

    def test_assignments_anywhere_with_quoted_keys_and_comments_keep_exact_spans(self):
        path = self.root / source.PROJECT
        project = 'OTHER = "untouched"; ' + " ".join(
            f'"{key}" /* before equals */ = /* before value */ "{self.current}" /* after */;'
            for _ in range(source.SETTING_COUNT) for key in source.KEYS
        )
        path.write_text(project)
        new = source.bump("patch", self.root)
        self.assertEqual(path.read_bytes(), project.replace(self.current, new).encode())
        self.assertEqual(source.read(self.root), new)

    def test_unquoted_project_references_and_whitespace_are_preserved(self):
        for reference in ("$(MARKETING_VERSION)", "${MARKETING_VERSION}"):
            self.copy_source()
            path = self.root / source.PROJECT
            project = " ".join(
                f'\tMARKETING_VERSION \t= \t"{self.current}" \t; '
                f'CURRENT_PROJECT_VERSION \t= \t{reference} \t;'
                for _ in range(source.SETTING_COUNT)
            )
            path.write_text(project)
            new = source.bump("minor", self.root)
            self.assertEqual(path.read_bytes(), project.replace(self.current, new).encode())
            self.assertEqual(source.read(self.root), new)

    def test_comment_and_string_assignment_noise_is_not_read_or_rewritten(self):
        project = self.root / source.PROJECT
        localized = self.root / source.LOCALIZED
        project_noise = (
            f'\n/* historical {self.current}\nMARKETING_VERSION = {self.drift};\n'
            f'"CURRENT_PROJECT_VERSION" = {self.drift};\n*/\n'
            f'// MARKETING_VERSION = {self.drift}; CURRENT_PROJECT_VERSION = {self.drift};\n'
            f'OTHER = "MARKETING_VERSION = {self.current}; '
            f'\\"CURRENT_PROJECT_VERSION\\" = {self.drift};";\n'
            'MARKETING_VERSION_SUFFIX = unchanged; PREFIX_CURRENT_PROJECT_VERSION = unchanged;\n'
        ).encode()
        localized_noise = (
            f'\n/* historical {self.current}\nCFBundleShortVersionString = "{self.drift}";\n'
            f'CFBundleGetInfoString = "not live";\n*/\n'
            f'// CFBundleShortVersionString = "{self.drift}";\n'
            f'Other = "CFBundleShortVersionString = \\"{self.current}\\";";\n'
        ).encode()
        before = self.snapshot()
        project.write_bytes(project.read_bytes() + project_noise)
        localized.write_bytes(localized.read_bytes() + localized_noise)
        new = source.bump("patch", self.root)
        for name, noise in ((source.PROJECT, project_noise), (source.LOCALIZED, localized_noise)):
            self.assertEqual((self.root / name).read_bytes(),
                             before[name][0].replace(self.current.encode(), new.encode()) + noise)

    def test_malformed_project_comments_strings_and_assignments_are_zero_write(self):
        original = source.text(self.root / source.PROJECT)
        for extra in (
            "\n/* unterminated", '\nOTHER = "unterminated',
            f"\nMARKETING_VERSION = {self.current}\n",
            f'\n"CURRENT_PROJECT_VERSION" = "{self.current}" nonsense;\n',
        ):
            with self.subTest(extra=extra):
                (self.root / source.PROJECT).write_text(original + extra)
                self.assert_rejected()

    def test_required_short_missing_alone_or_with_build_rejects_read_and_bump_zero_write(self):
        path = self.root / source.PLIST
        original = plistlib.loads(path.read_bytes())
        for build_present in (True, False):
            with self.subTest(build_present=build_present):
                data = dict(original)
                data.pop("CFBundleShortVersionString")
                if build_present:
                    data["CFBundleVersion"] = self.current
                else:
                    data.pop("CFBundleVersion", None)
                path.write_bytes(plistlib.dumps(data))
                self.assert_rejected()

    def test_invalid_plist_versions_and_roots_are_zero_write(self):
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            for value in ("", "14.3", "77", "01.2.3", "v1.2.3", "1.2.3-beta",
                          "$(UNKNOWN)", "${UNKNOWN}", self.drift, 123, True, [], {}):
                with self.subTest(key=key, value=value):
                    self.copy_source()
                    self.plist(**{key: value})
                    self.assert_rejected()
        for data in ([], "not a dictionary", 123):
            (self.root / source.PLIST).write_bytes(plistlib.dumps(data))
            self.assert_rejected()

    def test_duplicate_plist_keys_entities_and_binary_sources_are_zero_write(self):
        path = self.root / source.PLIST
        for key in ("CFBundleShortVersionString", "CFBundleVersion", "CFBundleName"):
            for value in (self.current, self.drift):
                self.copy_source()
                self.plist(**{key: self.current})
                path.write_text(source.text(path).replace(
                    "</dict>", f"<key>{key}</key><string>{value}</string></dict>"))
                self.assert_rejected()
        self.copy_source()
        original = path.read_bytes()
        for data in (
            b"not a plist", b"", original.replace(b"</dict>", b""),
            original.replace(b"$(MARKETING_VERSION)", b"&#36;(MARKETING_VERSION)"),
            plistlib.dumps(plistlib.loads(original), fmt=plistlib.FMT_BINARY),
        ):
            path.write_bytes(data)
            self.assert_rejected()

    def test_plist_comment_noise_and_unrelated_versions_stay_unchanged(self):
        self.plist(CFBundleShortVersionString=self.current, UnrelatedVersion=self.current)
        path = self.root / source.PLIST
        noise = f"<!-- <key>CFBundleVersion</key><string>{self.drift}</string> -->\n"
        original = source.text(path).replace("<dict>", "<dict>\n" + noise)
        path.write_text(original)
        new = source.bump("minor", self.root)
        expected = original.replace(
            f"<key>CFBundleShortVersionString</key>\n\t<string>{self.current}</string>",
            f"<key>CFBundleShortVersionString</key>\n\t<string>{new}</string>",
        )
        self.assertEqual(path.read_bytes(), expected.encode())
        self.assertEqual(plistlib.loads(path.read_bytes())["UnrelatedVersion"], self.current)

    def test_quoted_localized_keys_inline_layout_and_comments_are_supported(self):
        path = self.root / source.LOCALIZED
        original = (
            f'OTHER = "keep"; "CFBundleShortVersionString" /* comment */ = "{self.current}"; '
            f'"CFBundleGetInfoString" = "GET TEXT INPUT version {self.current}, '
            'Copyright 2024 miyako.";'
        )
        path.write_text(original)
        new = source.bump("patch", self.root)
        self.assertEqual(path.read_bytes(), original.replace(self.current, new).encode())

    def test_localized_inline_and_quoted_duplicates_reject_read_and_bump_zero_write(self):
        path = self.root / source.LOCALIZED
        original = source.text(path)
        for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
            for quoted in (False, True):
                for value in (self.current, self.drift):
                    for inline in (False, True):
                        with self.subTest(key=key, quoted=quoted, value=value, inline=inline):
                            name = f'"{key}"' if quoted else key
                            prefix = 'OTHER = "keep"; ' if inline else ""
                            path.write_text(original + f'\n{prefix}{name} = "{value}";\n')
                            self.assert_rejected()

    def test_localized_invalid_missing_and_malformed_values_are_zero_write(self):
        path = self.root / source.LOCALIZED
        original = source.text(path)
        for value in ("", "1.0", "01.2.3", self.drift, "$(MARKETING_VERSION)", "$(UNKNOWN)"):
            path.write_text(original.replace(self.current, value))
            self.assert_rejected()
        for key in ("CFBundleShortVersionString", "CFBundleGetInfoString"):
            path.write_text(original.replace(key, "UNKNOWN"))
            self.assert_rejected()
        for old, new in (("GET TEXT INPUT version", "Other version"),
                         ("Copyright 2024 miyako.", "Copyright 2024 someone.")):
            path.write_text(original.replace(old, new))
            self.assert_rejected()
        for extra in ('\nOTHER = "unterminated', "\n/* unterminated", '\nOTHER = "x"'):
            path.write_text(original + extra)
            self.assert_rejected()

    def test_all_source_encodings_endings_modes_and_unrelated_unicode_bytes(self):
        encodings = (
            (b"", "utf-8"), (b"\xef\xbb\xbf", "utf-8"),
            (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
        )
        for prefix, encoding in encodings:
            for newline in ("\n", "\r", "\r\n", "mixed"):
                with self.subTest(encoding=encoding, prefix=prefix, newline=newline):
                    self.copy_source()
                    before = {}
                    for index, name in enumerate(source.SOURCE_PATHS):
                        path = self.root / name
                        value = source.text(path)
                        if name == source.PLIST:
                            value = value.replace("</dict>", (
                                "<key>Unrelated</key><string>日本語 café</string>\n</dict>"
                            ))
                            if encoding.startswith("utf-16"):
                                value = value.replace('encoding="UTF-8"', 'encoding="UTF-16"')
                        elif name == source.PROJECT:
                            value += '\nOTHER = "日本語 café";\n'
                        else:
                            value += '\nOTHER = "日本語 café";\n'
                        value = value.rstrip("\n")
                        parts = value.split("\n")
                        value = "".join(
                            line + (("\r", "\r\n", "\n")[i % 3] if newline == "mixed"
                                    else newline) if i < len(parts) - 1 else line
                            for i, line in enumerate(parts)
                        )
                        path.write_bytes(prefix + value.encode(encoding))
                        path.chmod((0o755, 0o640, 0o644)[index])
                        before[name] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                    new = source.bump("minor", self.root)
                    self.assertEqual(source.read(self.root), new)
                    for name, (data, permissions) in before.items():
                        path = self.root / name
                        self.assertEqual(path.read_bytes(), data.replace(
                            self.current.encode(encoding), new.encode(encoding)))
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), permissions)

    def test_missing_source_files_and_unsupported_bytes_never_partially_write(self):
        for name in source.SOURCE_PATHS:
            self.copy_source()
            (self.root / name).unlink()
            self.assert_rejected(FileNotFoundError)
        for name in source.SOURCE_PATHS:
            self.copy_source()
            path = self.root / name
            path.write_bytes(path.read_bytes() + b"\xff")
            self.assert_rejected(UnicodeError)

    def test_render_failure_never_writes_earlier_outputs(self):
        before = self.snapshot()
        render = source._render
        calls = 0

        def fail_last(*args):
            nonlocal calls
            calls += 1
            if calls == len(source.SOURCE_PATHS):
                raise ValueError("fixture render failure")
            return render(*args)

        with patch.object(source, "_render", side_effect=fail_last), \
                patch.object(Path, "write_bytes") as write:
            with self.assertRaisesRegex(ValueError, "fixture render failure"):
                source.bump("patch", self.root)
            write.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_actual_tracked_prebuilt_4d_and_dependency_paths_are_ignored(self):
        names = (
            "test/Plugins/GET TEXT INPUT.bundle/Contents/Info.plist",
            "test/Plugins/GET TEXT INPUT.bundle/Contents/MacOS/GET TEXT INPUT",
            "test/Plugins/GET TEXT INPUT.bundle/Contents/QuickLook/Thumbnail.png",
            "test/Plugins/GET TEXT INPUT.bundle/Contents/Resources/English.lproj/InfoPlist.strings",
            "test/Plugins/GET TEXT INPUT.bundle/Contents/Resources/manifest.json",
            "test/Plugins/GET TEXT INPUT.bundle/Contents/Windows/GET TEXT INPUT.4DX",
            "test/Plugins/GET TEXT INPUT.bundle/Contents/Windows64/GET TEXT INPUT.4DX",
            "test/Project/Sources/catalog.4DCatalog", "manifest.json", "constants.xlf",
            "constants.json", "include/json/version.h", "a/libjsoncpp.a",
            "lib/windows/jsoncpp.lib", "lib/windows64/jsoncpp.lib",
            "4D Plugin API/4DPluginAPI.h", "4DPlugin-GET-TEXT-INPUT.h",
            "GET TEXT INPUT.vcxproj", "GET TEXT INPUT.vcxproj.filters",
            "GET TEXT INPUT.sln",
        )
        tracked = subprocess.check_output(["git", "-C", str(ROOT), "ls-files"], text=True).splitlines()
        self.assertTrue(set(names).issubset(tracked))
        before = {}
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, path)
            before[name] = (path.read_bytes(), path.stat().st_mode)
        source.bump("major", self.root)
        self.assertEqual(before, {
            name: ((self.root / name).read_bytes(), (self.root / name).stat().st_mode)
            for name in names
        })

    def test_invalid_mode_cli_read_and_cli_bump(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            source.bump("other", self.root)
        helper = str(ROOT / ".github/scripts/source_version.py")
        result = subprocess.run([sys.executable, helper], cwd=self.root, check=True,
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), self.current)
        self.assertEqual(self.snapshot(), before)
        result = subprocess.run([sys.executable, helper, "--bump", "patch"], cwd=self.root,
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), self.next_version("patch"))
        self.assertEqual(source.read(self.root), self.next_version("patch"))

    def bundle(self):
        contents = self.root / "built.bundle/Contents"
        localization = contents / "Resources/English.lproj/InfoPlist.strings"
        localization.parent.mkdir(parents=True, exist_ok=True)
        data = plistlib.loads((self.root / source.PLIST).read_bytes())
        for key in ("CFBundleShortVersionString", "CFBundleVersion"):
            if key in data:
                data[key] = self.current
        (contents / "Info.plist").write_bytes(plistlib.dumps(data))
        localization.write_bytes((self.root / source.LOCALIZED).read_bytes())
        return contents

    def test_built_plist_xml_binary_and_utf16_are_verified(self):
        contents = self.bundle()
        path = contents / "Info.plist"
        original = plistlib.loads(path.read_bytes())
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            path.write_bytes(plistlib.dumps(original, fmt=fmt))
            source.verify_bundle(contents.parent, self.root)
        xml = plistlib.dumps(original).decode().replace('encoding="UTF-8"', 'encoding="UTF-16"')
        for prefix, encoding in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
            path.write_bytes(prefix + xml.encode(encoding))
            source.verify_bundle(contents.parent, self.root)

    def test_built_localized_text_encodings_and_compiled_plists_are_verified(self):
        contents = self.bundle()
        path = contents / "Resources/English.lproj/InfoPlist.strings"
        value = source.text(self.root / source.LOCALIZED)
        for prefix, encoding in (
            (b"", "utf-8"), (b"\xef\xbb\xbf", "utf-8"),
            (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
        ):
            for newline in ("\n", "\r", "\r\n"):
                path.write_bytes(prefix + value.replace("\n", newline).encode(encoding))
                source.verify_bundle(contents.parent, self.root)
                path.write_bytes(prefix + value.replace(
                    self.current, self.drift).replace("\n", newline).encode(encoding))
                with self.assertRaises(ValueError):
                    source.verify_bundle(contents.parent, self.root)
        fields = {
            "CFBundleShortVersionString": self.current,
            "CFBundleGetInfoString": source.INFO_PREFIX + self.current + source.INFO_SUFFIX,
        }
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            path.write_bytes(plistlib.dumps(fields, fmt=fmt))
            source.verify_bundle(contents.parent, self.root)
            for key in fields:
                path.write_bytes(plistlib.dumps({**fields, key: self.drift}, fmt=fmt))
                with self.assertRaises(ValueError):
                    source.verify_bundle(contents.parent, self.root)
        xml = plistlib.dumps(fields).decode().replace('encoding="UTF-8"', 'encoding="UTF-16"')
        for prefix, encoding in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
            path.write_bytes(prefix + xml.encode(encoding))
            source.verify_bundle(contents.parent, self.root)

    def test_built_required_display_optional_build_and_wrong_roots_fail_closed(self):
        contents = self.bundle()
        path = contents / "Info.plist"
        for value in (
            [], {}, {"CFBundleShortVersionString": self.drift},
            {"CFBundleShortVersionString": "$(MARKETING_VERSION)"},
            {"CFBundleShortVersionString": self.current, "CFBundleVersion": self.drift},
        ):
            path.write_bytes(plistlib.dumps(value))
            with self.assertRaises(ValueError):
                source.verify_bundle(contents.parent, self.root)
        self.plist(CFBundleVersion=self.current)
        path.write_bytes(plistlib.dumps({"CFBundleShortVersionString": self.current}))
        with self.assertRaisesRegex(ValueError, "build version"):
            source.verify_bundle(contents.parent, self.root)
        path.write_bytes(plistlib.dumps({
            "CFBundleShortVersionString": self.current, "CFBundleVersion": self.current,
        }))
        source.verify_bundle(contents.parent, self.root)

    def test_built_missing_files_duplicate_keys_and_invalid_localization_fail_closed(self):
        contents = self.bundle()
        path = contents / "Info.plist"
        original = path.read_bytes()
        duplicate = f"<key>CFBundleShortVersionString</key><string>{self.current}</string>"
        path.write_bytes(original.replace(b"</dict>", duplicate.encode() + b"</dict>"))
        with self.assertRaises(ValueError):
            source.verify_bundle(contents.parent, self.root)
        path.write_bytes(original)
        localized = contents / "Resources/English.lproj/InfoPlist.strings"
        for data in (b"not strings", plistlib.dumps([]), plistlib.dumps({})):
            localized.write_bytes(data)
            with self.assertRaises(ValueError):
                source.verify_bundle(contents.parent, self.root)
        duplicate = (
            f'<key>CFBundleShortVersionString</key><string>{self.current}</string>'
        ).encode()
        fields = {
            "CFBundleShortVersionString": self.current,
            "CFBundleGetInfoString": source.INFO_PREFIX + self.current + source.INFO_SUFFIX,
        }
        localized.write_bytes(plistlib.dumps(fields).replace(
            b"</dict>", duplicate + b"</dict>"))
        with self.assertRaises(ValueError):
            source.verify_bundle(contents.parent, self.root)
        localized.unlink()
        with self.assertRaises(FileNotFoundError):
            source.verify_bundle(contents.parent, self.root)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            source.verify_bundle(contents.parent, self.root)


if __name__ == "__main__":
    unittest.main()
