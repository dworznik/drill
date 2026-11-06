import os
import shutil
import tempfile
import unittest
from pathlib import Path

from drill.skill import BadPin, Pin, SkillCache, local_skill, revision
from tests.gitutil import ENV, commit, git, new_repo

SKILL = {"skills/ci/SKILL.md": "# ci\n", "skills/ci/evals/old/case.yaml": "x: 1\n",
         "skills/other/SKILL.md": "# other\n", "README.md": "root\n"}


class PinValidation(unittest.TestCase):
    def pin(self, **over):
        return Pin.from_rig({"repo": "octo/skills", "sha": "a" * 40, "path": "skills/ci", **over})

    def test_full_sha_is_accepted(self):
        self.assertEqual(self.pin().sha, "a" * 40)

    def test_short_sha_is_refused(self):
        with self.assertRaisesRegex(BadPin, "full 40-character SHA"):
            self.pin(sha="a" * 7)

    def test_branch_is_refused(self):
        with self.assertRaisesRegex(BadPin, "full 40-character SHA"):
            self.pin(sha="main")

    def test_repo_must_be_owner_slash_repo(self):
        with self.assertRaisesRegex(BadPin, "owner/repo"):
            self.pin(repo="skills")

    def test_path_must_stay_inside_the_repo(self):
        with self.assertRaisesRegex(BadPin, "path"):
            self.pin(path="../etc")

    def test_missing_field_is_refused(self):
        with self.assertRaisesRegex(BadPin, "sha"):
            Pin.from_rig({"repo": "octo/skills", "path": "skills/ci"})


class ExportFromCache(unittest.TestCase):
    def setUp(self):
        self.remotes = Path(tempfile.mkdtemp())
        self.origin = new_repo(SKILL)
        (self.remotes / "octo").mkdir()
        self.origin = Path(shutil.move(str(self.origin), self.remotes / "octo" / "skills.git"))
        self.sha = git(self.origin, "rev-parse", "HEAD")
        self.cache = SkillCache(Path(tempfile.mkdtemp()), base_url=f"file://{self.remotes}/", env=ENV)

    def pin(self, sha=None):
        return Pin(repo="octo/skills", sha=sha or self.sha, path="skills/ci")

    def test_exported_copy_is_only_the_pinned_path_and_is_deleted_after(self):
        with self.cache.export(self.pin()) as skill:
            names = sorted(str(p.relative_to(skill)) for p in skill.rglob("*") if p.is_file())
            self.assertEqual(names, ["SKILL.md", "evals/old/case.yaml"])
            kept = skill
        self.assertFalse(kept.exists())

    def test_export_is_at_the_pinned_sha_not_the_tip(self):
        commit(self.origin, {"skills/ci/SKILL.md": "# ci v2\n"})
        with self.cache.export(self.pin()) as skill:
            self.assertEqual((skill / "SKILL.md").read_text(), "# ci\n")

    def test_second_drill_reuses_the_clone_without_fetching(self):
        with self.cache.export(self.pin()):
            pass
        shutil.move(str(self.origin), str(self.remotes / "gone"))  # remote unreachable now
        with self.cache.export(self.pin()) as skill:
            self.assertTrue((skill / "SKILL.md").is_file())

    def test_a_missing_sha_is_fetched_into_the_cached_clone(self):
        with self.cache.export(self.pin()):
            pass
        newer = commit(self.origin, {"skills/ci/SKILL.md": "# ci v2\n"})
        with self.cache.export(self.pin(newer)) as skill:
            self.assertEqual((skill / "SKILL.md").read_text(), "# ci v2\n")

    def test_a_sha_the_repo_does_not_have_is_refused(self):
        with self.assertRaisesRegex(BadPin, "not found"):
            with self.cache.export(self.pin("b" * 40)):
                pass

    def test_a_path_not_in_the_commit_is_refused(self):
        with self.assertRaisesRegex(BadPin, "no skills/nope"):
            with self.cache.export(Pin("octo/skills", self.sha, "skills/nope")):
                pass


class Revisions(unittest.TestCase):
    def test_clean_checkout(self):
        repo = new_repo({"a": "1"})
        rev = revision(repo, env=ENV)
        self.assertEqual(rev, {"sha": git(repo, "rev-parse", "HEAD"), "dirty": False})

    def test_uncommitted_change_is_dirty(self):
        repo = new_repo({"a": "1"})
        (repo / "a").write_text("2")
        self.assertTrue(revision(repo, env=ENV)["dirty"])

    def test_untracked_file_is_dirty(self):
        repo = new_repo({"a": "1"})
        (repo / "b").write_text("new")
        self.assertTrue(revision(repo, env=ENV)["dirty"])

    def test_rig_revision_is_the_last_commit_touching_the_rig_dir(self):
        repo = new_repo({"ci/rig.json": "{}"})
        rig_sha = git(repo, "rev-parse", "HEAD")
        commit(repo, {"other/rig.json": "{}"})
        (repo / "other" / "x").write_text("dirty elsewhere")
        self.assertEqual(revision(repo / "ci", env=ENV), {"sha": rig_sha, "dirty": False})

    def test_rig_dir_change_is_dirty(self):
        repo = new_repo({"ci/rig.json": "{}"})
        (repo / "ci" / "rig.json").write_text('{"x": 1}')
        self.assertTrue(revision(repo / "ci", env=ENV)["dirty"])

    def test_not_a_git_checkout(self):
        self.assertEqual(revision(Path(tempfile.mkdtemp()), env=ENV), {"sha": None, "dirty": True})


class LocalSkillOverride(unittest.TestCase):
    def test_local_skill_is_copied_and_marked_dirty(self):
        repo = new_repo({"skills/ci/SKILL.md": "# ci\n"})
        with local_skill(repo / "skills" / "ci", env=ENV) as (skill, rev):
            self.assertTrue((skill / "SKILL.md").is_file())
            self.assertNotEqual(skill, repo / "skills" / "ci")  # a copy: drill rewrites evals/
            self.assertEqual(rev, {"sha": git(repo, "rev-parse", "HEAD"), "dirty": True})
        self.assertTrue((repo / "skills" / "ci" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
