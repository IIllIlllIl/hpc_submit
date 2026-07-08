# ulhpc-submit 改进需求清单

> 来源：vibe-coding-planning 项目实际使用反馈（GEPA/Apptainer 长任务场景）。
> 状态：历史需求与实现状态记录。P0/P1/P2 主项大多已实现；未实现接口在对应条目中单独标注。

---

## 背景

在 vibe-coding-planning 项目中使用 `ulhpc-submit` 提交 ULHPC Iris 上的 GEPA/Apptainer 长任务。工具已覆盖 SSH、rsync、Slurm 脚本生成、提交、日志回传等核心能力，但作为基础工具，项目侧仍需要额外编写较多 wrapper 逻辑。本文档整理这些真实使用场景下的改进需求，按优先级分类，供后续迭代参考。

当前实现状态摘要：

- 已实现：submit-only/detach、外部数据 staging、persistent output symlink、sync extra file 诊断与策略、module/system Python runtime、doctor、Apptainer cache/tmp/SIF 参数、增强 dry-run、fetch、hooks、manifest、JSON submit result、FairShare usage summary、quick-test smoke/calibrate 初版。
- 部分实现：doctor 只做 SSH、远端目录可写、module、partition 可见性检查；尚未完整校验 partition 最大 wall time、account 权限、mem/gpu 合法性。
- 未实现：quick-test GPU utilization、`--stage-data-mode`、`--stage-outside-project`、`--retrieve-output`、`--resume-output`、`ulhpc-submit validate`、`--remote-git-status`、按 Slurm 结束原因细分 hooks。
- 明确不做：quick-test 自动资源搜索、自动改写正式任务资源参数；工具只提供 utilization report 和建议，最终参数由执行人决定。

---

## 一、项目侧目前额外实现的能力（建议原生支持）

### 1. 外部大文件/数据集 staging

**当前问题**

- `ulhpc-submit` 会同步整个项目目录，且配置默认排除 `output/`。
- 正式 dataset snapshot 位于 `output/SWE-bench_Verified/verified-round1-gepa-datasets/...`，被排除后无法同步。
- 项目侧自行实现：将 dataset rsync 到远端项目目录外（如 `~/hpc_datasets/...`），运行前 symlink 回项目内预期路径，避免污染 `remote-dir` 和触发 sync integrity mismatch。

**建议接口**

```bash
ulhpc-submit --stage-data LOCAL_PATH:REMOTE_PATH --link-as PROJECT_PATH python main.py
```

状态：已实现 `--stage-data LOCAL:REMOTE` 和 `--link-as PROJECT_PATH`。未实现 `--stage-data-mode` 和 `--stage-outside-project`；当前行为固定为把外部目录同步到指定远端路径，并在提供 `--link-as` 时创建 symlink。

或配置文件支持：

```yaml
data_mounts:
  - local: output/.../dataset
    remote: ~/hpc_datasets/project/dataset
    link_as: output/.../dataset
```

### 2. 持久 run state / output staging

**当前问题**

- GEPA 的 `run_dir` 必须跨 job resume。
- 放在 `remote-dir/output/...` 会与项目同步、排除规则、完整性检查冲突。
- 项目侧自行实现：远端 run state 放到 `~/hpc_run_state/...`，运行前 symlink 到项目内配置的 `run_dir`，运行后取回；误留在 `remote-dir/output/...` 的旧 run state 还需提交前迁移出去。

**建议接口**

```bash
ulhpc-submit --persistent-output PROJECT_PATH:REMOTE_STATE_PATH python main.py
```

状态：已实现运行前创建持久远端目录并 symlink 到项目内路径。未实现 `--retrieve-output` 和 `--resume-output`；当前可用 `ulhpc-submit fetch --job-id ... --remote-dir ...` 拉取 stdout/stderr，但不负责同步任意 output 目录。

### 3. sync integrity 与 excluded 目录冲突处理

**当前问题**

