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

The easiest way to create the config file is to run the interactive setup:

```bash
ulhpc-submit --init-config
```

This writes `~/.config/ulhpc-submit/config.yaml` with restricted permissions (`600`).

Alternatively, create the file manually:

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
# Optional: require at least 1.1x the local project size to be free on the remote filesystem
sync_free_space_margin: 1.1
```

The config file is automatically restricted to owner-only access (`600`) because it may contain SSH credentials.

### Disk-space check

Before running `rsync`, `ulhpc-submit` estimates the local upload size (respecting `sync_excludes`) and queries the remote filesystem for free space. You will see output like:

```text
[ulhpc-submit] Upload size: 12.45 MiB; remote free: 45.20 GiB / 100.00 GiB (55% used)
```

If the available space is less than `local_size × sync_free_space_margin`, the submission stops before any data is transferred:

```text
[SYNC_DISK_FULL] Insufficient disk space for sync to /home/user/hpc_runs/my_exp: upload requires 13.70 MiB (1.1x margin), but only 10.00 MiB is available (99% used).
```

You can override the margin per command:

```bash
# require exactly the local project size to be free
ulhpc-submit --sync-free-space-margin 1.0 python main.py

# or via environment variable
ULHPC_SYNC_FREE_SPACE_MARGIN=1.5 ulhpc-submit python main.py
```

If the remote `df` command is unavailable or returns unexpected output, the tool prints a warning and continues the sync rather than blocking the submission.

Configuration values are resolved in this order:

1. CLI options (highest priority)
2. `ULHPC_*` environment variables, e.g. `ULHPC_USER`, `ULHPC_HOST`, `ULHPC_PORT`, `ULHPC_SSH_KEY`
3. Config file values
4. Built-in defaults

Use `--show-config` to inspect the merged configuration:

```bash
ulhpc-submit --show-config
```

### Need help?

If you have questions about UL HPC, you can log in with your UL account to the [UL HPC GPT assistant](https://webapp-unilux-unigpt-prd-we.azurewebsites.net/) and ask HPC-related questions.

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

## Platform Support

`ulhpc-submit` is designed to submit jobs to the UL HPC **Iris** Slurm cluster, which is a Linux environment. The local machine must be able to run `ssh` and `rsync`.

| Platform | Support | Notes |
|---|---|---|
| Linux | Native | Primary target. All required tools are available. |
| macOS | Native | `ssh` and `rsync` are pre-installed. |
| Windows (WSL2) | Recommended | Run `ulhpc-submit` inside WSL2 to get the full Unix toolchain (`bash`, `rsync`, `ssh`, Slurm client). |
| Windows (native) | Limited | Native Windows does not include `rsync`, and generated job scripts assume `bash`. Use WSL2 instead. |

## Troubleshooting

### Avoiding rate-limiting on repeated failed connections

When you are on the UL campus network or VPN, rapid failed SSH attempts from the same egress IP can trigger a temporary block on `access-iris.uni.lu:8022`. `ulhpc-submit` therefore defaults to **fail-fast**:

- The default `--max-ssh-retries` is **1**: the first connection failure stops immediately, minimizing the risk of being rate-limited.
- If your network is occasionally flaky and you want the tool to tolerate transient issues, explicitly enable retries:
  ```bash
  ulhpc-submit --max-ssh-retries 3 python main.py
  # or
  ULHPC_MAX_SSH_RETRIES=3 ulhpc-submit python main.py
  ```
- Before submitting, you can verify reachability with a single SSH attempt:
  ```bash
  ulhpc-submit --test-connection
  ```
  This performs a real SSH login to the access node but tries only once.
- If you see `SYNC_NETWORK_ERROR`, **stop retrying immediately**, check your VPN/SSH setup, wait before trying again, and use `--test-connection` to confirm connectivity before a full submission.

## Testing

```bash
python -m pytest tests/ -v
python validate.py
```

All heavy work (`module load`, conda, pip, user command) is executed inside the Slurm job on a compute node; the access node is only used for file sync and `sbatch` submission.
