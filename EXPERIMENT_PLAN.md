# smallgameagent 下一步实验方案

> 2026-07-17 制定。目标：围绕合作同学提出的四个问题（空间一致性、时间一致性、策略优化探索、效率），
> 在「云端 LLM（kimi-k2.7-code / mimo-v2.5）+ 本地 VLM（LM Studio / Vulkan / Intel 核显）+ 纯规则」
> 三条决策路线上逐步实验、修正、沉淀报告。

## 0. 现状结论（侦察摘要）

### 环境
- 本机为 WSL2（Ubuntu 26.04，16 核 / 15GB RAM）。Vulkan 1.4 可用，GPU0 = Mesa Dozen
  桥接的 Intel Graphics（vendorID 0x8086）——即核显 Vulkan 路径成立，无 NVIDIA GPU。
- LM Studio 已安装（`/usr/bin/lm-studio`，`~/.lmstudio` 5.9GB），**尚无已下载模型**；
  `lms` CLI 需 LM Studio 守护进程运行才能认证。
- 云端 API 已打通：`https://opencode.ai/zen/go/v1`（OpenAI 兼容），凭据位于
  `~/.local/share/opencode/auth.json` 的 `opencode-go.key`。可用模型含
  `kimi-k2.7-code`（思考型，已实测对话成功）、`mimo-v2.5`（视觉）、`deepseek-v4-flash` 等 20 个。
- 仓库 `OpenCodeGoClient` 与该端点天然兼容，仅需 `OPENCODE_API_KEY` 环境变量。

### 资产
- 游戏本体：根目录 `SSD_00461P01_EN_WNK_...塔防...html`（老师给的网站同源游戏）+
  `_extracted/games/` 22 个 tiles-survive 游戏（含 merged.json 标注 + 玩法说明 md + 术语表）。
- 训练数据：`processed-runs.rar`（790MB，22 游戏各 1 run，共 3054 步，截图/state/action/delta/answer
  五件套，schema `dataset_workflow.*`，2026-07-07/08 生成）——最新的 stage2 VLM 数据集。
- 旧冷启动数据目录（20260608）本地不存在；训练脚本 `train_qwen35.py` / `train_gemma4.py` 在位。
- Qwen3.5-4B QLoRA 已在 ssh5090 训过（eval loss 1.549，LoRA r=8）；Gemma-4 受 transformers bug 阻塞。

### 代码关键空白（侦察发现，须修）
1. `probe_adapter.py:23` `DEFAULT_PROBE_PATH` 硬编码 `/home/azuma/delivery/delivery/...` 绝对路径。
2. `visual_analyzer.analyze()` 是 async 且收文件路径，但 `hybrid_agent.py:509` /
   `decision_makers/rule_maker.py:50` 同步调用并传 PIL Image —— rule 模式视觉输入恒为 None。
3. `inference/server.py:978` `kwargs.pop(device_map, None)` 缺引号 → NameError。
4. `engine/strategies/` 空目录；`taskguide/target-arrow/guide-follow` 三种 driver 无专属策略。
5. Mode 4/5/7 提取的 `RuleSet.rules` 不进执行路径（仅 driver_type 生效）。
6. 无空间/时间一致性机制；`ProceduralMemory._archive_low_performance` 无调用点。

## 1. 四个问题 → 机制映射

| 问题 | 根因（来自汇报与复盘材料） | 实验机制 |
|---|---|---|
| ① 空间一致性 | 旧版本事实驱动计划（autoFishing 266 步翻转 12 次）；场景变化后旧路径/旧交互仍被使用 | **版本化世界模型**：每个实体/能力记录 `entity_version`、场景 `scene_epoch`、能力 `capability_epoch`；观测写入时比对版本，失配则把依赖旧版本的路径与交互方式标 stale，仅局部重规划 |
| ② 时间一致性 | 观测/结算/验证三时刻未对齐（相邻帧温度 73%→36%；68.9% 步骤 decision 与 delta 字段偏差>0.05）；后续策略改写先前策略功能 | **三层时间对齐**（event_time / observed_at / settled_at + source/unit 比较键）+ **阶段契约**（precondition + allowed_actions + success_predicate + timeout）+ 受保护前缀 hash 锁定，commit gate 验收；失败按副作用分级 rollback / compensation / stop+replan |
| ③ 策略优化探索 | 按部就班、拿到钱就升级、来回折腾；约束紧（温度）时暴露 | **机制发现先行**：先定位机制（如返程触发器），再优化阈值；失败保留多个竞争假设，每轮只做区分假设的最小实验（单变量）；经济类抽象：**攒钱批量升级**（batch-upgrade），约束类抽象：动态安全边界 `T_now + r·ETA + margin ≥ T_limit → RETURN` |
| ④ 效率 | 截图 P50 397ms 且占日志 88.7%；探针全量读取浪费；日志时延 | **动态探针预算**：默认低成本状态探针，仅在触发器（phase_change / confidence<τ / action_no_effect / collision_hint / semantic_flip）命中时升级截图/深读；**动态日志分级**（DEBUG 环形内存，INFO 落盘，触发器命中时临时提升级别）；**回退分级**：行为级→状态级→策略级 |

RL 参考：借鉴 verifiers-v1 的 Environment+Rubric 抽象，把游戏 run 封装为
`env(step) → (obs, rubric_scores)`，rubric 覆盖通关/步数效率/资源效率/一致性违例计数，
为后续在线 RL 微调打底。