- 遇到过 `SYNC_INTEGRITY_ERROR local=633 remote=696`。
- 原因是远端 `remote-dir` 里残留了被本地 exclude 的文件（如 `output/`、`__pycache__`、旧 run state）。
- 项目侧只能绕过：换新的 remote dir，或手动清理/迁移。

**建议改进**

- 明确文档说明 integrity check 的计算方式。
- 对 excluded 远端文件提供可选策略：
  - `--remote-clean-excluded`
  - `--remote-ignore-extra`
  - `--sync-strict`
- `dry-run` 时显示哪些远端 extra files 会导致失败。
- 失败信息中列出前 N 个多出来的远端文件，而不是只有 count。

状态：已实现 extra/missing 文件诊断、`--remote-ignore-extra`、`--remote-clean-excluded`、`--sync-strict` 和清理路径保护。dry-run 会展示策略和同步计划，但不会实际连接列出远端 extra files。

### 4. 远端 module / runtime setup

**当前问题**

- ULHPC Iris 计算节点没有 conda。项目侧必须生成远端脚本：
  ```bash
  module load lang/Python/3.11 tools/Apptainer
  python3 ...
  ```
- 当前 `--conda-env` 和 `--container` 对“module load + system python + project script”的路径说明不够清楚。

**建议接口**

```bash
ulhpc-submit --module lang/Python/3.11 --module tools/Apptainer --python python3 --no-conda python main.py
```

- 在 Slurm script 里规范生成 `module load` 块。

状态：已实现 `--module`、`--python`、`--no-conda`，并在 Slurm script 中生成 runtime module/env setup。

### 5. Apptainer cache/tmp/SIF 目录支持

**当前问题**

- GEPA 需要大量 Docker image 转 SIF。
- 项目侧额外管理：
  ```bash
  APPTAINER_CACHEDIR=/scratch/.../apptainer-cache
  APPTAINER_TMPDIR=/scratch/.../apptainer-tmp
  container.sif_cache_dir=/scratch/.../sif-cache
  ```

**建议接口**

```bash
ulhpc-submit --apptainer-cache-dir /scratch/.../apptainer-cache \
             --apptainer-tmp-dir /scratch/.../apptainer-tmp \
             --apptainer-sif-cache-dir /scratch/.../sif-cache \
             --container ~/images/myenv.sif python main.py
```

- 文档明确区分：最终 `.sif` 文件目录、Apptainer layer cache、Apptainer tmp 构建目录，三者不是同一件事。

状态：已实现 CLI/config 参数，并在 Slurm script 中导出 `APPTAINER_CACHEDIR`、`APPTAINER_TMPDIR`、`ULHPC_APPTAINER_SIF_CACHE_DIR`。

### 6. 长任务 submit-only / detach 模式

**当前问题**

- 对于 24h+ 作业，不希望本地 `ulhpc-submit` 进程持续监控。
- 只需要：同步、提交、打印 job id / 远端日志路径 / 远端 workdir、退出。

**建议接口**

```bash
ulhpc-submit --submit-only python main.py
ulhpc-submit --detach python main.py
```

- 保证本地进程退出不会影响 Slurm job。

状态：已实现。`--submit-only` / `--detach` 在 `sbatch` 成功后打印 job id、remote workdir、stdout/stderr 路径和查询命令，然后跳过 monitor/log fetch。

### 7. 预检查与 fail-fast

**当前问题**

- 项目侧写了 smoke wrapper 提前检查：access node TCP 连通性、SSH 是否能连、配置是否可解析、远端 module 是否存在、远端目录是否可写、Slurm partition/time 是否有效。

**建议接口**

```bash
ulhpc-submit doctor
ulhpc-submit doctor --remote-dir ...
ulhpc-submit validate --time 2-00:00:00 --partition batch
```

- 在正式提交前 fail fast，避免进入较长 Paramiko retry 或 Slurm 提交后才发现问题。

状态：已实现 `ulhpc-submit doctor`，包含 SSH、远端目录可写、module 可用、partition 可见性检查。未实现独立 `ulhpc-submit validate`；资源限制深度校验仍是后续项。

