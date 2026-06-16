# UL HPC任务自动提交模块设计实现文档

---

## 1. 技术架构

- **主语言：Python**
- **核心工具与库：**
  - paramiko（SSH远程操作）
  - rsync/scp（文件同步，subprocess 调用系统命令）
  - subprocess（本地/远端命令行交互）
  - PyYAML（环境文件解析）
  - threading/asyncio（异步状态监控）
  - logging（标准日志管理）

---

## 2. 核心功能结构

### 2.1 文件同步模块

- 用 `rsync` 增量同步本地代码到HPC远端项目目录
- 同步前后，自动核查：
  - 目录空间
  - 文件权限
  - 完整性校验（校验和/文件数量比对）
- 同步失败报错示例：
  - `SYNC_NETWORK_ERROR`: 网络异常
  - `SYNC_NO_PERMISSION`: 目录权限不足
  - `SYNC_DISK_FULL`: 磁盘空间不足
  - `SYNC_INTEGRITY_ERROR`: 文件缺失或校验失败

### 2.2 依赖配置管理模块

- 自动提取/上传 `environment.yml`/`requirements.txt`
- SSH远端自动执行
  - `module load miniconda`/`python`
  - 新建/激活conda环境
  - `pip install -r requirements.txt`
- 依赖安装失败报错示例：
  - `ENV_MODULE_NOT_FOUND`: module命令不存在（access节点不允许）
  - `ENV_CONDA_INSTALL_ERROR`: conda安装失败/包缺失
  - `ENV_PYTHON_VERSION_MISMATCH`: Python版本不符
  - `ENV_DEPENDENCY_ERROR`: 单独依赖拉取失败

### 2.3 作业脚本生成和提交模块

- 自动生成包含资源、环境配置、日志输出等的Slurm脚本
  - 可根据用户参数自动调整 CPU/内存/分区/时间
- SSH上传脚本
- 提交作业(`sbatch`)，自动解析作业ID
- 作业提交失败报错示例：
  - `JOB_SUBMIT_ERROR`: sbatch命令报错
  - `JOB_INVALID_RESOURCES`: 资源申请非法或超限

### 2.4 作业状态监控模块

- 使用`squeue`/`sacct`定时查询作业状态
  - 记录`PENDING`/`RUNNING`/`FAILED`/`COMPLETED`等状态变化及时间
- 排队过长报错示例：
  - `JOB_PENDING_TIMEOUT`: 作业排队超时
  - `JOB_KILLED`: 作业被系统终止
  - `JOB_NODE_ERROR`: 节点不可用
- 等待与运行时间均详细写入日志

### 2.5 日志与输出管理模块

- SSH拉取远端日志 `job.out` 回传本地（分批回传防止大日志失败）
- 日志解析细化：
  - `CODE_ERROR`: 用户代码报错（如Traceback、SyntaxError等）
  - `ENV_ERROR`: 环境配置报错
  - `HPC_RESOURCE_ERROR`: 资源不足、节点退出
  - `NETWORK_ERROR`: API/外网访问失败
  - `UNKNOWN_ERROR`: 未分类异常

### 2.6 用户接口与配置

- 命令行工具/函数接口，接受用户本地命令、远端目录、资源参数、依赖配置等
- 本地日志输出需包含：
  - 时间戳
  - 操作步骤
  - 错误类别与详细信息（如上各类报错码与原因）
  - 推荐修复建议（如“请检查目录权限/请减少资源申请/请核查python版本/请联系IT support”）

---

## 3. 风险规避措施

- **多次重试**：同步、SSH、作业提交均支持自动重试，最大5次，失败即报错
- **完整性校验**：同步后自动对比文件数/校验和
- **权限、空间、网络预检测**：每步都要先检测，再操作，确保命令不会报错
- **作业提交和监控异常捕获**：SLURM错误、资源错误、节点报错一律统一解析，写明分类
- **日志截取与结构化**：日志如过大只回传部分（最后或错误段），保证回传不堵塞
- **安全配置**：敏感信息如API key/SSH密钥仅使用安全方式传递
- **严格遵守UL HPC规范**：任何重任务都在计算节点，module命令按规范配置，不违反守则
- **接口版本提示**：如UL HPC接口变更，脚本检测并及时报错

---

## 4. 异常处理与报错规划

- 每个主流程节点都要有异常判别/try-except
- 任何异常都要写日志、反馈给用户，附推荐修复建议
- 错误码与原因并输出到日志/回显
- 譬如：
  - `SYNC_NETWORK_ERROR: 网络故障。请检查本地/校园网是否正常。`
  - `ENV_CONDA_INSTALL_ERROR: 远端依赖安装失败。请核查environment.yml与HPC支持的包。`
  - `JOB_PENDING_TIMEOUT: 作业等待超时。可减少资源申请或换分区。`
  - `CODE_ERROR: 用户代码运行异常。建议本地调试并修复。`
  - `HPC_RESOURCE_ERROR: 资源不足，作业被系统kill。请减少内存或CPU申请。`

---

## 5. 基础主流程示例（逻辑伪代码）

```python
def submit_hpc_task(command, local_dir, remote_dir, env_file, resource_params):
    try:
        sync_code(local_dir, remote_dir)
    except SyncError as e:
        log_error("SYNC", e)
        return
    try:
        configure_env(remote_dir, env_file)
    except EnvError as e:
        log_error("ENV", e)
        return
    try:
        script = gen_job_script(command, resource_params, env_file)
        submit_job(remote_dir, script)
    except JobError as e:
        log_error("JOB_SUBMIT", e)
        return
    job_id = get_job_id()
    monitor_job(job_id)
    log_and_fetch_output(job_id, remote_dir)
