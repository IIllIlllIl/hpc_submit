"""Tests for job submission."""

from pathlib import Path

import pytest

from ulhpc_submit.errors import (
    JobAuthorizationError,
    JobInvalidAccountError,
    JobInvalidQOSError,
    JobInvalidResourcesError,
    JobSubmitError,
)
from ulhpc_submit.job_manager import JobManager


def test_submit_parses_job_id(fake_ssh):
    fake_ssh.set_response("sbatch", 0, "Submitted batch job 123456\n", "")
    jm = JobManager(fake_ssh, "/remote")
    remote_script = "/remote/job.sh"
    job_id = jm.submit(remote_script)
    assert job_id == "123456"
    assert "sbatch /remote/job.sh" in fake_ssh.commands


def test_submit_invalid_resources(fake_ssh):
    fake_ssh.set_response(
        "sbatch", 1, "", "sbatch: error: Batch job submission failed: Requested node configuration is not available\n"
    )
    jm = JobManager(fake_ssh, "/remote")
    with pytest.raises(JobInvalidResourcesError):
        jm.submit("/remote/job.sh")


@pytest.mark.parametrize(
    "stderr",
    [
        "Requested node configuration is not available",
        "Requested time limit is invalid",
        "Invalid memory specification",
        "More processors requested than permitted",
    ],
)
def test_submit_keeps_explicit_resource_limit_errors_in_resource_class(
    fake_ssh, stderr
):
    fake_ssh.set_response("sbatch", 1, "", stderr)
    with pytest.raises(JobInvalidResourcesError) as exc_info:
        JobManager(fake_ssh, "/remote").submit("/remote/job.sh")
    assert stderr in str(exc_info.value)


def test_submit_unknown_error(fake_ssh):
    fake_ssh.set_response("sbatch", 1, "", "sbatch: some other error\n")
    jm = JobManager(fake_ssh, "/remote")
    with pytest.raises(JobSubmitError):
        jm.submit("/remote/job.sh")


@pytest.mark.parametrize(
    ("stderr", "error_class"),
    [
        ("User's group not permitted to use this partition", JobAuthorizationError),
        ("Invalid account or account/partition combination specified", JobAuthorizationError),
        ("Invalid account 'missing'", JobInvalidAccountError),
        ("Invalid qos specification", JobInvalidQOSError),
    ],
)
def test_submit_classifies_authorization_without_resource_advice(
    fake_ssh, stderr, error_class
):
    fake_ssh.set_response("sbatch", 1, "", stderr)
    jm = JobManager(fake_ssh, "/remote")

    with pytest.raises(error_class) as exc_info:
        jm.submit("/remote/job.sh")

    assert stderr in str(exc_info.value)
    assert "Reduce --time" not in str(exc_info.value)


def test_upload_script_writes_to_remote(tmp_path: Path, fake_ssh):
    local = tmp_path / "job.sh"
    local.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    jm = JobManager(fake_ssh, "/remote")
    remote = jm.upload_script(str(local), "job.sh")
    assert remote == "/remote/job.sh"
    assert "/remote/job.sh" in fake_ssh.files