### 8. 快速测试 / 资源校准

**当前问题**

- 长任务如果因为依赖、module、container、入口参数等 1s 级错误立即失败，用户通常能很快发现；但如果任务启动后卡住或空转，可能长时间占用已申请资源。
- 低效作业仍会进入 Slurm accounting，浪费 CPU/GPU/memory allocation，并可能降低 FairShare。
- 正式任务前缺少一个标准化的短跑流程来回答两个问题：
  - 环境是否真的能在 compute node 上启动？
  - 当前 `--cpus`、`--mem`、`--time` 等资源请求是否明显过量或不足？

**建议接口**

```bash
# 10s 级环境 smoke test；wall time 可比 command timeout 稍长
ulhpc-submit quick-test smoke -- python -c "import torch; print('ok')"

# 10min 级资源校准；用户提供有代表性的短跑命令
ulhpc-submit quick-test calibrate --duration 10m -- python train.py --max-steps 100
```

可选参数草案：

```bash
ulhpc-submit quick-test smoke \
  --command-timeout 10s \
  --time 00:01:00 \
  [normal submit options] -- COMMAND

ulhpc-submit quick-test calibrate \
  --duration 10m \
  --time 00:11:00 \
  --cpu-efficiency-threshold 0.5 \
  --memory-headroom-threshold 0.25 \
  [normal submit options] -- COMMAND
```

状态：已实现初版。`quick-test smoke` 会用 in-job `timeout` 包装用户命令；`quick-test calibrate` 会在完成后查询 `sacct` 并输出 CPU efficiency、allocated core-hours、ReqMem/MaxRSS 和 sizing hints。未实现 GPU utilization、自动资源搜索、自动改写正式任务参数。

**建议测量指标**

- `state`：Slurm 最终状态，识别 `COMPLETED`、`FAILED`、`TIMEOUT`、`OUT_OF_MEMORY`、`CANCELLED`、`NODE_FAIL`。
- `elapsed`：实际运行时间，用于判断 smoke/calibration 是否达到预期。
- `allocated core-hours`：`Elapsed * AllocCPUS / 3600`，估算此次测试消耗。
- `CPU efficiency`：`TotalCPU / (Elapsed * AllocCPUS)`，识别 CPU 申请过量或并行度不足。
- `ReqMem` / `MaxRSS`：估算内存余量，识别内存申请过高或 OOM 风险。
- `stdout/stderr classification`：复用现有日志分类，识别 dependency error、code traceback、network/API error、resource error。

**建议输出**

- smoke 成功：环境、同步、runtime setup、用户短命令均可启动。
- smoke 超时：环境可能已启动，但短命令未在 timeout 内结束；建议缩短 smoke command 或检查是否卡在初始化。
- calibration 成功：输出 CPU efficiency、MaxRSS/ReqMem、allocated core-hours，并给出“保持/减少 CPU/减少内存/增加内存/缩短测试输入”的建议。
- calibration 失败：保持结构化错误码，并说明这个失败本身也会消耗少量 FairShare，但比长任务空转更可控。

**限制**

- 10s smoke test 不能证明长任务逻辑正确，只能尽早发现环境、依赖、入口、container/module 等启动失败。
- 10min calibration 的代表性取决于用户提供的短跑命令是否接近正式 workload。
- 第一版只做 CPU 和 memory accounting；GPU utilization 需要额外采集 `nvidia-smi` 或 Slurm TRES accounting，建议后置。

---

## 二、help / 文档不够清晰的地方

### 1. `--container` 的语义容易误解

**建议明确文档**

- `--container` 只包装外层用户命令。
- 如果项目代码内部需要创建 Docker/Apptainer 容器，仍需项目自身适配。
- `--container` 不等价于 Docker backend 自动迁移。

### 2. `--no-sync` 风险说明不足

**建议明确**

- `--no-sync` 使用远端已有代码。
- 远端代码版本、未提交修改、数据文件都由用户负责。
- 建议打印远端 pwd、git commit、dirty 状态，或提供 `--remote-git-status`。

