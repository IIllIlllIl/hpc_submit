# UL HPC Auto-Submission Module

A local Python CLI that makes running code on the UL HPC **Iris** cluster feel like running it locally.

## Quick start

```bash
# install
pip install -e ".[dev]"

# run a local command on HPC
ulhpc-submit python main.py

# GPU job example
ulhpc-submit --partition gpu --gpus 1 --time 02:00:00 train.py
```

## Configuration

Create `~/.config/ulhpc-submit/config.yaml`:

```yaml
host: access-iris.uni.lu
port: 8022
user: YOUR_USERNAME
remote_project_dir: ~/hpc_runs/{project_name}
default_partition: batch
default_time: "01:00:00"
conda_module: miniconda3
sync_excludes:
  - ".git"
  - "__pycache__"
```

The config file is automatically restricted to owner-only access (`600`) because it may contain SSH credentials.

## Common submission modes

### 1. Plain pip / `requirements.txt`

If your project has a `requirements.txt`:

```bash
ulhpc-submit --time 01:00:00 --cpus 4 python main.py
```

`ulhpc-submit` first tries to use the configured conda module. If conda is not available on the cluster, it automatically falls back to `module load` the configured Python module and runs `pip install --user -r requirements.txt`.

### 2. Conda via `environment.yml`

If your project has an `environment.yml`:

```bash
ulhpc-submit --time 02:00:00 python main.py
```

The generated job script reads the environment name from `environment.yml`, creates the environment if it does not exist, and updates it on subsequent runs. `environment.yml` takes precedence over `requirements.txt`; when it is present, `requirements.txt` is ignored to avoid conflicting installs.

### 3. Apptainer / Singularity container

If you have a `.sif` image on the cluster:

```bash
ulhpc-submit --container ~/images/myenv.sif --time 01:00:00 python main.py
```

The user command is wrapped as `apptainer exec ~/images/myenv.sif python main.py` inside the Slurm script. If your command already starts with `apptainer` or `singularity`, it is passed through unchanged.

## Dry-run

Inspect the generated Slurm script and rsync command without submitting:

```bash
ulhpc-submit --dry-run python main.py
```

## Monitoring and logs

After submission, `ulhpc-submit` polls the job state. If the job remains `PENDING` for several minutes, it prints periodic hints to the CLI. When the job finishes, remote `job_%j.out` and `job_%j.err` are merged into a local run log under `~/.local/share/ulhpc-submit/runs/`.

Use `--full-logs` to download the complete remote output files instead of tailing the last 500 lines.

## Testing

```bash
python -m pytest tests/ -v
python validate.py
```

All heavy work (`module load`, conda, pip, user command) is executed inside the Slurm job on a compute node; the access node is only used for file sync and `sbatch` submission.
