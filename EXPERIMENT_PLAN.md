# smallgameagent 实验方案（P7 修订版）

> 2026-07-21 修订。在已完成 P0–P6 的基础上，进入「多游戏自动跑测 + 多 Provider 混合 + 规则在线更新调优」阶段。

## 已完成摘要

- **多 Provider 云端 API**：`.env` 配置 + `MultiProviderClient` 支持 OpenCodeGo / Kimi / DeepSeek / MiMo / Qwen。
- **三层架构**：L0 规则引擎、L1 本地小 VLM、L2 云端多模态 API；规则在线更新（保守方案 A）已落地。
- **Agent 通信与记忆**：`AgentBus` + `StrategyMemory` + Critic/Verifier 循环；记忆读回 A/B 验证 composite 0.150 → 0.300。
- **训练数据管线**：`trajectory_converter.py` 已产出 34,150 条样本，覆盖 22 游戏、7 任务。
- **报告/PPT**：`scripts/generate_deliverables.py` 已修复 LibreOffice 颜色渲染，报告已推送 GitHub 并复制到 Windows 下载目录。

## P7 目标

1. **多游戏自动跑测**：用 `src/experiments/batch_runner.py` 在 22 游戏上跑出代表性/完整矩阵。
2. **多 Provider 对比**：对同一视觉/文本决策任务，比较 Kimi / MiMo / DeepSeek / Qwen 的延迟、成本、结构化输出准确率。
3. **规则在线更新调优**：解决 hierarchical 模式 activity=0、multi-bus-memory 记忆未预热问题；引入触发阈值、Verifier 可行性检查、L2 输出目标名称契约。
4. **数据资产沉淀**：每次批量跑测自动转换 trajectory → VLM 训练数据，持续扩充数据集。

## 1. 批量跑测快速开始

### 1.1 跑代表性子集（约 20–30 分钟）

```bash
.venv/bin/python src/experiments/exp_representative_subset.py
```

默认 6 个游戏：
- A 类（joystick）：SSD_00461P01（塔防）、SSD_00483P01（吸沙抽水）、SSD_00522P02（地下炸矿）
- B 类（tap-only）：SSD_00382P01（低坑杀鲨鱼）、SSD_00594P02（破石收水）、SSD_00742P01（加油小镇）

模式：
- A 类：rule / multi-bus / multi-bus-memory / hierarchical
- B 类：rule / multi-bus-memory

产物：
- `representative_results/A_representative/batch_results.json`
- `representative_results/B_representative/batch_results.json`
- `representative_results/batch_results_all.json`
- `representative_results/*/analysis.md`
- `representative_results/*/trajectories/*.jsonl`

### 1.2 跑完整矩阵（约 1–3 小时，取决于 API 调用量）

```bash
.venv/bin/python src/experiments/exp_full_matrix.py
```

22 游戏 × A 类 4 模式 / B 类 2 模式 × 2 seeds = 116 runs。

产物：
- `full_matrix_results/*/batch_results.json`
- `full_matrix_results/batch_results_all.json`
- `full_matrix_results/analysis_all.md`

### 1.3 自定义 batch

```python
from src.experiments.batch_runner import BatchConfig, run_batch
import asyncio

cfg = BatchConfig(
    games={"SSD_00461P01": "_extracted/games/.../SSD_00461P01.html"},
    modes=["rule", "multi-bus-memory", "hierarchical"],
    seeds=[42, 123],
    max_steps=25,
    headed=False,
    collect_dataset=True,
    output_dir="my_exp",
    memory_config={"strategy_memory_path": "my_memory.json"},
)
results = asyncio.run(run_batch(cfg))
```

### 1.4 轨迹 → 训练数据

```bash
.venv/bin/python src/training/trajectory_converter.py \
  --input representative_results \
  --output vlm-training-data-representative \
  --format chatml
```

转换后检查 `vlm-training-data-representative/dataset-manifest.json` 的 `total_samples`。

## 2. 多 Provider 对比实验

### 2.1 文本决策对比

用 `src/experiments/exp_cloud_api_matrix.py` 或自定义脚本，对同一 probe state 让不同 provider 输出 action JSON，比较：
- 首 token 延迟 / 总延迟
- JSON 合法率
- action 类型分布（move/tap/wait）
- 世界模型违例数

### 2.2 视觉 struct 对比

用 `src/experiments/exp_cloud_api_struct.py` 或 `src/experiments/exp_local_vlm_matrix.py`，对同一帧截图输出 19 字段 struct，比较字段准确率。

### 2.3 推荐配置

| 场景 | 推荐 provider | 原因 |
|---|---|---|
| 长程规划 / 规则更新 | kimi-k2.7-code | 代码/结构能力强，延迟 2–3s |
| 视觉单帧理解 | mimo-v2.5 / kimi-k2.6 | 多模态稳定，mimo 视觉 17–22s/帧 |
| 低成本文本 fallback | DeepSeek / Qwen | 按量计费，延迟低 |
| 本地实时感知 | gemma-4-e4b / qwen3.5-4b | 离线标注 + QLoRA 后可行 |

## 3. 规则在线更新下一阶段

### 3.1 已知问题

- **hierarchical activity=0**：L2 规划器倾向于输出 `wait`；需要 L2 输出目标名称（而非坐标），并由 Verifier 检查可行性。
- **multi-bus-memory 未预热**：冷记忆反而降低 composite；应先跑 phase1_write 再跑 phase2_read。
- **触发阈值固定**：应改为基于历史滑动窗口 + 游戏类型自适应。

### 3.2 下一步实验

1. **预热 memory 再测 multi-bus-memory**：先跑 1 轮 rule 或 api，把成功 tap 模式写入 strategy memory，再跑 multi-bus-memory。
2. **L2 输出契约**：强制 L2 输出 `target_name` + `action_hint`（tap/move/wait），坐标由 L1/L0 解析。
3. **Verifier 可行性检查**：Verifier 检查 L2 计划中的目标是否在当前观测中存在，不存在则触发 re-plan。
4. **触发阈值自适应**：`RuleUpdateTrigger` 加入滑动窗口，窗口长度按游戏类型（A/B）和当前阶段动态调整。
5. **代码级规则更新（激进方案 B）**：在沙盒中让 L2 生成 Python 代码补丁，经类型检查 + 单测通过后由人工确认写入 `src/engine/rules.py`。

## 4. 训练准备（等 ssh5090 可访问）

- 数据：已准备 `vlm-training-data-processed-runs/`（34,150 条）。继续用批量跑测补充轨迹后转换。
- 脚本：`train_qwen35.py` / `train_gemma4.py` 已在位；QLoRA 4-bit，优先 qwen3.5-4b。
- 连接：`~/ssh5090.sh` 可用后执行数据同步与训练。

## 5. 风险

- hierarchical / api 模式调用云端 API，费用与时延随步数线性增长；跑完整矩阵前建议先跑代表性子集。
- 浏览器实例（Playwright）在 WSL2 中串行运行，批量跑测无法并行；如需并行需多 WSL 实例或容器。
- 本地 VLM 在线推理仍慢，优先作为离线标注/上下文生成器。
