# smallgameagent 大规模实验路线图（2026-07-20 版）

> 目标：把当前 22 游戏矩阵扩展到「多 Provider 云端 API + 本地小 VLM + 规则在线更新 + Agent 通信与记忆 + 训练数据闭环」的完整实验体系，最终输出可汇报的 PDF/PPT 并推送 GitHub。

---

## 1. 当前已完成基线

| 阶段 | 状态 | 关键产出 |
|---|---|---|
| P1 多游戏可驱动 | 完成 | 22 游戏自动分类 A/B/C，generic / tap-only 驱动 |
| P2 批量实验框架 | 完成 | `batch_runner.py` + `analyze_batch.py` 支持多游戏 × 多模式 × 多 seed |
| P3 A 组 7 游戏 × 3 模式 × 2 seeds | 完成 | `full_matrix_results/A_full/` |
| P4 B 组 15 游戏 × 2 模式 × 2 seeds | 进行中 | `full_matrix_results/B_tap/`（预计 60 runs） |
| P5 多 Provider API 配置 | 完成 | `.env` 接入 OpenCodeGo / MiMo / Kimi / DeepSeek / Xiaomi / Qwen |
| P6 规则在线更新框架 | 完成 | 触发器 + 结构化输出 + 可选代码文件重写 + 待审队列 |
| P7 本地 VLM 推理准备 | 完成 | LM Studio 启动脚本 + KV-cache 量化参数 |
| P8 训练数据管线 | 完成 | `trajectory_converter.py` + `merge_vlm_datasets.py` |
| P9 报告/PPT 生成 | 完成 | `scripts/generate_deliverables.py` 输出 PDF/PPT |

---

## 2. 下一阶段实验矩阵

### 2.1 22 游戏全量矩阵收尾与数据分析

- **动作**：等 `bash-6ye5976y` 跑完 B_tap（60 runs）。
- **产出**：
  - `full_matrix_results/B_tap/batch_results.json`
  - `full_matrix_results/B_tap/analysis.md`
  - 转换 B_tap 轨迹 → `vlm-training-data-B-tap/`
  - 合并全部数据集 → `vlm-training-data-merged/`
- **决策**：识别哪些 B 类游戏 rule 即可满分、哪些需要 multi-bus-memory、哪些 activity=0 需要 profile 调优。

### 2.2 多 Provider 云端 API 可用性实验

| Provider | 模型 | 当前状态 | 实验动作 |
|---|---|---|---|
| OpenCodeGo | mimo-v2.5 | 余额不足 | 充值或改用测试额度后重测 |
| Kimi | kimi-k2.7-code / k2.6 多模态 | 404 模型名 | 确认正确模型名后重测 |
| DeepSeek | deepseek-chat / deepseek-reasoner | 余额不足 | 充值后重测 |
| Xiaomi | mimo-v2.5 | 可用 | 跑 3 个代表性游戏，记录 latency、JSON 稳定性、composite |
| Qwen | token-plan 兼容接口 | 模型名 404 | 换 `qwen-vl-max` / `qwen2.5-vl-7b-instruct` 等名重测 |

- **实验脚本**：新增 `src/experiments/exp_cloud_providers.py`，每个 provider 跑 3 游戏 × 2 模式 × 1 seed，输出 `cloud_provider_results.json`。
- **评价指标**：
  - 可用率（非余额/404 错误）
  - 平均 latency（首 token / 完整 JSON）
  - JSON 解析成功率
  - composite 与 rule 基线对比

### 2.3 规则在线更新触发器实验

- **目标**：验证「底层规则 + 上层按需更新」的三段结构。
- **实验设计**：
  1. 选择 3 个 A 组游戏（00461、00483、00522）和 2 个 B 组游戏。
  2. 初始规则故意设置次优参数（例如「有钱就升级」）。
  3. 运行 30 步，触发器检测 stall / composite 低 / L0-L2 冲突。
  4. L2 输出结构化更新（`param` / `memory_entry` / `phase_contract` / `code_file`）。
  5. 比较更新前后 composite 变化。
- **产出**：`rule_update_results.json` + `rule_update_examples.md`。
- **风险控制**：代码文件改写走 allowlist + 置信度 ≥0.9 + 备份 + 待审队列；默认只改内存参数。

### 2.4 本地 VLM 常驻与 Hierarchical 协同实验

