import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from chatgpt_web.actions import apply_patch
from chatgpt_web.errors import ClientError


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "projet avec espaces"
        self.work.mkdir()
        self.patch = self.root / "fix.patch"

    def write_patch(self, text):
        self.patch.write_text(text, encoding="utf-8")

    def test_check_does_not_modify_then_apply_utf8_outside_git(self):
        target = self.work / "f.txt"
        target.write_text("avant\n")
        self.write_patch("--- f.txt\n+++ f.txt\n@@ -1 +1 @@\n-avant\n+après\n")
        self.assertTrue(apply_patch(self.patch, self.work, check_only=True)["checked"])
        self.assertEqual(target.read_text(), "avant\n")
        self.assertTrue(apply_patch(self.patch, self.work)["applied"])
        self.assertEqual(target.read_text(), "après\n")

    def test_git_style_patch_and_creation(self):
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        self.write_patch("diff --git a/new.txt b/new.txt\nnew file mode 100644\n--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+nouveau\n")
        apply_patch(self.patch, self.work)
        self.assertEqual((self.work / "new.txt").read_text(), "nouveau\n")

    def test_failing_second_file_does_not_modify_first(self):
        (self.work / "first").write_text("old\n")
        (self.work / "second").write_text("different\n")
        self.write_patch("--- first\n+++ first\n@@ -1 +1 @@\n-old\n+new\n--- second\n+++ second\n@@ -1 +1 @@\n-old\n+new\n")
        with self.assertRaises(ClientError):
            apply_patch(self.patch, self.work)
        self.assertEqual((self.work / "first").read_text(), "old\n")

    def test_path_traversal_refused(self):
        outside = self.root / "outside"
        outside.write_text("old\n")
        self.write_patch("--- ../outside\n+++ ../outside\n@@ -1 +1 @@\n-old\n+new\n")
        with self.assertRaises(ClientError):
            apply_patch(self.patch, self.work)
        self.assertEqual(outside.read_text(), "old\n")

    def test_symlink_target_refused(self):
        outside = self.root / "outside"
        outside.write_text("old\n")
        (self.work / "link").symlink_to(outside)
        self.write_patch("--- link\n+++ link\n@@ -1 +1 @@\n-old\n+new\n")
        with self.assertRaises(ClientError):
            apply_patch(self.patch, self.work)
        self.assertEqual(outside.read_text(), "old\n")

    def test_git_subdirectory_requires_explicit_repository_root(self):
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        sub = self.work / "sub"
        sub.mkdir()
        self.write_patch("--- f\n+++ f\n@@ -1 +1 @@\n-old\n+new\n")
        with self.assertRaises(ClientError) as caught:
            apply_patch(self.patch, sub)
        self.assertEqual(caught.exception.code, "repository_root")

    def test_inherited_git_worktree_is_ignored(self):
        (self.work / "f").write_text("old\n")
        self.write_patch("--- f\n+++ f\n@@ -1 +1 @@\n-old\n+new\n")
        previous = os.environ.get("GIT_WORK_TREE")
        os.environ["GIT_WORK_TREE"] = str(self.root / "wrong")
        try:
            apply_patch(self.patch, self.work)
            self.assertEqual((self.work / "f").read_text(), "new\n")
        finally:
            if previous is None:
                os.environ.pop("GIT_WORK_TREE", None)
            else:
                os.environ["GIT_WORK_TREE"] = previous
