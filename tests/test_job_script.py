"""Tests for Slurm job script generation."""

from pathlib import Path

from ulhpc_submit.config import Config
from ulhpc_submit.env_manager import EnvironmentManager
from ulhpc_submit.job_script import JobScriptBuilder


def test_script_contains_user_command(project_dir: Path, tmp_config: Config):
    builder = JobScriptBuilder(
        config=tmp_config,
        command=["python", "main.py", "--epochs", "10"],
        project_dir=str(project_dir),
        remote_dir="~/hpc_runs/sample_project",
    )
    script = builder.build()
    assert "python main.py --epochs 10" in script
    assert "#SBATCH --job-name=sample_project" in script
    assert "#SBATCH --partition=batch" in script


def test_script_env_block_inside_job(project_dir: Path, tmp_config: Config):
    em = EnvironmentManager(str(project_dir), conda_env="myenv")
    builder = JobScriptBuilder(
        config=tmp_config,
        command=["python", "main.py"],
        project_dir=str(project_dir),
        remote_dir="~/hpc_runs/sample_project",
        env_manager=em,
    )
    script = builder.build()
    assert "module load miniconda3" in script
    assert "conda activate myenv" in script


def test_gpu_partition_and_gres(project_dir: Path, tmp_config: Config):
    builder = JobScriptBuilder(
        config=tmp_config,
        command=["python", "train.py"],
        project_dir=str(project_dir),
        remote_dir="~/hpc_runs/sample_project",
        partition="gpu",
        gpus=2,
    )
    script = builder.build()
    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --gres=gpu:2" in script


def test_script_writes_file(project_dir: Path, tmp_config: Config):
    builder = JobScriptBuilder(
        config=tmp_config,
        command=["python", "main.py"],
        project_dir=str(project_dir),
        remote_dir="~/hpc_runs/sample_project",
    )
    path = builder.write()
    assert Path(path).exists()
    assert Path(path).stat().st_mode & 0o111  # executable


def test_script_supports_module_runtime_without_conda(project_dir: Path, tmp_config: Config):
    builder = JobScriptBuilder(
        config=tmp_config,
        command=["python", "main.py"],
        project_dir=str(project_dir),
        remote_dir="~/hpc_runs/sample_project",
        runtime_modules=["lang/Python/3.11", "tools/Apptainer"],
        python_executable="python3",
        use_conda=False,
    )
    script = builder.build()
    assert "module load lang/Python/3.11" in script
    assert "module load tools/Apptainer" in script
    assert "module load miniconda3" not in script
    assert "python3 main.py" in script


def test_script_python_executable_rewrites_python_command(project_dir: Path, tmp_config: Config):
    builder = JobScriptBuilder(
        config=tmp_config,
        command=["python", "main.py"],
        project_dir=str(project_dir),
        remote_dir="~/hpc_runs/sample_project",
        python_executable="python3",
    )
    script = builder.build()
    assert "python3 main.py" in script


def test_script_exports_apptainer_cache_dirs(project_dir: Path, tmp_config: Config):
    builder = JobScriptBuilder(
        config=tmp_config,
        command=["python", "main.py"],
        project_dir=str(project_dir),
        remote_dir="~/hpc_runs/sample_project",
        container="~/images/app.sif",
        apptainer_cache_dir="/scratch/cache",
        apptainer_tmp_dir="/scratch/tmp",
        apptainer_sif_cache_dir="/scratch/sif",
    )
    script = builder.build()
    assert "mkdir -p /scratch/cache" in script
    assert 'export APPTAINER_CACHEDIR="/scratch/cache"' in script
    assert "mkdir -p /scratch/tmp" in script
    assert 'export APPTAINER_TMPDIR="/scratch/tmp"' in script
    assert "mkdir -p /scratch/sif" in script
    assert 'export ULHPC_APPTAINER_SIF_CACHE_DIR="/scratch/sif"' in script