- **环境**：5060 Laptop 8GB 独显 + Vulkan / CUDA。
- **模型**：Qwen3.5-4B / Qwen3.5-9B / Gemma-4-E4B，4-bit NF4 + KV-cache 量化（q4_0 / q8_0）。
- **启动**：`scripts/launch_local_vlm.sh` 启动 LM Studio 本地 server。
- **实验**：
  - L1 每 5 步截图判断当前状态（next_probe_action / information_gain_judgment）。
  - L2 每 15 步用云端 API 做长程规划。
  - 对比 `hierarchical` vs `rule` vs `multi-bus-memory`。
- **指标**：composite、activity、L1/L2 调用次数、单步延迟。

### 2.5 训练数据采集与 QLoRA 微调准备

- **自动采集**：`scripts/collect_training_data.py` 批量跑游戏并转换样本。
- **任务定义**（与同学 Node.js 对齐）：
  1. `next_probe_action`：给定 probe state，预测下一步动作。
  2. `probe_action_effect`：预测动作后的 keyNumbers 变化。
  3. `information_gain_judgment`：判断某节点是否值得读取。
  4. `target_state_description`：用文字描述当前目标状态。
  5. `state_change_explanation`：解释前后帧状态变化原因。
  6. `action_justification`：为某个动作生成理由。
  7. `tap_screen_position`：从截图预测 tap 坐标。
- **合并数据集**：`scripts/merge_vlm_datasets.py` 合并 processed-runs / representative / A_full / B_tap，去重后统计。
- **QLoRA 脚本**：`src/training/qlora_vlm.py` 已准备，等 5090 可 SSH 后运行；当前先用默认 4-bit VLM 推理。

### 2.6 Agent 通信与记忆策略实验

- **目标**：把 6 角色总线（Observer / StateMapper / DecisionAnalyst / Verifier / Critic / MemoryCurator）从概念落地为可测量收益。
- **实验变量**：
  - 有/无 MemoryCurator（记忆读回）。
  - 有/无 Critic（失败回溯）。
  - 有/无 Verifier（动作执行前校验）。
- **指标**：composite、wm_violations、fail_flips、stall_steps。
- **产出**：`agent_ablation_results.json`。

### 2.7 报告/PPT 持续更新

- 每完成一个阶段，更新 `REPORT.md`、`EXPERIMENT_RESULTS.md`。
- 运行 `scripts/generate_deliverables.py` 重新编译 PDF/PPT。
- 推送到 GitHub，并复制到 `/mnt/c/Users/tzh03/Downloads/`。

---

## 3. 时间计划（建议）

| 周次 | 重点任务 |
|---|---|
| W1 | B_tap 收尾、多 Provider 实验、规则更新实验 |
| W2 | 本地 VLM 常驻 + hierarchical 实验、训练数据合并 |
| W3 | Agent ablation、QLoRA 微调（5090 可用后） |
| W4 | 报告/PPT 定稿、GitHub 推送、Windows 下载目录同步 |

---

## 4. 关键决策点

1. **多 Provider 预算**：OpenCodeGo / DeepSeek 余额不足，是否充值？建议先用 Xiaomi MiMo 跑通实验，再逐步接入 Kimi / Qwen。
2. **本地 VLM 模型选择**：Gemma-4-E4B 输出稳定但慢；Qwen3.5-4B 快但需更强 prompt。建议两个都跑，对比 latency/准确率。
3. **规则代码文件重写**：当前是保守方案（高阈值 + 待审队列）。是否开放自动 apply？建议先保持保守，避免运行时破坏规则引擎。
4. **5090 训练**：无法 SSH 时先准备数据；可连接后立即启动 QLoRA。

---

## 5. 可立即执行的下一件事

等 `bash-6ye5976y` 完成后：

```bash
.venv/bin/python src/training/trajectory_converter.py \
  --input-dir full_matrix_results/B_tap/trajectories \
  --output-dir vlm-training-data-B-tap

.venv/bin/python scripts/merge_vlm_datasets.py \
  vlm-training-data-processed-runs \
  vlm-training-data-representative \
  vlm-training-data-A-full \
  vlm-training-data-B-tap \
  --output vlm-training-data-merged
```

然后更新 `REPORT.md` 第 6.2 节、第 9/17 节，重新生成 PDF/PPT，推送并复制到 Windows 下载目录。