状态：风险说明仍可继续加强；`--remote-git-status` 未实现。

### 3. `--dry-run` 输出应该更完整

**建议 dry-run 展示**

- 最终 Slurm script 完整内容
- rsync include/exclude 规则
- remote-dir
- log 路径
- 是否会清理/覆盖远端文件
- 是否会触发 sync integrity check
- 预计同步文件数/大小

状态：已实现最终配置、完整 Slurm script、rsync 命令、exclude 规则、remote paths、staging/persistent-output plan、预计上传大小。未实现 dry-run 中远端 extra files 的实时列举。

### 4. config schema 应明确列出

**建议提供**

```bash
ulhpc-submit config-schema
ulhpc-submit --show-config --explain
```

- 完整列出 config YAML 支持的字段、默认值、环境变量映射。

状态：已实现。

### 5. 资源参数的 ULHPC 限制应该更早暴露

**当前问题**

- 提交 `--time 3-00:00:00` 时 Slurm 返回 time limit invalid。

**建议改进**

- 提交前检查或给出更清楚提示：当前 partition 最大 wall time、当前用户/account 是否允许该 partition、time/mem/gpu 请求是否合法。

状态：部分实现。`doctor` 可检查 partition 是否可见；具体 wall time/account/mem/gpu 限制仍依赖 Slurm 返回错误或后续增强。

---

## 三、建议新增的功能接口（按优先级）

### P0（最希望删除项目侧 wrapper）

- [x] `--submit-only` / `--detach`
- [x] `--stage-data local:remote --link-as project_path`
- [x] `--persistent-output project_path:remote_path`
- [x] 更清晰的 sync integrity error，列出 extra files
- [x] `--module` / `--no-conda` / `--python` 支持

### P1

- [x] `ulhpc-submit quick-test smoke`：10s 级环境启动测试，避免长任务因启动错误或卡住而空转
- [x] `ulhpc-submit quick-test calibrate`：10min 级短跑资源校准，基于 `sacct` 给出 CPU/memory sizing 建议
- [x] `ulhpc-submit doctor`
- [x] Apptainer cache/tmp/SIF 参数
- [x] dry-run 输出完整 Slurm script 和 rsync plan
- [x] remote extra file 清理策略（`--remote-clean-excluded` 等）
- [x] remote output retrieve 单独命令，例如 `ulhpc-submit fetch --job-id ...`

---

## 四、分阶段实施计划

### 阶段 1：长任务 submit-only / detach 模式（已实现）

目标：支持 24h+ 作业提交后本地进程立即退出，不再持续监控或拉取日志。

- 增加 `--submit-only` / `--detach` CLI 参数。
- 保持同步、环境检查、Slurm 脚本生成、脚本上传、`sbatch` 提交流程不变。
- `sbatch` 成功后打印 job id、远端 workdir、远端 stdout/stderr 日志路径、常用查询命令。
- 跳过 `JobMonitor` 和 `LogManager`，本地进程退出不影响 Slurm job。

依赖：基本独立，是后续长任务能力的基础。

### 阶段 2：远端 module / runtime setup（已实现）

目标：支持 ULHPC Iris 上常见的 `module load + python3` 运行模式，降低对 conda 的假设。

- 增加可重复 `--module`。
- 增加 `--python`。
- 增加 `--no-conda`。
- Slurm script 中生成清晰的 module load 块。
- 文档明确 `--conda-env`、`--container`、`--module` 的关系。

依赖：应早于 Apptainer 参数和 doctor 的 module 检查。

### 阶段 3：sync integrity 错误可诊断（已实现）

目标：遇到完整性检查失败时能知道具体差异，而不是只有文件数量。

- `SYNC_INTEGRITY_ERROR` 中列出前 N 个远端 extra files。
- 区分本地缺失、远端多余、exclude 规则导致的远端残留。
- 文档说明 integrity check 的计算方式。

