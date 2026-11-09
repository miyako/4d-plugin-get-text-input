"""Real Git transport tests; all remotes are disposable local bare fixtures."""

import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".github/scripts"))
import release
import source_version as source


@contextlib.contextmanager
def cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class BumpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bump-integration-")
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.root = self.area / "checkout"
        self.root.mkdir()
        self.origin = self.area / "origin.git"
        for path in source.SOURCE_PATHS:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)
        shutil.copytree(ROOT / ".github/scripts", self.root / ".github/scripts")
        self.hook = self.root / ".github/scripts/bump-hooks/pre-push"
        self.git("init", "-q", "-b", "selected")
        self.git("config", "user.name", "Offline fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "root")
        self.ancestor = self.git("rev-parse", "HEAD")
        (self.root / "unchanged").write_text("preserved")
        self.git("add", "unchanged")
        self.git("commit", "-qm", "selected")
        self.sha = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        subprocess.run(["git", "init", "--bare", "-q", str(self.origin)], check=True)
        self.git("remote", "add", "origin", str(self.origin))
        self.git("-c", "push.followTags=false", "push", "--no-follow-tags",
                 "-q", "origin", "HEAD:refs/heads/selected")
        self.git("config", "push.followTags", "true")
        self.git("tag", "-a", "offline-annotated-tag", "-m", "must never be pushed")
        self.current = source.read(self.root)

    def git(self, *args, input=None):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], input=input, text=True,
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.strip()

    def bare(self, *args):
        return subprocess.run(
            ["git", "--git-dir", str(self.origin), *args], check=True,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.strip()

    def invoke(self, mode="patch"):
        with cwd(self.root), patch.object(release, "__file__",
                                         str(self.root / ".github/scripts/release.py")):
            release.bump_branch(self.sha, "selected", mode)

    def assert_no_bump_push(self):
        self.assertEqual(self.bare("rev-parse", "refs/heads/selected"), self.sha)
        self.assertEqual(self.bare("tag", "--list"), "")

    def test_real_bump_is_clean_direct_child_source_only_with_trailer_and_no_tags(self):
        real_run = release.run
        commands = []

        def capture(*args, **kwargs):
            commands.append(args)
            return real_run(*args, **kwargs)

        with patch.object(release, "run", side_effect=capture):
            self.invoke()
        new = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", new), f"{new} {self.sha}")
        self.assertEqual(self.bare("rev-parse", "refs/heads/selected"), new)
        changed = set(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", new).splitlines())
        self.assertTrue(changed)
        self.assertTrue(changed.issubset(set(map(str, source.SOURCE_PATHS))))
        self.assertIn(release.TRAILER, self.git("show", "-s", "--format=%B", new).splitlines())
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.bare("tag", "--list"), "")
        self.assertNotEqual(source.read(self.root), self.current)
        pushes = [command for command in commands if "push" in command]
        self.assertEqual(len(pushes), 1)
        self.assertIn("push.followTags=false", pushes[0])
        self.assertIn("--no-follow-tags", pushes[0])
        self.assertEqual(pushes[0][-1], f"{new}:refs/heads/selected")
        self.assertFalse(any("force" in arg or arg.startswith("+") for arg in pushes[0]))

    def test_missing_and_nonexecutable_guard_rejected_initially(self):
        original = self.hook.read_bytes()
        for mutation in ("missing", "not-executable"):
            with self.subTest(mutation=mutation):
                self.hook.write_bytes(original)
                self.hook.chmod(0o755)
                if mutation == "missing":
                    self.hook.unlink()
                else:
                    self.hook.chmod(0o644)
                with self.assertRaisesRegex(ValueError, "guard"):
                    self.invoke()
                self.assertEqual(self.git("rev-parse", "HEAD"), self.sha)
                self.assert_no_bump_push()

    def test_missing_and_nonexecutable_guard_rejected_late(self):
        for mutation in ("missing", "not-executable"):
            with self.subTest(mutation=mutation):
                fixture = BumpIntegrationTests("test_real_bump_is_clean_direct_child_source_only_with_trailer_and_no_tags")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                original_run = release.run

                def mutate(*args, **kwargs):
                    result = original_run(*args, **kwargs)
                    if args[:2] == ("git", "show"):
                        if mutation == "missing":
                            fixture.hook.unlink()
                        else:
                            fixture.hook.chmod(0o644)
                    return result

                with patch.object(release, "run", side_effect=mutate):
                    with self.assertRaisesRegex(ValueError, "guard"):
                        fixture.invoke()
                fixture.assert_no_bump_push()

    def test_selected_branch_moved_before_start_rejected(self):
        self.bare("update-ref", "refs/heads/selected", self.ancestor)
        with self.assertRaisesRegex(ValueError, "moved since dispatch"):
            self.invoke()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.sha)

    def test_real_advertisement_races_after_final_read(self):
        for race in ("advance", "rewind", "delete", "same-result-zero-updates"):
            with self.subTest(race=race):
                fixture = BumpIntegrationTests("test_real_bump_is_clean_direct_child_source_only_with_trailer_and_no_tags")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                original = release.run
                raced = False

                def run(*args, **kwargs):
                    nonlocal raced
                    if "push" in args:
                        raced = True
                        new = fixture.git("rev-parse", "HEAD")
                        if race == "advance":
                            advanced = fixture.git("commit-tree", fixture.tree, "-p", fixture.sha,
                                                   input="concurrent source commit\n")
                            fixture.bare("fetch", "--no-tags", str(fixture.root), advanced)
                            target = advanced
                        elif race == "rewind":
                            target = fixture.ancestor
                        elif race == "delete":
                            fixture.bare("update-ref", "-d", "refs/heads/selected")
                            target = None
                        else:
                            fixture.bare("fetch", "--no-tags", str(fixture.root), new)
                            target = new
                        if target:
                            fixture.bare("update-ref", "refs/heads/selected", target)
                    return original(*args, **kwargs)

                with patch.object(release, "run", side_effect=run):
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        fixture.invoke()
                self.assertTrue(raced)
                self.assertEqual(fixture.bare("tag", "--list"), "")
                if race == "rewind":
                    self.assertEqual(fixture.bare("rev-parse", "refs/heads/selected"), fixture.ancestor)
                elif race == "delete":
                    self.assertEqual(fixture.git("ls-remote", "--refs", "origin",
                                                 "refs/heads/selected"), "")
                elif race == "advance":
                    self.assertNotEqual(fixture.bare("rev-parse", "refs/heads/selected"),
                                        fixture.git("rev-parse", "HEAD"))

    def test_empty_nonsource_multiparent_and_missing_trailer_commits_rejected(self):
        for mutation in ("empty", "non-source", "multiparent", "missing-trailer"):
            with self.subTest(mutation=mutation):
                fixture = BumpIntegrationTests("test_real_bump_is_clean_direct_child_source_only_with_trailer_and_no_tags")
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                original = release.run

                def run(*args, **kwargs):
                    if args[:2] == ("git", "commit"):
                        if mutation == "empty":
                            fixture.git("restore", "--source", fixture.sha, "--staged",
                                        "--worktree", "--", *map(str, source.SOURCE_PATHS))
                            return original(*args, "--allow-empty", **kwargs)
                        if mutation == "non-source":
                            (fixture.root / "unexpected").write_text("must not push")
                            fixture.git("add", "unexpected")
                        if mutation == "missing-trailer":
                            return original("git", "commit", "-m", "no coauthor", **kwargs)
                        result = original(*args, **kwargs)
                        if mutation == "multiparent":
                            tree = fixture.git("rev-parse", "HEAD^{tree}")
                            merge = fixture.git("commit-tree", tree, "-p", fixture.sha,
                                                "-p", fixture.ancestor,
                                                input=f"fake merge\n\n{release.TRAILER}\n")
                            fixture.git("update-ref", "HEAD", merge)
                        return result
                    return original(*args, **kwargs)

                with patch.object(release, "run", side_effect=run):
                    with self.assertRaisesRegex(ValueError, "source|single-parent|trailer"):
                        fixture.invoke()
                fixture.assert_no_bump_push()

    def test_guard_rejects_wrong_old_ref_literal_new_head_and_extra_updates(self):
        self.invoke()
        new = self.git("rev-parse", "HEAD")
        valid = f"{new} {new} refs/heads/selected {self.sha}\n"
        env = {**os.environ, "BUMP_EXPECTED_SHA": self.sha,
               "BUMP_EXPECTED_REF": "refs/heads/selected", "BUMP_NEW_SHA": new}
        cases = [
            "", valid + valid, valid.replace(self.sha, self.ancestor),
            valid.replace("refs/heads/selected", "refs/heads/other"),
            valid.replace(new, "HEAD", 1),
            valid.replace(new, "refs/heads/selected", 1),
            valid.replace(new, self.sha),
        ]
        for data in cases:
            with self.subTest(data=data):
                result = subprocess.run(
                    [str(self.hook), "origin", str(self.origin)], cwd=self.root, env=env,
                    input=data, text=True, capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
        self.git("checkout", "--detach", self.sha)
        result = subprocess.run(
            [str(self.hook), "origin", str(self.origin)], cwd=self.root, env=env,
            input=valid, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
