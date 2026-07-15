import json
import os

from gitmemory.cli import main


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_cli_init_creates_store(tmp_path):
    store = str(tmp_path / "mem.json")
    rc = main(["--store", store, "init"])
    assert rc == 0
    assert os.path.exists(store)
    data = json.load(open(store))
    assert data["memories"] == []


def test_cli_ingest_then_recall_fake(tmp_path, monkeypatch, capsys):
    """End-to-end through the CLI using the offline FakeLLM.

    FakeLLM returns '{}' for chat by default (no scripted memories), so ingest
    yields zero memories — but the pipeline, persistence, and recall wiring must
    still run cleanly and exit 0.
    """
    store = str(tmp_path / "mem.json")

    # ingest from a file
    src_file = str(tmp_path / "pr.txt")
    _write(src_file, "We decided to store all timestamps in UTC.")
    rc = main(["--store", store, "--fake", "ingest", "--source", "PR#1", "--file", src_file])
    assert rc == 0
    assert os.path.exists(store)

    # recall writes a comment file (empty is fine)
    out = str(tmp_path / "comment.md")
    q_file = str(tmp_path / "q.txt")
    _write(q_file, "timestamp timezone question")
    rc = main(["--store", store, "--fake", "recall", "--file", q_file, "--output", out])
    assert rc == 0
    assert os.path.exists(out)

    # stats runs
    rc = main(["--store", store, "stats"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "total" in captured.out


def test_cli_ingest_no_content_errors(tmp_path):
    store = str(tmp_path / "mem.json")
    # empty inline text -> exit code 2
    rc = main(["--store", store, "--fake", "ingest", "--source", "PR#1", "--text", "   "])
    assert rc == 2