依赖：应早于远端清理/忽略策略，先保证诊断准确。

### 阶段 4：外部数据 staging（已实现）

目标：支持大文件/数据集独立同步到项目目录外，并在运行前链接回项目内预期路径。

- 增加 `--stage-data LOCAL:REMOTE`。
- 增加 `--link-as PROJECT_PATH` 或配置文件 `data_mounts`。
- staging 同步与主项目同步分离。
- 远端运行前创建 symlink。
- staging 路径不参与普通项目 sync integrity mismatch。

依赖：依赖阶段 3 对同步边界和完整性错误的澄清。

### 阶段 5：持久 output / run state（部分实现）

目标：支持跨 job resume 的 run state，不把长期输出状态混进普通代码同步目录。

- 增加 `--persistent-output PROJECT_PATH:REMOTE_PATH`。
- 运行前把远端 state symlink 到项目内路径。
- 未实现：运行后可选 retrieve、`--resume-output`、旧 state 自动迁移提示。

依赖：可复用阶段 4 的 symlink、路径校验和独立同步设计。

### 阶段 6：dry-run 增强（已实现）

目标：提交前完整展示将要发生的远端操作。

- 显示完整 Slurm script。
- 显示最终配置。
- 显示 rsync include/exclude 规则。
- 显示 remote-dir、日志路径、预计同步大小。
- 显示 data staging、persistent output、symlink 操作计划。

依赖：建议在阶段 4/5 后集中完善，避免重复改 dry-run 输出。

### 阶段 7：doctor / validate（部分实现）

目标：正式提交前 fail-fast，减少长时间等待后才发现配置或资源错误。

- 增加 `ulhpc-submit doctor`。
- 检查 access node TCP/SSH 连通性。
- 检查远端目录是否可写。
- 检查 module 是否存在。
- 已实现 partition 可见性检查。
- 未实现：独立 `validate` 子命令、partition 最大 wall time/account/mem/gpu 深度校验。

依赖：依赖阶段 2 的 runtime/module 模型，也可复用阶段 6 的配置展示。

### 阶段 8：Apptainer cache/tmp/SIF 参数（已实现）

目标：原生支持容器长任务常用缓存和临时目录。

- 增加 `--apptainer-cache-dir`。
- 增加 `--apptainer-tmp-dir`。
- 增加 `--apptainer-sif-cache-dir`。
- Slurm script 中导出相关环境变量。
- 文档区分 layer cache、tmp、SIF cache。

依赖：依赖阶段 2 的 module/runtime 支持。

### 阶段 9：远端 extra 文件策略（已实现）

目标：在诊断清楚后，提供可控的忽略或清理策略。

- 增加 `--remote-ignore-extra`。
- 增加 `--remote-clean-excluded`。
- 增加 `--sync-strict`。
- dry-run 中展示策略和同步计划；未实现实时列出远端将忽略或清理的文件。
- 清理前增加路径保护，避免误删 remote-dir 外内容。

依赖：强依赖阶段 3 和阶段 6，风险高，后置实现。

### 阶段 10：输出和元数据能力（已实现）

目标：提升自动化集成和长期可维护性。

- 增加 job metadata manifest。
- 增加 structured JSON output。
- 增加 `ulhpc-submit fetch --job-id ...`。
- 增加 Slurm hooks。
- 增加 `config-schema` / `--show-config --explain`。

依赖：适合在核心提交、同步、状态管理能力稳定后实现。

### 阶段 11：quick-test smoke / calibrate（已实现初版）

目标：在正式长任务前提供两个低成本检查：

- `quick-test smoke`：提交极短 Slurm job，用 in-job timeout 包装用户命令，尽早暴露环境、依赖、container/module、入口参数错误。
- `quick-test calibrate`：提交短时间代表性 workload，完成后用 `sacct` 分析资源利用率，给正式任务的 `--cpus`、`--mem`、`--time` 提供建议。

第一版范围（已实现）：

