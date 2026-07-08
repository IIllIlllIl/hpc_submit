# TODO

This file tracks deferred items and explicit non-goals after the first quick-test implementation.

## Deferred

- GPU utilization reporting for Iris GPU jobs.
  - Status: future work.
  - Context: Iris has GPU nodes, but the current project does not need GPU workflows yet.
  - Possible future scope: collect `nvidia-smi` samples or Slurm TRES accounting for GPU smoke/calibration jobs.
  - Current behavior: quick-test reports CPU and memory utilization only.

- Richer quick-test JSON output.
  - Status: future work.
  - Current behavior: `--json` preserves the existing submission pipeline JSON on stdout, while quick-test analysis is printed to stderr.
  - Possible future scope: add a structured quick-test report with CPU efficiency, memory usage ratio, hints, and timeout classification.

- Deeper Slurm resource validation.
  - Status: future work.
  - Current behavior: `doctor` checks SSH, remote directory writability, module availability, and partition visibility.
  - Possible future scope: validate partition wall-time limits, account/QoS access, GPU availability, and memory limits before submission.

## Non-Goals

- Automatic resource search.
  - Decision: do not implement for now.
  - Reason: choosing representative workloads and interpreting tradeoffs is the operator's responsibility.
  - Expected tool behavior: provide utilization reports and actionable hints, not automated resource exploration.

- Automatic production parameter tuning.
  - Decision: do not implement for now.
  - Reason: changing `--cpus`, `--mem`, `--gpus`, or `--time` automatically could be unsafe or misleading.
  - Expected tool behavior: report CPU efficiency, memory usage, allocated core-hours, and sizing suggestions; the operator decides final parameters.
