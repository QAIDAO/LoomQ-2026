import io
import hashlib
import importlib.util
import json
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from competition.collect_submissions import collect, extract_artifact_wrapper
from competition.submission_common import (
    GitHubAPIError,
    SubmissionError,
    parse_issue_body,
    parse_timestamp,
    receipt_from_comments,
    safe_extract_submission,
    select_latest_submissions,
)
from competition.submission_intake import validate_issue


SHA = "a" * 40


def load_prepare_submission():
    path = Path(__file__).resolve().parents[1] / "starter_kit" / "prepare_submission.py"
    spec = importlib.util.spec_from_file_location("loomq_prepare_submission_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def issue_body(sha=SHA, team_id="alice", repository="https://github.com/alice/LoomQ-2026"):
    return f"""### Team ID

{team_id}

### Fork repository

{repository}

### Commit SHA

{sha}

### Levels

- [x] L1

### Hardware evidence

_No response_

### Declarations

- [x] I agree that this commit is the team's own submission and the team can explain its implementation.
- [x] I agree that the organizers may archive, build, execute, and review this commit for competition judging.
- [x] I agree that the GitHub Issue creation time is the authoritative submission time.
"""


def event(
    created_at="2026-08-25T04:00:00Z",
    author="alice",
    sha=SHA,
    team_id="alice",
    repository="https://github.com/alice/LoomQ-2026",
):
    return {
        "issue": {
            "number": 7,
            "created_at": created_at,
            "html_url": "https://github.com/QAIDAO/LoomQ-2026/issues/7",
            "user": {"login": author},
            "body": issue_body(sha, team_id, repository),
        }
    }


CONFIG = {
    "upstream": "QAIDAO/LoomQ-2026",
    "deadline": "2026-08-25T04:00:00Z",
    "deadline_display": "2026-08-25 12:00 UTC+8",
    "submission_path": "starter_kit",
    "max_archive_bytes": 1024 * 1024,
    "required_files": [
        "starter_kit/submission.yaml",
        "starter_kit/adapter.py",
        "starter_kit/Dockerfile",
        "starter_kit/README.md",
    ],
}


class FakeGitHub:
    def __init__(self, source="QAIDAO/LoomQ-2026", missing_file=False, missing_commit=False):
        self.source = source
        self.missing_file = missing_file
        self.missing_commit = missing_commit

    def json(self, method, path, data=None):
        if "/commits/" in path:
            if self.missing_commit:
                raise GitHubAPIError(method, path, 404, "missing")
            return {"sha": SHA}
        if "/contents/" in path:
            if self.missing_file and "README.md" in path:
                raise GitHubAPIError(method, path, 404, "missing")
            return {"type": "file"}
        return {"fork": True, "source": {"full_name": self.source}}

    def download(self, path, destination, max_bytes):
        destination.write_bytes(b"archive")
        return 7


class IntakeValidationTests(unittest.TestCase):
    def test_issue_author_can_submit_without_a_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_issue(
                event(team_id="alice"), CONFIG, FakeGitHub(), Path(tmp) / "submission.tar.gz"
            )
        self.assertEqual(result["team_id"], "alice")

    def test_single_character_github_login_is_a_valid_team_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = validate_issue(
                event(author="a", team_id="a", repository="https://github.com/a/LoomQ-2026"),
                CONFIG,
                FakeGitHub(),
                Path(tmp) / "submission.tar.gz",
            )
        self.assertEqual(result["team_id"], "a")

    def test_issue_parser_uses_stable_headings(self):
        fields = parse_issue_body(issue_body())
        self.assertEqual(fields["team_id"], "alice")
        self.assertEqual(fields["commit_sha"], SHA)
        self.assertEqual(fields["hardware_evidence"], "")

    def test_exact_deadline_is_accepted_and_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "submission.tar.gz"
            result = validate_issue(event(), CONFIG, FakeGitHub(), archive)
        self.assertEqual(result["team_id"], "alice")
        self.assertEqual(result["commit_sha"], SHA)
        self.assertEqual(result["archive_bytes"], 7)

    def test_after_deadline_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(SubmissionError, "晚于"):
            validate_issue(
                event(created_at="2026-08-25T04:00:01Z"),
                CONFIG,
                FakeGitHub(),
                Path(tmp) / "submission.tar.gz",
            )

    def test_team_id_must_match_issue_author(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(SubmissionError, "Issue 作者"):
            validate_issue(event(author="mallory"), CONFIG, FakeGitHub(), Path(tmp) / "x")

    def test_fork_owner_must_match_issue_author(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(SubmissionError, "fork 所有者"):
            validate_issue(
                event(repository="https://github.com/bob/LoomQ-2026"),
                CONFIG,
                FakeGitHub(),
                Path(tmp) / "x",
            )

    def test_short_sha_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(SubmissionError, "40 位"):
            validate_issue(event(sha="abc"), CONFIG, FakeGitHub(), Path(tmp) / "x")

    def test_missing_commit_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(SubmissionError, "不存在"):
            validate_issue(
                event(), CONFIG, FakeGitHub(missing_commit=True), Path(tmp) / "x"
            )

    def test_non_fork_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(SubmissionError, "fork network"):
            validate_issue(
                event(), CONFIG, FakeGitHub(source="someone/else"), Path(tmp) / "x"
            )

    def test_missing_required_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(SubmissionError, "缺少必需文件"):
            validate_issue(event(), CONFIG, FakeGitHub(missing_file=True), Path(tmp) / "x")


class PreflightIdentityTests(unittest.TestCase):
    def test_preflight_rejects_team_id_that_does_not_own_origin_fork(self):
        prepare = load_prepare_submission()
        responses = {
            ("status", "--porcelain"): "",
            ("rev-parse", "HEAD"): SHA,
            ("remote", "get-url", "origin"): "git@github.com:bob/LoomQ-2026.git",
        }

        def fake_git(*arguments, **_kwargs):
            return responses[arguments]

        with mock.patch.object(prepare, "git", side_effect=fake_git), mock.patch.object(
            prepare.sys, "argv", ["prepare_submission.py", "--team-id", "alice"]
        ), self.assertRaisesRegex(RuntimeError, "fork 的 GitHub 所有者"):
            prepare.main()


class SelectionTests(unittest.TestCase):
    def test_latest_accepted_issue_per_team_wins(self):
        issues = [
            {"number": 1, "created_at": "2026-08-25T03:00:00Z", "labels": [{"name": "submission:accepted"}]},
            {"number": 2, "created_at": "2026-08-25T03:30:00Z", "labels": [{"name": "submission:accepted"}]},
            {"number": 3, "created_at": "2026-08-25T04:00:01Z", "labels": [{"name": "submission:accepted"}]},
        ]
        receipts = {number: {"team_id": "team-001"} for number in (1, 2, 3)}
        selected = select_latest_submissions(issues, receipts, parse_timestamp(CONFIG["deadline"]))
        self.assertEqual(selected["team-001"][0]["number"], 2)

    def test_higher_issue_number_breaks_timestamp_tie(self):
        issues = [
            {"number": number, "created_at": "2026-08-25T03:00:00Z", "labels": [{"name": "submission:accepted"}]}
            for number in (8, 9)
        ]
        receipts = {number: {"team_id": "team-001"} for number in (8, 9)}
        selected = select_latest_submissions(issues, receipts, parse_timestamp(CONFIG["deadline"]))
        self.assertEqual(selected["team-001"][0]["number"], 9)

    def test_receipt_must_be_authored_by_actions_bot(self):
        payload = {"team_id": "team-001"}
        marker = f"<!-- loomq-submission-receipt:v1\n{json.dumps(payload)}\n-->"
        comments = [
            {"user": {"login": "mallory"}, "body": marker},
            {"user": {"login": "github-actions[bot]"}, "body": marker},
        ]
        self.assertEqual(receipt_from_comments(comments), payload)


def make_tar(path: Path, members):
    with tarfile.open(path, "w:gz") as bundle:
        for name, kind, data in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                raw = data.encode()
                info.size = len(raw)
                bundle.addfile(info, io.BytesIO(raw))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = data
                bundle.addfile(info)


class ArchiveSafetyTests(unittest.TestCase):
    def test_extracts_only_starter_kit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "source.tar.gz"
            make_tar(archive, [
                ("repo-sha/starter_kit/adapter.py", "file", "ok"),
                ("repo-sha/README.md", "file", "not submission"),
            ])
            destination = root / "submission"
            safe_extract_submission(archive, destination, "starter_kit", 10, 1000)
            self.assertEqual((destination / "adapter.py").read_text(), "ok")
            self.assertFalse((destination / "README.md").exists())

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "source.tar.gz"
            make_tar(archive, [("repo-sha/../escape", "file", "bad")])
            with self.assertRaisesRegex(SubmissionError, "不安全"):
                safe_extract_submission(archive, root / "submission", "starter_kit", 10, 1000)

    def test_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "source.tar.gz"
            make_tar(archive, [("repo-sha/starter_kit/link", "symlink", "/etc/passwd")])
            with self.assertRaisesRegex(SubmissionError, "文件类型"):
                safe_extract_submission(archive, root / "submission", "starter_kit", 10, 1000)

    def test_artifact_wrapper_requires_single_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "artifact.zip"
            with zipfile.ZipFile(wrapper, "w") as bundle:
                bundle.writestr("loomq-submission.tar.gz", b"payload")
            archive = root / "out.tar.gz"
            extract_artifact_wrapper(wrapper, archive, 100)
            self.assertEqual(archive.read_bytes(), b"payload")

    def test_artifact_wrapper_rejects_oversized_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrapper = root / "artifact.zip"
            with zipfile.ZipFile(wrapper, "w") as bundle:
                bundle.writestr("loomq-submission.tar.gz", b"too large")
            with self.assertRaisesRegex(SubmissionError, "大小限制"):
                extract_artifact_wrapper(wrapper, root / "out.tar.gz", 3)

    def test_tar_rejects_extracted_size_over_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "source.tar.gz"
            make_tar(archive, [("repo-sha/starter_kit/large.bin", "file", "12345")])
            with self.assertRaisesRegex(SubmissionError, "大小限制"):
                safe_extract_submission(archive, root / "submission", "starter_kit", 10, 4)


class CollectorEndToEndTests(unittest.TestCase):
    def test_collects_from_artifact_without_reading_source_fork(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.tar.gz"
            make_tar(source, [("repo-sha/starter_kit/adapter.py", "file", "collected")])
            archive_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            wrapper = root / "artifact.zip"
            with zipfile.ZipFile(wrapper, "w") as bundle:
                bundle.write(source, "loomq-submission.tar.gz")
            receipt = {
                "team_id": "team-001",
                "repository": "https://github.com/alice/LoomQ-2026",
                "commit_sha": SHA,
                "issue_number": 7,
                "issue_created_at": "2026-08-25T03:00:00Z",
                "archive_sha256": archive_sha,
                "artifact_id": 123,
                "artifact_name": "submission-team-001-issue-7",
                "submission_path": "starter_kit",
            }
            marker = f"<!-- loomq-submission-receipt:v1\n{json.dumps(receipt)}\n-->"
            issue = {
                "number": 7,
                "created_at": "2026-08-25T03:00:00Z",
                "html_url": "https://github.com/QAIDAO/LoomQ-2026/issues/7",
                "labels": [{"name": "submission:accepted"}],
            }

            class CollectorClient:
                def __init__(self, token):
                    self.token = token

                def json(self, method, path, data=None):
                    if "/comments" in path:
                        return [{"user": {"login": "github-actions[bot]"}, "body": marker}]
                    return [issue]

                def download(self, path, destination, max_bytes):
                    destination.write_bytes(wrapper.read_bytes())
                    return destination.stat().st_size

            config = {
                "upstream": "QAIDAO/LoomQ-2026",
                "deadline": "2026-08-25T04:00:00Z",
                "submission_path": "starter_kit",
                "max_archive_bytes": 1024 * 1024,
                "max_extracted_bytes": 1024 * 1024,
                "max_files": 100,
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            output = root / "intake"
            arguments = SimpleNamespace(config=str(config_path), output=str(output))
            with mock.patch("competition.collect_submissions.GitHubClient", CollectorClient), mock.patch.dict(
                os.environ, {"GH_TOKEN": "test-token"}
            ):
                result = collect(arguments)
            self.assertEqual(result, 0)
            extracted = output / "teams" / "team-001" / "submission" / "adapter.py"
            self.assertEqual(extracted.read_text(), "collected")
            manifest = json.loads((output / "intake-manifest.json").read_text())
            self.assertEqual(manifest["collected_count"], 1)

            mismatch_output = root / "intake-mismatch"
            mismatch_arguments = SimpleNamespace(config=str(config_path), output=str(mismatch_output))
            with mock.patch("competition.collect_submissions.GitHubClient", CollectorClient), mock.patch(
                "competition.collect_submissions.sha256_file", return_value="0" * 64
            ), mock.patch.dict(os.environ, {"GH_TOKEN": "test-token"}):
                mismatch_result = collect(mismatch_arguments)
            self.assertEqual(mismatch_result, 1)
            mismatch_manifest = json.loads((mismatch_output / "intake-manifest.json").read_text())
            self.assertEqual(mismatch_manifest["submissions"][0]["status"], "ERROR")
            self.assertIn("SHA-256", mismatch_manifest["submissions"][0]["error"])


if __name__ == "__main__":
    unittest.main()
