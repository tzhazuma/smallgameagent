# game-agent-harness vs smallgameagent 对比分析

> 对比对象：https://github.com/fps-research/game-agent-harness（main 最新，2026-08 克隆）vs 本仓库（smallgameagent）
> 日期：2026-08-05

## 1. 总览

| 维度 | fps-research/game-agent-harness (gah) | tzhazuma/smallgameagent (我们) |
|---|---|---|
| 语言 | Node.js/ESM（VLM 服务为 Python 子服务） | Python |
| 规模 | 133 文件 / ~87K LOC / 18 个 src 模块 | 99 文件 / ~25K LOC / 6 个 src 目录 |
| 定位 | 确定性网页小游戏自动通关框架 | LLM/VLM 驱动的游戏 Agent + QLoRA 微调管线 |
| 规划模型 | Codex（CLI/会话/API/自建端点） | mimo-v2.5 / kimi / qwen / opencodego 多 provider |
| VLM | 内置 qwen35-perception-final-v2（Qwen3.5-9B LoRA，冻结+校验） | QLoRA 4B 微调完成（886 步，loss 1.99/1.51）+ 多后端可切换 |
| 测试 | Node test 套件（连续策略/策略机/审计等） | 720 pytest 通过 |

## 2. 核心架构差异

### gah：确定性 harness 优先（信任控制流明确）

```
探针 → 版本化世界(Event-sourced DSG) → 连续层次 FSM → Option 监督器
→ JSON-RPC → Macro Task Runtime → VLM 观察(final-v2) → 证据缓冲
→ Codex 策略修复 → 新鲜度/风险/预算/完成闸门 → 闭环控制
```

- **VLM 只观察、不允许给动作**；**Codex 只做策略修复、不直接控制**——权限方向单向
- 18 个模块含先进组件：
  - `cognition/`：证据缓冲、自适应证据预算、认知调度
  - `governance/`：多游戏验证评估
  - `release/`：便携发布（portable distribution）
  - `monitoring/`：运行时监控
  - `regression/`：回归记录
  - `strategy/` + `policy-graph/`：策略晋升、游戏策略图
  - `experience-learning/`：跨 run 经验记忆

### smallgameagent：三层 LLM 分层（职责分离）

```
L0 规则引擎（零延迟执行 + rule_update.py 在线更新：阈值触发/结构化应用/回滚）
L1 本地 VLM（/observe 适配器 + vlm_policy 自适应开关 + QLoRA 微调）
L2 云端 API（多 provider + normalizer 契约校验，planner_rejected=0）
```

- 更依赖 LLM 生成策略，normalizer 把模型输出改写为 harness 可接受的形式
- 训练管线完整：QLoRA 4B（6 任务 15K 样本）、数据采集、评估脚本

## 3. 各自优点

### gah 优点

1. **确定性执行**：FSM + Option + gate 严格约束 → 可复现、可审计、可回归
2. **治理完备**：多游戏验证、便携发布、回归套件、版本化证据（SHA-256 校验）
3. **VLM 受约束观察**：final-v2 adapter 内置 + 完整性清单 + 只观察不给动作 → 安全可控
4. **学习能力**：策略晋升、跨 run 记忆、假设学习（Causal Decision Graph）
5. **浏览器生态**：Node.js 原生 CDP/Chromium，通用探针内核成熟
6. **文档完备**：40+ 篇架构/治理/运行文档

### smallgameagent 优点

1. **多 provider 灵活**：opencodego/kimi/qwen/mimo 一键切换，不绑定单一模型
2. **训练管线**：QLoRA 本地可微调（4B/9B/Gemma），数据驱动迭代
3. **三层架构直观**：L0/L1/L2 职责清晰；规则在线更新（阈值触发 + 结构化应用 + 安全回滚）
4. **VLM 自适应策略**：vlm_policy.py 智能开关（进展跳 VLM / 卡住触发 / 任务分级）
5. **实验验证充分**：50 游戏批量基线（62.5%）、VLM 对照（gameplay +2180%）、三模型对照（mimo 唯一可靠）
6. **Python 生态**：HF/transformers/PyTorch 训练推理原生

## 4. 各自缺点

### gah 缺点

1. **模型绑定**：Codex/GPT 系为主，无多 provider 灵活切换（贵且单一）
2. **无训练管线**：VLM 是"训练好冻结"，本地无法重新微调迭代
3. **语言割裂**：主框架 Node.js，VLM 服务 Python，推理生态弱
4. **复杂度高**：连续 FSM + 多治理机制，学习曲线陡
5. **依赖人工 Codex 会话**：部分规划路径需 Codex CLI/会话安全边界

### smallgameagent 缺点

1. **确定性弱**：更依赖 LLM 策略生成，无严格 FSM/Option 闸门（normalizer 兜底而非约束）
2. **无治理机制**：缺多游戏验证、便携发布、回归、版本化证据
3. **无跨 run 学习**：无策略晋升、版本化记忆（memory.py 是运行内存）
4. **浏览器集成依赖他人**：渲染/探针依赖 fps-play-agent-harness（非自研）
5. **VLM 无完整性校验**：adapter 部署无 SHA-256/架构签名验证
6. **工程化弱**：文档零散，无 release/portable 分发

## 5. 结论与互补方向

两者互补性极强，合并思路：

| 吸收 gah | 贡献我们的 |
|---|---|
| 确定性执行引擎（FSM/Option/gate） | 多 provider 云端规划（不绑 Codex） |
| 治理机制（多游戏验证/发布/回归） | QLoRA 本地训练迭代（VLM 可重训） |
| 受约束 VLM 观察（完整性校验） | 规则在线更新（阈值触发+回滚） |
| 跨 run 记忆与策略晋升 | VLM 自适应开关（vlm_policy） |

**落地优先级**：
1. 把 gah 的 FSM 闸门/证据缓冲思想引入我们的 L0 规则层（确定性兜底）
2. 把我们的多 provider + 训练管线接入 gah（替换 Codex 单一依赖）
3. 借鉴 gah 的 governance/release 建立我们的批量验证与分发机制
