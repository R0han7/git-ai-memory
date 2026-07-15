import json
import os
import subprocess

import pytest

from gitmemory.cli import main
from gitmemory.models import MemoryRecord, MemoryStatus
from gitmemory.store import MemoryStore


def _write_store(path, records):
    s = MemoryStore(path=path)
    for r in records:
        s.add(r)
    s.save()


def test_cli_merge_unions_files(tmp_path):
    f1 = str(tmp_path / "a.json")
    f2 = str(tmp_path / "b.json")
    out = str(tmp_path / "out.json")
    _write_store(f1, [MemoryRecord(claim="one", id="m1")])
    _write_store(f2, [MemoryRecord(claim="two", id="m2")])

    rc = main(["merge", f1, f2, "-o", out])
    assert rc == 0
    merged = MemoryStore.load(out)
    assert {r.id for r in merged.all()} == {"m1", "m2"}


def test_cli_merge_skips_missing_inputs(tmp_path):
    f1 = str(tmp_path / "a.json")
    missing = str(tmp_path / "nope.json")
    out = str(tmp_path / "out.json")
    _write_store(f1, [MemoryRecord(claim="one", id="m1")])
    rc = main(["merge", missing, f1, "-o", out])
    assert rc == 0
    assert len(MemoryStore.load(out)) == 1


def test_cli_merge_all_missing_errors(tmp_path):
    rc = main(["merge", str(tmp_path / "x.json"), str(tmp_path / "y.json")])
    assert rc == 2


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git not available")
def test_git_merge_driver_resolves_without_conflict(tmp_path):
    """End-to-end: two branches change the memory file; the union driver merges
    them with no conflict and a correct result."""
    repo = tmp_path / "repo"
    repo.mkdir()
    store_dir = repo / ".gitmemory"
    store_dir.mkdir()
    store_path = str(store_dir / "memories.json")

    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t.co"], repo)
    _git(["config", "user.name", "t"], repo)
    default_branch = _git(["symbolic-ref", "--short", "HEAD"], repo).stdout.strip()

    # Register the union driver to point at our installed CLI.
    driver = f"{__import__('sys').executable} -m gitmemory.cli merge %O %A %B -o %A"
    _git(["config", "merge.gitmemory.name", "union"], repo)
    _git(["config", "merge.gitmemory.driver", driver], repo)
    (repo / ".gitattributes").write_text(".gitmemory/memories.json merge=gitmemory\n")

    # base commit: one shared record
    _write_store(store_path, [MemoryRecord(claim="shared", id="base")])
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "base"], repo)

    # branch feature: add record m_feat
    _git(["checkout", "-q", "-b", "feature"], repo)
    _write_store(store_path, [MemoryRecord(claim="shared", id="base"),
                              MemoryRecord(claim="feat", id="m_feat")])
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "feature"], repo)

    # back on the default branch: add a different record m_main
    _git(["checkout", "-q", default_branch], repo)
    _write_store(store_path, [MemoryRecord(claim="shared", id="base"),
                              MemoryRecord(claim="main", id="m_main")])
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "main"], repo)

    # merge feature into main — must NOT conflict
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(os.path.dirname(__file__), "..", "src")
    result = subprocess.run(["git", "merge", "feature", "-m", "merge"],
                            cwd=repo, capture_output=True, text=True, env=env)
    assert result.returncode == 0, f"merge conflicted:\n{result.stdout}\n{result.stderr}"

    merged = MemoryStore.load(store_path)
    ids = {r.id for r in merged.all()}
    assert ids == {"base", "m_feat", "m_main"}   # both branches' records survive
