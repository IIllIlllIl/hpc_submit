"""Structured error taxonomy for UL HPC submission.

Every exception carries a machine-readable code, a human-readable message,
and a recommended fix. This guarantees that both logs and CLI output are
actionable and traceable.
"""

from typing import Optional


class ULHPCError(Exception):
    """Base class for all module errors."""

    code: str = "UNKNOWN_ERROR"
    suggestion: str = "Please review the logs. If the issue persists, contact UL HPC support."

    def __init__(self, message: str, suggestion: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if suggestion:
            self.suggestion = suggestion

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}\nSuggestion: {self.suggestion}"


# ---------- Config errors ----------

class ConfigError(ULHPCError):
    code = "CONFIG_ERROR"
    suggestion = "Run 'ulhpc-submit --init-config' to create a configuration file, or edit ~/.config/ulhpc-submit/config.yaml."


class ConfigMissingError(ConfigError):
    code = "CONFIG_MISSING"
    suggestion = "No config file found. Run 'ulhpc-submit --init-config' to create one."


# ---------- Sync errors ----------

class SyncError(ULHPCError):
    code = "SYNC_ERROR"
    suggestion = "Check local and remote paths, then retry."


class SyncNetworkError(SyncError):
    code = "SYNC_NETWORK_ERROR"
    suggestion = "Network failure during sync. Verify VPN/campus network and SSH connectivity to access-iris.uni.lu:8022."


class SyncPermissionError(SyncError):
    code = "SYNC_NO_PERMISSION"
    suggestion = "Remote directory is not writable. Check permissions or choose a different remote_project_dir."


class SyncDiskFullError(SyncError):
    code = "SYNC_DISK_FULL"
    suggestion = "Remote disk quota appears full. Clean up old runs or request more quota."


class SyncIntegrityError(SyncError):
    code = "SYNC_INTEGRITY_ERROR"
    suggestion = "File count or checksum mismatch after sync. Retry, or check excluded files."


# ---------- Environment / dependency errors ----------

class EnvError(ULHPCError):
    code = "ENV_ERROR"
    suggestion = "Review the dependency configuration and HPC module availability."


class EnvModuleNotFoundError(EnvError):
    code = "ENV_MODULE_NOT_FOUND"
    suggestion = "Required Environment module not found. Verify the conda/python module name in config."


class EnvCondaInstallError(EnvError):
    code = "ENV_CONDA_INSTALL_ERROR"
    suggestion = "Conda environment creation/update failed. Validate environment.yml / package versions."


class EnvPythonVersionMismatchError(EnvError):
    code = "ENV_PYTHON_VERSION_MISMATCH"
    suggestion = "Requested Python version does not match the HPC module. Adjust environment.yml or config."


class EnvDependencyError(EnvError):
    code = "ENV_DEPENDENCY_ERROR"
    suggestion = "A required package is missing or incompatible. Check requirements.txt / environment.yml."


# ---------- Job submission errors ----------

class JobError(ULHPCError):
    code = "JOB_ERROR"
    suggestion = "Review the Slurm job parameters and cluster status."


class JobSubmitError(JobError):
    code = "JOB_SUBMIT_ERROR"
    suggestion = "sbatch failed. Inspect the generated script and Slurm error message."


class JobInvalidResourcesError(JobError):
    code = "JOB_INVALID_RESOURCES"
    suggestion = "Requested resources exceed partition limits. Reduce --time, --mem, --cpus, or --gpus."


# ---------- Job runtime / monitor errors ----------

class JobPendingTimeoutError(JobError):
    code = "JOB_PENDING_TIMEOUT"
    suggestion = "Job waited too long. Reduce resource request, use a different partition, or submit off-peak."


class JobKilledError(JobError):
    code = "JOB_KILLED"
    suggestion = "Job was cancelled or hit a wall. Check --time, memory limits, and preemption policy."


class JobNodeError(JobError):
    code = "JOB_NODE_ERROR"
    suggestion = "Compute node failed. Resubmit; if recurrent, report to UL HPC support."


class HPCResourceError(JobError):
    code = "HPC_RESOURCE_ERROR"
    suggestion = "Resource limit exceeded (OOM, disk, CPU). Increase allocation or optimize your code."


# ---------- Code / output errors ----------

class CodeError(ULHPCError):
    code = "CODE_ERROR"
    suggestion = "Your program raised an exception. Debug locally, then resubmit."


class NetworkError(ULHPCError):
    code = "NETWORK_ERROR"
    suggestion = "External network/API call failed inside the job. Check proxy settings or firewall rules."


class UnknownError(ULHPCError):
    code = "UNKNOWN_ERROR"
    suggestion = "Unclassified failure. Save the log and contact UL HPC support with the error code."
