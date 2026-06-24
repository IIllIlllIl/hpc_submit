"""Dependency/environment detection and Slurm-script environment block generation.

Important: per UL HPC rules, `module` commands and conda/pip installation run
inside the Slurm job on compute nodes. This module only inspects local files
and generates a shell snippet that will be embedded in the job script.
"""

import os
from pathlib import Path
from typing import List, Optional

import yaml

from .errors import EnvDependencyError


class EnvironmentManager:
    """Detect local dependency files and build an env setup block."""

    def __init__(
        self,
        project_dir: str,
        conda_module: str = "miniconda3",
        python_module: str = "lang/Python/3.11",
        conda_env: Optional[str] = None,
        runtime_modules: Optional[List[str]] = None,
        python_executable: str = "python",
        use_conda: bool = True,
    ):
        self.project_dir = Path(project_dir)
        self.conda_module = conda_module
        self.python_module = python_module
        self.conda_env = conda_env or self._default_env_name()
        self.runtime_modules = runtime_modules or []
        self.python_executable = python_executable
        self.use_conda = use_conda

    def _default_env_name(self) -> str:
        return self.project_dir.name or "hpc_env"

    def detect_dependency_files(self) -> dict:
        """Return which dependency files exist."""
        return {
            "requirements": (self.project_dir / "requirements.txt").exists(),
            "environment_yml": (self.project_dir / "environment.yml").exists(),
        }

    def _env_name_from_yml(self) -> Optional[str]:
        """Read the 'name' field from environment.yml if it exists."""
        env_file = self.project_dir / "environment.yml"
        if not env_file.exists():
            return None
        try:
            data = yaml.safe_load(env_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("name")
        except Exception:  # noqa: BLE001
            return None
        return None

    def build_env_block(
        self,
        use_requirements: Optional[bool] = None,
        use_environment_yml: Optional[bool] = None,
    ) -> str:
        """Generate a shell block to be placed inside the Slurm script.

        The block loads modules and installs/activates the environment on the
        compute node. It first tries the configured conda module; if conda is
        not available on the cluster it falls back to ``module load`` of the
        Python module and ``pip install --user``.
        """
        files = self.detect_dependency_files()
        if use_requirements is None:
            use_requirements = files["requirements"]
        if use_environment_yml is None:
            use_environment_yml = files["environment_yml"]

        # If environment.yml is present it takes precedence for conda; do not
        # also run pip install -r requirements.txt to avoid duplicate/conflicting
        # installs. In pip-fallback mode we only use requirements.txt.
        if use_environment_yml:
            use_requirements = False

        yml_name = self._env_name_from_yml() if use_environment_yml else None
        env_name = yml_name or self.conda_env

        lines: List[str] = []
        lines.append('echo "[ulhpc-submit] configuring environment on compute node"')

        for module in self.runtime_modules:
            lines.append(f"module load {module}")

        if not self.use_conda:
            if not self.runtime_modules and self.python_module:
                lines.append(f"module load {self.python_module}")
            if files["requirements"]:
                lines.append(
                    f"{self.python_executable} -m pip install --user -r requirements.txt"
                )
            lines.append('echo "[ulhpc-submit] environment ready"')
            return "\n".join(lines)

        # Probe whether the requested conda module exists on this cluster.
        lines.append(f"if module load {self.conda_module} 2>/dev/null && command -v conda >/dev/null 2>&1; then")
        lines.append('    echo "[ulhpc-submit] using conda environment"')
        lines.append("    set +u  # avoid conda uninitialized-variable errors")
        lines.append('    source "$(conda info --base)/etc/profile.d/conda.sh"')
        lines.append("    set -u")
        lines.append(
            f'    if ! conda env list | grep -qE "^\\s*{env_name}\\s+"; then'
        )

        if use_environment_yml:
            lines.append("        conda env create -f environment.yml")
        else:
            lines.append(
                f"        conda create -n {env_name} python={self._python_version()} -y"
            )

        lines.append("    else")

        if use_environment_yml:
            lines.append("        conda env update -f environment.yml --prune")
        elif files["requirements"]:
            lines.append(
                f"        conda run -n {env_name} pip install -r requirements.txt"
            )

        lines.append("    fi")
        lines.append(f"    conda activate {env_name}")

        if use_requirements:
            lines.append("    pip install -r requirements.txt")

        # Fall back to pip with the configured Python module when conda is absent.
        lines.append("else")
        lines.append(
            '    echo "[ulhpc-submit] WARNING: conda not available; falling back to pip. '
            'Some packages may differ from the conda environment."'
        )
        lines.append(f"    module load {self.python_module}")
        if files["requirements"]:
            lines.append(
                f"    {self.python_executable} -m pip install --user -r requirements.txt"
            )
        lines.append("fi")

        lines.append('echo "[ulhpc-submit] environment ready"')
        return "\n".join(lines)

    def _python_version(self) -> str:
        """Extract major.minor from python_module string."""
        # e.g. lang/Python/3.11 -> 3.11
        parts = self.python_module.replace("/", " ").split()
        for part in reversed(parts):
            if "." in part and part.replace(".", "").isdigit():
                return part
        return "3.11"

    def validate_local_dependencies(self) -> None:
        """Check that local dependency files are non-empty if present."""
        req = self.project_dir / "requirements.txt"
        env = self.project_dir / "environment.yml"
        for path in (req, env):
            if path.exists() and path.stat().st_size == 0:
                raise EnvDependencyError(
                    f"Dependency file {path.name} exists but is empty."
                )
