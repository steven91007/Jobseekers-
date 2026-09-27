"""Offline end-to-end exercise of gitkb against a throwaway git repository."""
import json, os, pathlib, shutil, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from gitkb import build as build_mod, cli, db, hashing, notes
from gitkb.config import load
from gitkb.summarize import CommitAnalysis, DrySummarizer, FileSummary, reconcile

FIXED_ENV = {
    "GIT_AUTHOR_NAME": "Test Author", "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Committer", "GIT_COMMITTER_EMAIL": "committer@example.com",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
}


def git(repo, *args, date=None):
    env = dict(os.environ, **FIXED_ENV)
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)


def make_repo(root):
    git(root, "init", "-q", "-b", "main")
    (root / "a.py").write_text("def f():\n    return 1\n")
    (root / "README.md").write_text("# demo\n")
    git(root, "add", "."); git(root, "commit", "-q", "-m", "first commit", date="2026-01-01T00:00:00+00:00")

    (root / "a.py").write_text("def f():\n    return 2\n\n\ndef g():\n    return 3\n")
    (root / "docs").mkdir(); git(root, "mv", "README.md", "docs/README.md")
    (root / "img.bin").write_bytes(b"\x00\x01\x02binary\x00")
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n' * 5)
    git(root, "add", "."); git(root, "commit", "-q", "-m", "second commit\n\nwith a body", date="2026-01-02T00:00:00+00:00")

    git(root, "rm", "-q", "a.py")
    git(root, "commit", "-q", "-m", "third commit", date="2026-01-03T00:00:00+00:00")