- 新增 `ulhpc-submit quick-test smoke [normal submit options] -- COMMAND`。
- 新增 `ulhpc-submit quick-test calibrate [normal submit options] -- COMMAND`。
- smoke 默认 command timeout 为 `10s`，默认 Slurm time 为 `00:01:00`。
- calibrate 默认 command timeout 为 `10m`，默认 Slurm time 为 `00:11:00`，让 in-job timeout 有机会先于 scheduler walltime 触发。
- 复用现有同步、staging、persistent output、runtime setup、submit、monitor、log fetch、manifest。
- 在 Slurm script 中使用 `timeout` 包装用户命令，而不是依赖本地进程超时。
- 完成后查询 `sacct -j <jobid>`，复用 `usage.py` 的 duration、CPU efficiency、core-hours、ReqMem/MaxRSS 解析能力。
- 输出人类可读建议；`--json` 时保留现有 pipeline JSON 在 stdout，quick-test 人类可读分析写入 stderr。

第一版不做（仍未实现）：

- 自动修改用户正式任务的资源参数。
- GPU utilization 采集和建议。
- 多轮二分/搜索最优资源量。
- 对正式命令自动推断 `--max-steps` 或小数据集参数；用户需要提供代表性短跑命令。
- quick-test 专属结构化 calibration JSON；当前 `--json` 只保证不污染 pipeline JSON stdout。

建议验收标准：

- smoke 成功时返回 0，并显示 job id、运行状态、日志路径、耗时和“环境可启动”结论。
- smoke 因 dependency/code error 失败时返回非 0，并复用现有错误分类。
- smoke 因 command timeout 结束时明确区分“命令超时”与 Slurm walltime timeout。
- calibrate 成功时显示 CPU efficiency、allocated core-hours、requested memory、MaxRSS 和建议。
- calibrate 在 CPU efficiency 低于阈值时建议减少 CPU 或提升并行度。
- calibrate 在 MaxRSS 明显低于 ReqMem 时建议降低内存申请；OOM 时建议增加内存。
- 所有远程 shell 参数必须 quote；job id 仍用白名单校验。

### P2

- [x] Slurm job template hooks：
  - `--pre-sync-command`
  - `--pre-run-command`
  - `--post-run-command`
  - `--on-failure-command`
- [x] job metadata manifest：
  - local commit
  - remote dir
  - job id
  - submit time
  - Slurm script path
  - stdout/stderr path
  - sync excludes
- [x] structured JSON output：方便 wrapper 解析 job id 和路径，避免 grep stdout

---

## 四、安全建议

`ulhpc-submit` 应明确禁止或强烈警告以下做法：

- 把 API key 放入 command line
- 把 key 写入生成的 Slurm script
- 把本地 `.env` 自动 rsync 到远端
- 在 dry-run 输出中打印 secret
- 把 token 写入 Git remote URL 或日志

**建议安全模式**

```bash
ulhpc-submit --remote-env-file ~/.config/project/env \
             --require-remote-env KEY \
             --redact-env DEEPSEEK_API_KEY,OPENAI_API_KEY,ANTHROPIC_API_KEY \
             python main.py
```

语义：

- env file 必须已经在远端存在
- 不通过 rsync 上传
- 不打印内容
- Slurm script 只写 `source <path>` 和 `test -n "$KEY"`

---

## 五、如果实现上述能力，项目侧可删除的逻辑

- dataset 手动 rsync 到 `~/hpc_datasets`
- run_dir 手动迁移到 `~/hpc_run_state`
- remote `output/` 清理和 symlink
- access node SSH/TCP preflight wrapper
- module load 远端脚本模板
- Slurm script 路径和日志路径解析
- sync integrity mismatch 的规避逻辑
- Apptainer cache/tmp 环境变量注入

---

## 总体原则

`ulhpc-submit` 不需要理解 GEPA，但应该原生支持以下通用能力：

> **项目代码同步 + 外部数据 staging + 持久输出目录 + module runtime + submit-only + structured metadata**

这些是很多 HPC 项目都会需要的通用能力。