## 2. 实验阶段

### P0 工程修复与基线（本机，1–2 天）
- 修复 §0 代码空白 1–3（probe 路径改为包内 vendored / 环境变量；rule 视觉调用改 async 路径；
  server.py:978 补引号）。补对应单测。
- 基线 A：rule 模式跑 SSD_00461 塔防（本地 HTML），100 步，记录成功率/步数/时延。
- 基线 B：api 模式接入 kimi-k2.7-code（文本决策）+ mimo-v2.5（视觉），跑同一游戏 30 步。
  对照旧 deepseek-v4-flash 基线（29s/步）。
- 验收：pytest 全绿；两条基线产出 `experiment_baseline_*.json`。

### P1 一致性机制（核心，2–4 天）
- 在 `src/agent/` 新增 `world_model.py`：版本化世界模型（实体表 + epoch 计数 + stale 传播 +
  局部重规划接口），写单测覆盖「能力翻转→旧交互 stale→只局部重规划」场景。
- 新增 `phase_contract.py`：阶段契约数据结构 + 三层时间戳对齐 + commit gate + 回退分级执行器。
- 接入 HybridAgent 决策层（先 rule 与 api 两模式），在 00594（温度限制）与 00496（自动化翻转）
  类游戏上 A/B：基线 vs 一致性机制。指标：步数、墙钟、stale 引导次数、阶段违例回退成功率。
- 预期对照汇报数据：小场景 71/72/71 步 vs 81 步基线（−11.9% 步数、−36.4% 墙钟）。

### P2 策略优化探索（2–3 天）
- 实现 `strategy_optimizer.py`：机制发现（从轨迹聚类资源/升级事件）→ 竞争假设集 →
  单变量最小实验生成器 → 策略参数更新（如升级批量阈值、返程 margin）。
- 在经济循环类游戏（00853 2D 经济 + 1 个 follow-guide 游戏）上对照：
  朴素「够钱就升」 vs 「攒钱批量升」。指标：单位时间进度、往返步数、温度类游戏的存活率。
- prompt 侧：为 kimi-k2.7-code 写引导式 system prompt（先抽象机制再决策），验证
  「加以引导后探索最优解能力强」的观察。

### P3 效率机制（1–2 天，与 P1 部分并行）
- `probe_budget.py`：探针分级（L0 状态摘要 / L1 组件快照 / L2 截图+VLM），触发器状态机，
  预算计数与统计。动态日志分级并入同一触发器。
- 在 3 个不同后端复杂度游戏上测：平均步时延、截图占比、日志体积、决策质量回归（不降为限）。

### P4 本地 VLM（1–2 天，等模型下载）
- LM Studio 下载 qwen3.5-4b / qwen3.5-9b / gemma4-e4b 的 4bit（GGUF Q4 或 MLX 等效）；
  确认 Vulkan(Dozen) 后端加载，`lms server` 起 OpenAI 兼容端点 `http://127.0.0.1:1234/v1`。
- 新增 `src/agent/lmstudio_client.py`（OpenAI 兼容、多模态），接入 vlm-struct 模式：
  用 processed-runs 的截图+answer 做离线 struct 提取准确率评测（对照 19 字段 schema）。
- 基准：单帧推理时延（4b/9b/e4b × Vulkan）、显存/内存占用、字段准确率。**不训练**。

### P5 verifiers 风格环境原型（1–2 天）
- `src/experiments/game_env.py`：GameEnv（reset/step/seed）+ Rubric 集
  （completion / step_efficiency / resource_efficiency / consistency_violations），
  输出 verifiers 兼容的 trajectory JSON。
- 用 rule 模式在 2 个游戏上跑出评分曲线，验证 rubric 区分度。

### P6 训练准备（等 ssh5090 可访问后执行）
- 解包 `processed-runs.rar` 到数据盘，写 `src/training/processed_runs_converter.py`
  转成 `VLMColdStartDataset` 的 7 任务 JSONL 格式（重点补 failure_recovery，旧数据仅 83 条）。
- 本地做数据集 smoke（converter + data_loader 单测通过），确认 `train_qwen35.py --4bit`
  参数组合；ssh5090 可用后：`bash scripts/scp_to_ssh5090.sh` + 同步数据 + QLoRA 开训
  （qwen3.5-4b 优先，9b 视 VRAM；gemma4-e4b 需先确认 transformers 补丁）。

### P7 报告
- 每阶段结果追加到 `EXPERIMENT_RESULTS.md`；最终汇总四大问题的对照数据、
  三路线（云端/本地VLM/规则）的效率-质量前沿、下一步建议。

## 3. 风险与依赖

- LM Studio 模型下载体积大（4bit 4B≈2.5GB、9B≈5GB、e4b≈4GB），依赖网络；Dozen 驱动
  对 llama.cpp Vulkan 的兼容性未验证（fallback：CPU 推理，时延变差但可用）。
- `kimi-k2.7-code` 为思考型模型，单步时延可能高于 deepseek-v4-flash；需在 P0 基线实测。
- ssh5090 当前不可达，训练全部阻塞在 P6，之前只做准备。
- processed-runs 每游戏仅 1 run，做训练集够、做统计显著的 benchmark 不够；
  benchmark 需自己跑多 run（P5 环境支持 seed 复跑）。
