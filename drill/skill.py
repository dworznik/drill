"""Getting the skill under drill: the pin, the clone cache, the throwaway copy, and revisions (ADR-0002)."""

import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

FULL_SHA = re.compile(r"[0-9a-f]{40}")
OWNER_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class BadPin(Exception):
    """The rig's skill pin can't be honoured."""


@dataclass(frozen=True)
class Pin:
    repo: str  # owner/repo
    sha: str   # full commit SHA, never a branch
    path: str  # subpath of the skill inside the repo

    @classmethod
    def from_rig(cls, rig: dict) -> "Pin":
        for key in ("repo", "sha", "path"):
            if not isinstance(rig.get(key), str) or not rig[key]:
                raise BadPin(f"rig.json needs a string {key!r}")
        repo, sha, path = rig["repo"], rig["sha"], rig["path"]
        if not OWNER_REPO.fullmatch(repo) or repo.split("/")[1] in (".", ".."):
            raise BadPin(f"repo must be owner/repo, got {repo!r}")
        if not FULL_SHA.fullmatch(sha):
            raise BadPin(f"sha must be a full 40-character SHA (not a branch or short SHA), got {sha!r}")
        p = PurePosixPath(path)
        if p.is_absolute() or ".." in p.parts:
            raise BadPin(f"path must be a subpath inside the repo, got {path!r}")
        return cls(repo, sha, str(p))


def user_cache() -> Path:
    """drill's own cache dir: `$XDG_CACHE_HOME/drill`, else `~/.cache/drill`."""
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "drill"


def default_cache_root() -> Path:
    return user_cache() / "repos"


def _git(*args, env=None, cwd=None, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=check, capture_output=True)


class SkillCache:
    """One bare clone per repo, fetched with the caller's own git credentials."""

    def __init__(self, root: Path | None = None, base_url: str = "https://github.com/", env=None):
        self.root = root or default_cache_root()
        self.base_url = base_url
        self.env = env

    def _has(self, bare: Path, sha: str) -> bool:
        return _git("--git-dir", str(bare), "cat-file", "-e", f"{sha}^{{commit}}",
                    env=self.env, check=False).returncode == 0

    def clone(self, pin: Pin) -> Path:
        """The bare clone holding pin.sha, cloning or fetching only when needed."""
        bare = self.root / f"{pin.repo}.git"
        url = f"{self.base_url}{pin.repo}.git"
        if not bare.is_dir():
            bare.parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(dir=bare.parent, prefix=".clone-"))
            r = _git("clone", "--bare", "--quiet", url, str(tmp / "repo.git"), env=self.env, check=False)
            if r.returncode:
                shutil.rmtree(tmp)
                raise BadPin(f"can't clone {url}: {r.stderr.decode().strip()}")
            (tmp / "repo.git").rename(bare)
            tmp.rmdir()
        if not self._has(bare, pin.sha):
            # A SHA fetch works on GitHub; fall back to every branch and tag.
            _git("--git-dir", str(bare), "fetch", "--quiet", "origin", pin.sha, env=self.env, check=False)
            if not self._has(bare, pin.sha):
                _git("--git-dir", str(bare), "fetch", "--quiet", "origin",
                     "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*", env=self.env, check=False)
        if not self._has(bare, pin.sha):
            raise BadPin(f"commit {pin.sha} not found in {pin.repo}")
        return bare

    @contextmanager
    def export(self, pin: Pin):
        """Yield a throwaway directory holding only pin.path at pin.sha; delete it afterwards."""
        bare = self.clone(pin)
        r = _git("--git-dir", str(bare), "archive", "--format=tar", pin.sha, "--", pin.path,
                 env=self.env, check=False)
        if r.returncode:
            raise BadPin(f"{pin.repo}@{pin.sha[:12]} has no {pin.path}")
        work = Path(tempfile.mkdtemp(prefix="drill-skill-"))
        try:
            with tarfile.open(fileobj=io.BytesIO(r.stdout)) as tar:
                tar.extractall(work, filter="data")
            yield work / pin.path
        finally:
            shutil.rmtree(work, ignore_errors=True)


@contextmanager
def local_skill(skill_dir: Path, env=None):
    """`--skill <dir>`: a throwaway copy of a working tree, its revision always dirty."""
    work = Path(tempfile.mkdtemp(prefix="drill-skill-"))
    try:
        copy = work / skill_dir.resolve().name
        shutil.copytree(skill_dir, copy, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        yield copy, {"sha": revision(skill_dir, env=env)["sha"], "dirty": True}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def revision(path: Path, env=None) -> dict:
    """The last commit touching `path` and whether `path` has uncommitted changes.

    For a repo root that is HEAD; for a rig directory, the last rigs-repo commit touching it.
    Outside any git checkout the SHA is None and the revision counts as dirty.
    """
    path = Path(path).resolve()
    log = _git("log", "-1", "--format=%H", "--", ".", cwd=path, env=env, check=False)
    sha = log.stdout.decode().strip() or None if log.returncode == 0 else None
    if sha is None:
        return {"sha": None, "dirty": True}
    status = _git("status", "--porcelain", "--untracked-files=normal", "--", ".", cwd=path, env=env)
    return {"sha": sha, "dirty": bool(status.stdout.strip())}


def drill_revision(env=None) -> dict:
    """drill's own checkout: this package's repo."""
    return revision(Path(__file__).resolve().parent.parent, env=env)
