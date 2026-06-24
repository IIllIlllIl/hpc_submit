"""Tests for environment manager."""

from pathlib import Path

import pytest

from ulhpc_submit.env_manager import EnvironmentManager
from ulhpc_submit.errors import EnvDependencyError


def test_detect_dependency_files(project_dir: Path):
    em = EnvironmentManager(str(project_dir))
    files = em.detect_dependency_files()
    assert files["requirements"] is True
    assert files["environment_yml"] is False


def test_build_env_block_with_requirements(project_dir: Path):
    em = EnvironmentManager(str(project_dir), conda_env="testenv")
    block = em.build_env_block()
    assert "module load miniconda3" in block
    assert "conda activate testenv" in block
    assert "pip install -r requirements.txt" in block


def test_build_env_block_with_environment_yml(project_dir: Path):
    (project_dir / "environment.yml").write_text(
        "name: foo\ndependencies:\n  - python=3.10\n", encoding="utf-8"
    )
    em = EnvironmentManager(str(project_dir), conda_env="foo")
    block = em.build_env_block()
    assert "conda env create -f environment.yml" in block
    assert "conda env update" in block


def test_python_version_extraction():
    em = EnvironmentManager("/tmp", python_module="lang/Python/3.10")
    assert em._python_version() == "3.10"


def test_build_env_block_warns_on_pip_fallback(project_dir: Path):
    em = EnvironmentManager(str(project_dir), conda_env="testenv")
    block = em.build_env_block()
    assert "WARNING: conda not available; falling back to pip" in block
    assert "Some packages may differ from the conda environment" in block
    assert "module load lang/Python/3.11" in block


def test_build_env_block_no_conda_with_runtime_modules(project_dir: Path):
    em = EnvironmentManager(
        str(project_dir),
        runtime_modules=["lang/Python/3.11", "tools/Apptainer"],
        python_executable="python3",
        use_conda=False,
    )
    block = em.build_env_block()
    assert "module load lang/Python/3.11" in block
    assert "module load tools/Apptainer" in block
    assert "module load miniconda3" not in block
    assert "conda activate" not in block
    assert "python3 -m pip install --user -r requirements.txt" in block


def test_empty_requirements_file(project_dir: Path):
    (project_dir / "requirements.txt").write_text("", encoding="utf-8")
    em = EnvironmentManager(str(project_dir))
    with pytest.raises(EnvDependencyError):
        em.validate_local_dependencies()