class CountingSummarizer(DrySummarizer):
    calls = 0
    def summarize(self, canon):
        CountingSummarizer.calls += 1
        return super().summarize(canon)


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="gitkb-test-")).resolve()
    ok = True
    try:
        repo = tmp / "repo"; repo.mkdir(); make_repo(repo)
        kdir = repo / "knowledge"
        argv = ["--repo", str(repo), "--knowledge-dir", str(kdir)]

        # 1. dry-run build writes notes + index
        assert cli.main(argv + ["build", "--dry-run"]) == 0
        assert len(list((kdir / "commits").glob("*.md"))) == 3
        change_notes = list((kdir / "changes").glob("*.md"))
        assert len(change_notes) == 2 + 4 + 1, len(change_notes)
        conn = db.connect(kdir / "index.db")
        c = db.counts(conn)
        assert c == {"commits": 3, "changes": 7, "links": 7 + 2, "commit_parents": 2, "notes_fts": 10}, c
        statuses = {(ch.path, ch.status) for r in db.list_commits(conn) for ch in db.changes_for_commit(conn, r.git_sha)}
        assert ("docs/README.md", "R") in statuses and ("a.py", "D") in statuses and ("img.bin", "A") in statuses, statuses
        second = [r for r in db.list_commits(conn) if r.subject == "second commit"][0]
        by_path = {ch.path: ch for ch in db.changes_for_commit(conn, second.git_sha)}
        assert by_path["img.bin"].is_binary and by_path["img.bin"].insertions is None
        assert by_path["package-lock.json"].truncated and by_path["package-lock.json"].truncation_reason == "generated"
        assert by_path["docs/README.md"].old_path == "README.md"
        assert second.model == "dry-run"
        # links: commit note -> change notes, and child -> parent
        outgoing = db.links_from(conn, second.note_sha256)
        assert sum(1 for _, rel in outgoing if rel == "commit_change") == 4
        assert sum(1 for _, rel in outgoing if rel == "commit_parent") == 1
        assert db.get_commit(conn, second.git_sha[:7]).note_sha256 == second.note_sha256

        # 2. front matter round trip and hash properties
        cfg = load(repo, knowledge_dir=kdir)
        canon = hashing.canonicalize(repo, second.git_sha, cfg)
        canon2 = hashing.canonicalize(repo, second.git_sha, cfg)
        assert canon.commit_sha256 == canon2.commit_sha256 == second.note_sha256
        assert all(a.sha256 == b.sha256 for a, b in zip(canon.per_file, canon2.per_file))
        assert len({pf.sha256 for pf in canon.per_file} | {canon.commit_sha256}) == 5
        third = [r for r in db.list_commits(conn) if r.subject == "third commit"][0]
        canon3 = hashing.canonicalize(repo, third.git_sha, cfg)
        assert canon3.commit_sha256 != canon3.per_file[0].sha256  # single-file commit vs its change
        assert "".join(pf.chunk for pf in canon.per_file) in canon.commit_text
        text = (kdir / "commits" / f"{second.note_sha256}.md").read_text("utf-8")
        parsed = notes.parse_note(text)
        assert parsed.front["git_sha"] == second.git_sha and parsed.front["files"][0]["note_sha256"]
        assert parsed.front["subject"] == "second commit" and "with a body" in parsed.sections["Commit message"]
        assert notes.parse_front_matter(notes.dump_front_matter({"a": 'x "y" \n', "b": [1, {"c": None}], "d": []}))[0] == {"a": 'x "y" \n', "b": [1, {"c": None}], "d": []}

        # 3. idempotent rebuild and crash healing
        assert cli.main(argv + ["build", "--dry-run"]) == 0
        stats = build_mod.build(repo, cfg, conn, DrySummarizer())
        assert stats.summarized == 0 and stats.skipped == 3, stats
        with conn:
            conn.execute("DELETE FROM commits WHERE git_sha=?", (second.git_sha,))
        stats = build_mod.build(repo, cfg, conn, CountingSummarizer())
        assert stats.reindexed_from_md == 1 and CountingSummarizer.calls == 0, stats
        assert db.counts(conn)["commits"] == 3

        # 4. reconcile fills missing files, never drops
        analysis = CommitAnalysis("s", "w", "r", [], [FileSummary("b/a.py", "changed a", "refactor", ["f"])])
        rec = reconcile(analysis, canon)
        assert set(rec) == {pf.change.path for pf in canon.per_file}
        assert rec["a.py"].kind == "refactor" and rec["img.bin"].kind == "other"

        # 5. pending -> import replaces placeholders with real summaries
        assert cli.main(argv + ["pending", "--out", str(tmp / "pending.json")]) == 0
        pending = json.loads((tmp / "pending.json").read_text("utf-8"))
        assert [c["subject"] for c in pending["commits"]] == ["first commit", "second commit", "third commit"]
        assert pending["commits"][1]["note_sha256"] == second.note_sha256
        answers = {"model": "test-model", "commits": {}}
        for c in pending["commits"]:
            answers["commits"][c["git_sha"]] = {
                "summary": f"Real summary of {c['subject']}.", "why": "Because.", "risks": "none identified",
                "tags": ["demo"],
                "files": [{"path": f["path"], "summary": f"Real change to {f['path']}.", "kind": "feature",
                           "notable_symbols": ["f"]} for f in c["files"]],
            }
        (tmp / "answers.json").write_text(json.dumps(answers), "utf-8")
        assert cli.main(argv + ["import", str(tmp / "answers.json")]) == 0
        conn = db.connect(kdir / "index.db")
        rows = db.list_commits(conn)
        assert all(r.model == "test-model" for r in rows) and all(r.summary_short.startswith("Real summary") for r in rows)
        assert len(list((kdir / "changes").glob("*.md"))) == 7  # same filenames, overwritten
        assert cli.main(argv + ["pending", "--out", str(tmp / "pending2.json")]) == 0
        assert json.loads((tmp / "pending2.json").read_text("utf-8"))["commits"] == []
        assert cli.main(argv + ["build"]) == 0  # nothing to do, no failure

        # 6. rebuild-index from notes yields the same index
        before = {r.note_sha256 for r in rows}
        conn.close()
        assert cli.main(argv + ["rebuild-index"]) == 0
        conn = db.connect(kdir / "index.db")
        assert {r.note_sha256 for r in db.list_commits(conn)} == before and db.counts(conn)["changes"] == 7

        # 7. search / show / log / history
        hits = db.search(conn, "real")
        assert hits and any(h.kind == "commit" for h in hits)
        assert cli.main(argv + ["search", "summary"]) == 0
        assert cli.main(argv + ["show", second.git_sha[:7]]) == 0
        assert cli.main(argv + ["show", second.note_sha256]) == 0
        assert cli.main(argv + ["show", canon.per_file[0].sha256]) == 0
        assert cli.main(argv + ["show", "deadbeef"]) == 1
        assert cli.main(argv + ["log"]) == 0
        assert len(db.changes_for_path(conn, "a.py")) == 3
        assert cli.main(argv + ["history", "a.py"]) == 0
        # no leftover temp files from atomic writes
        assert not list(kdir.rglob(".*.tmp"))
    except AssertionError:
        ok = False
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print("gitkb tests:", "OK" if ok else "FAILED")


if __name__ == "__main__":
    main()
