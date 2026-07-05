# 多Agent通信与记忆架构设计

> **版本**: v1.0  
> **日期**: 2026-06-30  
> **适用范围**: SmallGameAgent — LLM/VLM 驱动的 Cocos Creator HTML5 可玩广告游戏智能体

---

## 目录

1. [现有通信架构分析](#1-现有通信架构分析)
2. [多Agent通信方案对比](#2-多agent通信方案对比)
3. [记忆系统方案设计](#3-记忆系统方案设计)
4. [针对本项目的具体建议](#4-针对本项目的具体建议)
5. [架构演进路线图](#5-架构演进路线图)

---

## 1. 现有通信架构分析

### 1.1 总体架构模式

SmallGameAgent 当前采用**分层单体 (Layered Monolith)** 架构。所有 7 种游戏模式共享相同的观察层和执行层，仅在决策层有所不同：

> **注意**: 模式 1 (直接 API) 有两种变体:
> - `LLMAgent.run_game()` (独立使用): 每步执行文本+视觉两次 API 调用, 然后调用 `_fuse_decisions()` 融合
> - `HybridAgent._decide_api()` (HybridAgent 的 `mode="api"`): 仅文本, 不调用视觉 (代码注释: "Direct API (text-only, no vision)")

```
                    ┌──────────────────────────────────────┐
                    │          观察层 (Observation Layer)      │
                    │  Playwright → ProbeAdapter → state dict │
                    │  GameRunner.screenshot() → bytes        │
                    └──────────────┬───────────────────────┘
                                   │ state + screenshot
                                   ▼
                    ┌──────────────────────────────────────┐
                    │     决策层 (Decision Layer) — 7 种模式   │
                    │                                      │
                    │  模式1a: LLMAgent: text+vision→fusion │
                    │  模式1b: HybridAPI: text only          │
                    │  模式2: screenshot → VLM → action     │
                    │  模式3: VLM→Struct→API→action         │
                    │  模式4: VLM→Rules→RuleEngine→action   │
                    │  模式5: API→Rules→RuleEngine→action   │
                    │  模式6: RuleEngine→action             │
                    │  模式7: VLM→Struct→API→Rules→Engine   │
                    └──────────────┬───────────────────────┘
                                   │ decision dict
                                   ▼
                    ┌──────────────────────────────────────┐
                    │       执行层 (Action Layer)             │
                    │  GameRunner.joystick_pulse() / tap()   │
                    │  CDP Input.dispatchTouchEvent          │
                    └──────────────────────────────────────┘
```

### 1.2 组件通信路径

当前所有通信均为**进程内同步函数调用**，无消息总线、无事件系统、无中间件：

| 通信路径 | 源文件 (关键方法) | 方式 | 数据格式 |
|---------|-------------------|------|---------|
| 浏览器 → 探针适配器 | `ProbeAdapter.inject()` / `.observe_fast()` | `page.evaluate()` | JavaScript → Python dict |
| 探针适配器 → LLM智能体 | `LLMAgent._observe()` → `_think_text()` | 直接方法调用 | `dict[str, Any]` (state) |
| LLM智能体 → API客户端 | `OpenCodeGoClient.chat()` / `.chat_with_vision()` | OpenAI SDK (HTTPS) | JSON (OpenAI Chat格式) |
| 视觉分析器 → Mimo API | `VisualAnalyzer._analyze_via_api()` | HTTPS + base64 image | JSON (结构化视觉响应) |
| API文本决策 + 视觉决策 | `LLMAgent._fuse_decisions()` | 静态优先级融合 | 两个 dict → 合并为 action dict |
| VLM推理服务器 | `POST /predict` (FastAPI) | REST multipart | PNG + JSON state |
| 结构化提取器 → VLM | `extract_visual_structure(vlm_predict_fn)` | callable 协议 | PIL Image + state_payload dict |
| 规则提取器 → VLM/API | `extract_rules_from_vlm()` / `extract_rules_from_api()` | callable 协议 | PIL Image + state_payload dict |
| 规则提取器 → 规则引擎 | `RuleSet` 构造函数 → `RuleEngine.step()` | 数据类实例化 | `GameRule` / `RuleSet` 对象 |
| HybridAgent 决策路由 | `HybridAgent._decide()` | `if/elif` 分支 | 7 种独立的方法调用 |

### 1.3 7 种模式的完整数据流

#### 模式 1: 直接 API (LLMAgent)

```
Cocos 游戏 → ProbeAdapter.observe_fast() → state dict
                                          → LLMAgent._think_text(state, history)
                                            → OpenCodeGoClient.chat(DeepSeek-v4-flash)
                                            → _parse_llm_response() → action dict
Screenshot → GameRunner.screenshot() → PNG bytes
                                     → LLMAgent._think_vision(screenshot)
                                       → OpenCodeGoClient.chat_with_vision(Mimo-v2.5)
                                       → _parse_llm_response() → vision dict
                                     → LLMAgent._fuse_decisions(text_action, vision_action)
                                       → 最终 action dict
                                     → LLMAgent._execute(action) → GameRunner joystick/tap
```

**关键特征**:
- 每一步执行 2 次 API 调用 (文本 + 视觉)
- 视觉决策覆盖规则: 结束画面 → `wait` / 箭头 → `move` 方向 / 否则信任文本
- 历史记录窗口: 最近 20 步, 仅最后 5 步注入 prompt
- API 失败容错: JSON 解析重试 ×2, fallback 到 `wait`

#### 模式 2: 直接 VLM

```
Screenshot → HybridAgent._decide_vlm()
           → Image.open(BytesIO(screenshot_bytes)).convert("RGB")
           → loop.run_in_executor(None, self._vlm_engine.predict, pil, state)
             → GameAgentInference.predict(pil, state_dict)
               → processor.apply_chat_template() → model.generate() → decode
               → _parse_model_output() → action dict
```

**关键特征**:
- 单次前向传播, 无需外部 API
- 需要 GPU (~30GB VRAM for BF16, ~6GB for 4-bit NF4)
- 通过 `run_in_executor` 实现异步, 避免阻塞事件循环
- 当前 OOM 问题: 训练使用 4-bit NF4 量化 (~6GB VRAM), 但推理服务器默认加载 BF16 (~30GB), 导致 4× RTX 5090 上 OOM
- 解决方案: 启动服务器时添加 `--4bit` 标志 (`python src/inference/server.py --model ... --4bit`)

#### 模式 3: VLM → 结构化状态 → API 文本

```
Screenshot → extract_visual_structure(vlm_predict_fn, pil, state)
           → VLM 输出 19 字段结构化视觉信息
           → enriched_state = state + {"_visual_struct": visual_struct}
           → LLMAgent._think_text(enriched_state, [])
             → DeepSeek 基于丰富状态决策 → action dict
```

**关键特征**:
- VLM 负责感知 (perception), DeepSeek 负责推理 (reasoning)
- 结构化状态包含: 箭头方向/位置、目标、障碍物、结束画面、UI 按钮
- VLM predict 通过 `_extract_mode=True` 标记切换到提取模式

#### 模式 4: VLM → 规则 → 规则引擎

```
Screenshot → extract_rules_from_vlm(vlm_predict_fn, pil, state)
           → VLM 输出 action → 转换为 GameRule
           → RuleSet → 更新 RuleEngine.driver_type
           → RuleEngine.step(state) → action dict
```

**关键特征**:
- VLM 输出直接被映射为 `GameRule` 对象
- 规则引擎根据 driver_type 选择策略 (follow-guide / 2d / learned 等)
- 规则引擎维护内部状态机: `step_count`, `stuck_streak`, `last_player_pos`

#### 模式 5: API → 规则 → 规则引擎

```
state + screenshot → LLMAgent._think_text() + LLMAgent._think_vision()
                   → extract_rules_from_api()
                   → 构建 GameRule 列表 (箭头检测/结束画面规则)
                   → RuleSet → RuleEngine.step(state) → action dict
```

**关键特征**:
- 规则提取需要 2 次 API 调用 (文本 + 视觉各一次), 后续执行无需 API
- 比模式 1 快 6x (实验数据: 4.8s/step vs 29.0s/step)
- 规则引擎本地执行零 API 延迟

#### 模式 6: 纯规则引擎

```
state + screenshot → VisualAnalyzer.analyze(pil) (Mimo/PIL fallback)
                   → RuleEngine.step(state, visual) → action dict
```

**关键特征**:
- 零 API 调用, 零 GPU 需求, 最快模式 (2.9s/step)
- 包含卡住检测、目标选择、路径规划
- PIL fallback 使用颜色阈值检测青色箭头和结束画面

#### 模式 7: VLM → 结构化 → API → 规则 → 规则引擎

```
Screenshot → VLM struct_extractor → visual_struct
           → API _RULE_GEN_PROMPT.format(visual_struct, state_json)
           → DeepSeek chat → 解析 GameRule 列表 → RuleSet
           → RuleEngine.step(state) → action dict
```

**关键特征**:
- 7 步全链路: VLM 感知 → API 推理 → 规则引擎执行
- 最复杂的模式, 当前测试在 ssh5090 上端到端验证通过
- VLM 输出和 API 规则格式的匹配仍需优化

### 1.4 决策融合机制 (`_fuse_decisions`)

当前决策融合使用**静态优先级规则** (`llm_agent.py:430-487`):

```
1. 如果 vision 检测到结束画面 (is_end_screen=true)
   → 无条件覆盖为 wait (2000ms)
   → 理由: "Vision: end screen detected, waiting"

2. 如果 vision 检测到箭头 (has_arrow=true, arrow_direction 未缺失且不为 "none")
   → 覆盖为 move, 方向由箭头方向决定
   → 理由: "Vision: following {direction} arrow"

3. 否则信任文本模型的决策
   → 使用 text_response 的 action/params/reason

4. 额外视觉信息 (extra_notes) 追加到 reason 字段

5. 无效 action 类型 → fallback 到 wait
```

**局限性**:
- 融合逻辑是硬编码的, 不支持扩展新的覆盖规则
- 没有置信度评分机制 (视觉和文本同等权重)
- 不保留融合依据供后续调试
- 没有"投票"或"加权"等高级融合策略

### 1.5 当前内存管理现状

| 内存类型 | 位置 | 内容 | 生命周期 | 持久化 |
|---------|------|------|---------|-------|
| 步骤历史 | `llm_agent.py:156` | 最近 20 步 `{step, state_summary, decision}` | per `run_game()` | ❌ 进程内 |
| 数据集缓冲 | `llm_agent.py:126` | 完整 `{state, screenshot, decision}` | per `run_game()` | ❌ 仅返回在 result 中 |
| 规则引擎状态 | `RuleEngine.__init__()` | `step_count`, `stuck_streak`, `last_player_pos`, `last_action`, `_learned_obstacles` | per `RuleEngine` 实例 | ❌ 进程内 |
| 视觉缓存 | `visual_analyzer.py:115` | SHA-256 哈希键, 5秒 TTL | per `VisualAnalyzer` 实例 | ❌ 进程内 |
| 实验数据 | `experiments/*.py` | 完成摘要 JSON | 每次实验写入 | ✅ JSON 文件 |
| 训练数据 | `vlm-training-data-*/` | 9,378 样本 JSONL + PNG | 静态 | ✅ 数据集 |

**当前架构的关键局限**:

1. **无跨会话记忆**: 每次 `run_game()` 从零开始, 智能体不记得之前的游戏经验
2. **无组件间消息抽象**: 所有通信是直接函数调用, 紧耦合, 难以扩展新的处理阶段
3. **无持久化步历史**: 步骤历史在游戏结束后丢失, 无法用于离线分析或训练
4. **数据集无反馈回路**: `collect_dataset` 收集的数据仅返回在 result 中, 不会自动写入磁盘或反馈到推理
5. **硬编码模式路由**: HybridAgent 使用 `if/elif` 在 7 种模式间路由, 添加新模式需要修改核心代码
6. **无因果追踪**: 无法追溯"为什么智能体在那个时间点选择了那个动作"

---

## 2. 多Agent通信方案对比

### 2.1 方案总览

下表对比 7 种通信模式, 按耦合度从紧到松排列:

| 方案 | 耦合度 | 延迟 | 可扩展性 | 可调试性 | 适用场景 |
|------|--------|------|---------|---------|---------|
| 直接函数调用 (当前) | 🔴 紧 | 0 (进程内) | ❌ 低 | ✅ 高 | 单进程单体 |
| 共享状态/黑板 | 🟡 中 | 0-1ms | 🟡 中 | 🟡 中 | 3-5 个组件协作 |
| 消息队列 (asyncio.Queue) | 🟢 松 | 0.1-1ms | 🟢 高 | 🟡 中 | 异步管道处理 |
| 发布/订阅 (事件总线) | 🟢 松 | 0.1-5ms | 🟢 高 | 🔴 低 | 10+ 组件事务系统 |
| REST/HTTP API | 🟢 松 | 1-50ms | 🟢 高 | ✅ 高 | 跨进程/跨机器 |
| gRPC | 🟢 松 | 0.3-5ms | 🟢 高 | 🟡 中 | 高性能跨进程 |
| A2A (Agent-to-Agent) | 🟢 松 | 10-100ms | 🟢 高 | 🟡 中 | 跨组织/异构代理 |

### 2.2 方案详述

#### 2.2.1 直接函数调用 (当前方案)

**模式**: 组件 A 直接调用组件 B 的方法, 同步等待结果。

```
Agent._think_text(state, history) → call→  OpenCodeGoClient.chat(messages)
                                   ← result ←
Agent._fuse_decisions(text, vision) → 合并决策
```

**优点**:
- 零通信开销, 最简实现
- 调试器可以直接跟踪调用堆栈
- 类型安全 (IDE 可以检查方法签名)

**缺点**:
- 紧耦合: 调用方必须知道被调用方的存在
- 无法添加中间处理阶段 (如日志、缓存、重试)
- 不适合跨进程或跨机器场景

**对本项目适用性**: ⭐⭐⭐⭐⭐ (当前状态, 适合单进程)

#### 2.2.2 共享状态/黑板模式

**模式**: 所有组件通过一个中央数据存储交换信息, 不直接通信。

```
                    ┌──────────────────────┐
                    │   黑板 (共享状态)       │
                    │  state = {            │
                    │    probe_state,       │
                    │    visual_struct,     │
                    │    extracted_rules,   │
                    │    decision_history,  │
                    │    ...               │
                    │  }                   │
                    └──────────────────────┘
                          ↕      ↕      ↕
                   ┌────┐ ┌────┐ ┌────┐
                   │感知│ │推理│ │执行│
                   └────┘ └────┘ └────┘
```

**优点**:
- 松耦合: 组件只与黑板通信, 不知道其他组件存在
- 天然支持日志/审计 (所有状态变更经过黑板)
- 易于添加新组件 (只需读写黑板)

**缺点**:
- 黑板可能成为性能瓶颈
- 需要状态变更通知机制
- 缺乏调用序约束 (组件可能读取到不完整状态)

**对本项目适用性**: ⭐⭐⭐⭐ (非常适合 HybridAgent 的模式路由)

#### 2.2.3 消息队列模式

**模式**: 组件通过消息队列 (管道) 传递数据, 每个组件独立消费和生产。

```
状态获取器 → Queue[state] → 决策器 → Queue[action] → 执行器
```

Python 实现:
```python
import asyncio

class AgentPipeline:
    def __init__(self):
        self._state_queue: asyncio.Queue = asyncio.Queue()
        self._action_queue: asyncio.Queue = asyncio.Queue()
    
    async def observer_loop(self, probe, runner):
        while True:
            state = await probe.observe_fast(runner._page)
            await self._state_queue.put(state)
            await asyncio.sleep(0.05)
    
    async def decision_loop(self):
        while True:
            state = await self._state_queue.get()
            action = await self._decide(state)
            await self._action_queue.put(action)
    
    async def execution_loop(self, runner):
        while True:
            action = await self._action_queue.get()
            await self._execute(action, runner)
```

**优点**:
- 天然异步: 各阶段以各自节奏运行
- 背压支持: 队列满时消费者自动减速
- 易于并行: 多决策器可以竞争消费

**缺点**:
- 需要队列管理 (大小、超时、死信)
- 调试流程比同步调用困难
- 需要确保消息有序性

**对本项目适用性**: ⭐⭐⭐ (适合大规模并行, 但对单游戏实例可能过度)

#### 2.2.4 发布/订阅 (事件总线)

**模式**: 组件发布事件到主题, 订阅者异步接收。事件总线解耦所有组件。

```
  ┌─→ 状态更新事件 ──→  日志记录器
  │                     决策记录器
  │                     性能监控器
  │
  │─→ 决策事件 ──→     实验数据收集器
  │                     可视化面板
  │
  │─→ 游戏结束事件 ──→  数据集写入器
                        模型训练触发器
```

**优点**:
- 最大解耦: 发布者不知道订阅者
- 易于扩展: 添加新订阅者无需修改现有代码
- 完美支持横切关注点 (日志、监控、审计)

**缺点**:
- 事件风暴: 高频事件可能导致订阅者过载
- 因果跟踪困难: 难以确定事件顺序
- 无回调: 发布者无法获得处理结果

**对本项目适用性**: ⭐⭐⭐ (适合 10+ 组件事务系统, 当前阶段可能过度)

#### 2.2.5 REST/HTTP API

**模式**: 组件作为 HTTP 服务暴露 API, 使用 JSON 格式通信。

本项目已有的案例:
- VLM 推理服务器: `POST /predict` (multipart: screenshot + JSON state)
- OpenCodeGo API 客户端: `POST /v1/chat/completions` (OpenAI 兼容格式)

**优点**:
- 语言/平台无关
- 天然跨进程/跨机器
- OpenAI 兼容格式是事实标准

**缺点**:
- 进程内通信开销大 (序列化/反序列化)
- 需要 HTTP 服务器基础设施
- 不适合高频调用 (<10ms 间隔)

**对本项目适用性**: ⭐⭐⭐⭐ (适合 VLM 推理服务器和外部 API)

#### 2.2.6 gRPC

**模式**: 使用 Protocol Buffers 的二进制 RPC 框架, HTTP/2 传输。

与 REST 对比:

| 指标 | REST + JSON | gRPC + Protobuf |
|------|------------|-----------------|
| P50 延迟 | ~142ms | ~88ms |
| P99 延迟 | ~847ms | ~319ms |
| 序列化 512-dim 向量 | ~0.8ms | ~0.05ms |
| CPU (10K req/s) | ~18 核 | ~3 核 |
| 流式支持 | SSE (附接) | 原生双向 |

**优点**:
- 更低的延迟和更高的吞吐量
- 原生流式支持 (服务器推送、双向流)
- 强类型接口定义 (.proto 文件)

**缺点**:
- 需要 Proto 定义和代码生成
- 浏览器支持有限 (需要 gRPC-Web)
- Python 生态支持不如 REST 成熟

**对本项目适用性**: ⭐⭐ (高频场景有价值, 当前阶段收益不大)

#### 2.2.7 A2A (Agent-to-Agent Protocol)

**模式**: Google 发起的跨框架代理通信标准, 2026 年 6 月 v1.0 发布。通过 Agent Card 发现能力, JSON-RPC 2.0 传输。

```
Agent A ── Task(submitted→working→completed) ──→ Agent B
         ←── Agent Card (capabilities) ──────────
```

**核心概念**:
- **Agent Card**: 声明能力、输入/输出格式、端点的 JSON 元数据
- **Task 生命周期**: `submitted → working → completed | failed`
- **Message Part**: `TextPart` (自然语言) / `DataPart` (结构化 JSON)

**优点**:
- 跨框架互操作 (LangGraph ↔ CrewAI ↔ ADK)
- 标准化发现机制
- 150+ 企业伙伴支持

**缺点**:
- 需要额外的服务器基础设施
- 单游戏代理场景可能过度
- 生态系统仍在发展中

**对本项目适用性**: ⭐⭐ (未来扩展时有价值, 当前不需)

### 2.3 推荐组合

对于本项目, 推荐**混合通信方案**:

| 通信层级 | 推荐方案 | 理由 |
|---------|---------|------|
| 进程内组件协同 | 黑板模式 (共享状态) | HybridAgent 模式间共享上下文 |
| 异步管道处理 | asyncio.Queue | 观察→决策→执行三步流水线 |
| 跨进程 VLM 推理 | REST/HTTP (OpenAI 兼容) | 已实现, 事实标准 |
| 外部 API | REST/HTTP (已有) | OpenAI SDK 封装 |

```
┌──────────────┐     asyncio.Queue     ┌──────────────┐
│  观察者循环    │──── state dict ──────▶│  决策器循环    │
│  (生产者)      │                      │  (消费者)      │
└──────────────┘                      └──────┬───────┘
                                             │ action dict
                                             ▼
                                    ┌──────────────┐
                                    │  执行器循环    │
                                    │  (消费者)      │
                                    └──────────────┘

黑板 (共享上下文):
├── game_profiles (静态配置)
├── session_state (当前游戏状态)
├── memory_store (记忆服务, 见第3节)
└── event_bus (可选, 用于横切关注点)
```

---

## 3. 记忆系统方案设计

### 3.1 四层记忆模型

基于 CoALA (Cognitive Architectures for Language Agents) 框架和 MemGPT/Letta 架构, 设计四层记忆系统:

```
┌───────────────────────────────────────────────────────────────┐
│                    智能体 (Agent Instance)                       │
│                                                               │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  工作记忆 (Working Memory)                             │    │
│  │  • 当前游戏帧缓冲 (最近 60 帧)                          │    │
│  │  • 当前状态 (level, score, HP, boss_phase)             │    │
│  │  • 最近操作历史 (最近 20 步)                             │    │
│  │  • TTL: 5 分钟, 自动过期                                │    │
│  │  存储: In-process dict                                  │    │
│  └──────────────────────────────────────────────────────┘    │
│                         ↕ (API 读写)                          │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  情景记忆 (Episodic Memory)                           │    │
│  │  • 完整游戏会话记录 (时间、动作、状态快照)               │    │
│  │  • 查询: "类似情况下过去怎么做的?"                     │    │
│  │  • 旧会话自动摘要 (ConversationSummaryBuffer 模式)    │    │
│  │  存储: SQLite (WAL 模式)                               │    │
│  └──────────────────────────────────────────────────────┘    │
│                         ↕ (向量检索)                          │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  语义记忆 (Semantic Memory)                           │    │
│  │  • 游戏机制知识: "Boss 红时 → 向左闪避"                │    │
│  │  • 策略模式: "得分 > 5000 → 使用炸弹"                 │    │
│  │  • 校准数据: "00848 的摇杆映射已验证"                 │    │
│  │  存储: ChromaDB / sqlite-vec                          │    │
│  └──────────────────────────────────────────────────────┘    │
│                         ↕ (规则应用)                          │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  程序记忆 (Procedural Memory)                         │    │
│  │  • IF/THEN 规则: "IF stuck > 5s THEN escape_rotate"    │    │
│  │  • 成功动作序列模板                                    │    │
│  │  • 每种 driver_type 的策略函数                         │    │
│  │  存储: JSON + 代码 + 版本化配置                        │    │
│  └──────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────┘
```

### 3.2 工作记忆 (Working Memory)

**定位**: 超短期记忆, 相当于 MemGPT 的 "Core Memory" 或人类的 "注意力焦点"。

**当前状态**: LLMAgent 维护了 `history[-20:]` 和 `self._dataset`, 但都不是结构化的。

**建议实现**:

```python
@dataclass
class WorkingMemory:
    """工作记忆 — 当前会话的所有活跃上下文"""
    # 当前游戏帧
    game_id: str = ""
    current_state: dict[str, Any] = field(default_factory=dict)
    last_screenshot: bytes | None = None
    
    # 操作历史 (结构化)
    action_history: list[StepRecord] = field(default_factory=list)
    max_history: int = 60  # 最多保留 60 步
    
    # 规则引擎内部状态
    step_count: int = 0
    stuck_streak: int = 0
    last_player_pos: tuple[float, float] | None = None
    
    # 工作标志
    is_stuck: bool = False
    current_target: tuple[float, float] | None = None
    
    def push_action(self, state: dict, action: dict, screenshot: bytes | None = None):
        """记录一步操作, 自动维护窗口"""
        record = StepRecord(
            timestamp=time.monotonic(),
            state_summary=self._summarize(state),
            action=action,
        )
        self.action_history.append(record)
        if len(self.action_history) > self.max_history:
            self.action_history = self.action_history[-self.max_history:]
    
    def recent_actions(self, n: int = 5) -> list[StepRecord]:
        """获取最近 N 步操作, 用于 prompt 注入"""
        return self.action_history[-n:]
    
    def detect_stuck(self, current_pos: tuple[float, float], threshold: float = 0.05) -> bool:
        """检测是否卡住"""
        if self.last_player_pos:
            moved = math.hypot(
                current_pos[0] - self.last_player_pos[0],
                current_pos[1] - self.last_player_pos[1],
            )
            if moved < threshold:
                self.stuck_streak += 1
            else:
                self.stuck_streak = 0
            self.is_stuck = self.stuck_streak >= 5
        self.last_player_pos = current_pos
        return self.is_stuck
```

**存储**: 进程内 dict, 不需要持久化。
**TTL**: 5 分钟无访问自动过期。

### 3.3 情景记忆 (Episodic Memory)

**定位**: 长期记忆, 相当于 MemGPT 的 "Archival Memory" 或 SQLite 数据库。

**功能**:
- 记录完整的游戏会话 (时间戳、动作序列、结果)
- 支持按相似度检索 (向量搜索)
- 旧会话自动摘要 (减少存储和检索成本)

**数据库 Schema** (SQLite):

```sql
-- 情景记忆: 会话表
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,           -- UUID
    game_id TEXT NOT NULL,         -- "SSD_00848P01"
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    total_steps INTEGER DEFAULT 0,
    result TEXT,                   -- "win" / "lose" / "timeout"
    score REAL DEFAULT 0,
    summary TEXT,                  -- LLM 生成的会话摘要
    embedding BLOB,                -- se-sr384 向量 (768维 float32)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 情景记忆: 步骤表
CREATE TABLE steps (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    step_number INTEGER NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    
    -- 状态 (JSON 序列化)
    state_json TEXT NOT NULL,
    
    -- 决策
    action TEXT NOT NULL,          -- "move" / "tap" / "wait"
    params_json TEXT NOT NULL,     -- {"dx": 0.5, "duration_ms": 320}
    reason TEXT,                   -- 决策理由
    
    -- 元数据
    mode TEXT,                     -- "api" / "vlm" / "rule" / ...
    latency_ms REAL,               -- 此步骤耗时
    screenshot_path TEXT,          -- PNG 路径 (可选)
    
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

-- 步骤的向量索引
CREATE VIRTUAL TABLE steps_fts USING fts5(
    state_json, action, reason,
    content='steps',
    content_rowid='rowid'
);

-- 索引
CREATE INDEX idx_sessions_game ON sessions(game_id);
CREATE INDEX idx_sessions_result ON sessions(result);
CREATE INDEX idx_steps_session ON steps(session_id);
CREATE INDEX idx_steps_action ON steps(action);
```

**检索 API**:

```python
class EpisodicMemory:
    async def find_similar_sessions(
        self, game_id: str, query_embedding: list[float], top_k: int = 5
    ) -> list[SessionSummary]:
        """查找与当前场景相似的历史会话"""
        # 1. 向量相似度搜索 (sqlite-vec / ChromaDB)
        # 2. 按 game_id 过滤
        # 3. 按时间排序 (最近的优先)
        # 4. 返回摘要
        ...
    
    async def record_step(
        self, session_id: str, step: int, state: dict, action: dict, 
        mode: str, latency_ms: float, screenshot: bytes | None = None
    ):
        """记录一步到情景记忆"""
        ...
    
    async def summarize_session(self, session_id: str) -> str:
        """使用 LLM 生成会话摘要"""
        ...
```

### 3.4 语义记忆 (Semantic Memory)

**定位**: 事实性知识, 相当于人类的"常识"或游戏"攻略"。

**存储**: ChromaDB (PersistentClient) 或 `sqlite-vec`。

```
Collection: "game_mechanics"
  Document: "BOSS 红色时向左闪避可以避免攻击"
  Metadata: {game_id: "SSD_00848P01", confidence: 0.85, times_used: 12}
  Embedding: [0.12, 0.45, ...] (768维)

Collection: "game_strategies"
  Document: "前 30 秒收集所有金币可触发隐藏关卡"
  Metadata: {game_id: "SSD_00853P01", confidence: 0.7, success_rate: 0.6}
  Embedding: [...]

Collection: "calibration_data"
  Document: "SSD_00848P01 joystick anchor [91,699] radius 50 basis right=(2.12,2.12)"
  Metadata: {game_id: "SSD_00848P01", verified: "2026-05-27"}
```

**知识获取管道**:

```
游戏结束 → LLM 分析会话 → 提取"什么策略有效/无效"
         → 生成语义记忆条目
         → 计算嵌入 → 存入 ChromaDB
```

**检索 API**:

```python
class SemanticMemory:
    async def query(
        self, query: str, game_id: str | None = None, top_k: int = 5
    ) -> list[KnowledgeEntry]:
        """语义搜索游戏知识"""
        embedding = await self._embed(query)
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={"game_id": game_id} if game_id else None,
        )
        return [KnowledgeEntry(doc, meta) for doc, meta in zip(results)]
    
    async def add_knowledge(
        self, content: str, metadata: dict, importance: float = 0.5
    ):
        """添加新知识 (由 LLM 在游戏后分析生成)"""
        embedding = await self._embed(content)
        self._collection.add(
            documents=[content],
            embeddings=[embedding],
            metadatas=[{**metadata, "importance": importance, "created_at": time.time()}],
        )
```

### 3.5 程序记忆 (Procedural Memory)

**定位**: "如何做"的知识 — 规则、策略函数、动作模板。

**当前状态**: 规则引擎 (`src/engine/rules.py`) 已经实现了部分程序记忆 — `_strategy_follow_guide`, `_strategy_2d`, `_strategy_learned`。但不可持久化, 不可跨会话重用。

**建议方案**:

```python
@dataclass
class ProceduralRule:
    """可持久化的程序记忆单元"""
    name: str
    condition: str                    # "IF stuck_streak >= 5 AND no_target"
    priority: int                     # 0-10, 越高越优先
    action_template: dict             # {"action": "move", "params": {"dx": "...", "dy": "..."}}
    success_rate: float = 0.0         # 历史成功率
    times_applied: int = 0
    source: str = "builtin"           # "builtin" / "vlm_extracted" / "api_generated" / "learned"
    game_id: str = ""

class ProceduralMemory:
    def __init__(self, db_path: str = "procedural_memory.json"):
        self._rules: list[ProceduralRule] = []
        self._load(db_path)
    
    def match(self, state: dict, working_mem: WorkingMemory) -> ProceduralRule | None:
        """找到匹配当前状态且优先级最高的规则"""
        matched = []
        for rule in self._rules:
            if self._evaluate_condition(rule.condition, state, working_mem):
                matched.append(rule)
        if not matched:
            return None
        return max(matched, key=lambda r: r.priority)
    
    def learn(self, rule: ProceduralRule):
        """添加新规则 (从 VLM/API 提取)"""
        self._rules.append(rule)
        self._save()
    
    def update_success_rate(self, rule_name: str, succeeded: bool):
        """更新规则的成功率 (强化学习信号)"""
        for rule in self._rules:
            if rule.name == rule_name:
                old = rule.success_rate
                rule.times_applied += 1
                # 指数移动平均
                rule.success_rate = old + (1.0 / rule.times_applied) * (int(succeeded) - old)
                break
```

### 3.6 记忆生命周期

```
                        写入路径                          读取路径
                         
  游戏步骤完成 ──→ 工作记忆                       LLM prompt 构建
       │              push_action()                  │
       ▼                                              ▼
  [批处理积累] ──→ 情景记忆                       组装上下文块
  (每 N 步或       record_step()              (工作记忆 + 情景检索
   会话结束)                                       + 语义检索)
       │                                              │
       ▼                                              ▼
  会话结束 ──→ 情景摘要 ──→ 语义知识提取           注入 system prompt
  (LLM 分析      (LLM               (LLM 分析:         + user message
   会话)         summary)            "学到了什么?")
                                        │
                                        ▼
                                    程序记忆
                                    (新策略规则)
                                        │
                                        ▼
                                    版本化存储
                                    (JSON + 嵌入)
```

**遗忘策略**:
- 工作记忆: 5 分钟 TTL, 无访问自动清除
- 情景记忆: >30 天的会话自动摘要, 原始步骤删除
- 语义记忆: 置信度 < 0.3 的知识不再返回
- 程序记忆: 成功率 < 0.2 且应用次数 > 5 的规则归档

### 3.7 实现方案对比

| 方案 | 持久化 | 搜索能力 | 部署复杂度 | 适用于 |
|------|--------|---------|-----------|-------|
| **JSONL 文件** | ✅ 简单 | ❌ 只支持顺序扫描 | 🟢 极低 | 工作记忆快照 |
| **SQLite + FTS5** | ✅ 可靠 | 🟡 关键词搜索 | 🟢 低 | 情景记忆 |
| **SQLite + sqlite-vec** | ✅ 可靠 | ✅ 向量 + 关键词混合 | 🟢 低 | 情景 + 语义记忆 |
| **ChromaDB** | ✅ 持久 | ✅ 向量搜索 | 🟡 中 | 语义记忆 |
| **Redis** | ⚠️ 需持久化配置 | 🟡 有限 | 🟡 中 | 工作记忆 (高性能) |
| **PostgreSQL + pgvector** | ✅ 企业级 | ✅ 混合搜索 | 🔴 高 | 大规模部署 |

**推荐**: 从 **SQLite + sqlite-vec** 起步, 零基础设施, 单文件存储, 支持向量+关键词混合搜索。项目 >10 万条记录时考虑迁移到 PostgreSQL + pgvector。

---

## 4. 针对本项目的具体建议

### 4.1 [P0] 引入黑板模式共享上下文

**问题**: 当前 HybridAgent 在 7 种模式间传递共享状态的方式不统一 — 有的模式使用 `state` 字典, 有的使用 `_visual_struct`, 有的使用 `RuleSet`。没有标准化的上下文传递机制。

**方案**: 引入 `AgentContext` 黑板对象, 作为所有模式共享的上下文容器。

```python
@dataclass
class AgentContext:
    """所有智能体模式共享的上下文黑板"""
    # 观察结果
    probe_state: dict[str, Any] = field(default_factory=dict)
    screenshot: bytes | None = None
    visual_struct: dict[str, Any] | None = None
    
    # 决策结果
    text_decision: dict | None = None
    vision_decision: dict | None = None
    final_action: dict | None = None
    extracted_rules: RuleSet | None = None
    
    # 工作记忆
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    
    # 元数据
    current_mode: str = "api"
    step_number: int = 0
    errors: list[str] = field(default_factory=list)
```

**影响文件**: `hybrid_agent.py` (新增 `AgentContext`), `llm_agent.py` (修改 `_decide` 系列方法签名)

**工作量**: S (2-3 天)

---

### 4.2 [P0] 添加结构化的步骤历史持久化

**问题**: 当前 20 步历史窗口在游戏结束后丢失。无法回放、分析或用于训练。

**方案**: `WorkingMemory.push_action()` 将每一步记录到 SQLite 数据库。每步包含状态、动作、截图路径、延迟、模式。

```python
# 使用方式
await working_memory.push_action(
    state=probe_state,
    action=decision,
    screenshot=screenshot_bytes,
    mode=self.mode,
    latency_ms=step_latency,
    db_path="game_sessions.db",  # SQLite 数据库路径
)
```

**影响文件**: 新建 `src/agent/memory.py` (WorkingMemory + EpisodicMemory), 修改 `hybrid_agent.py`

**工作量**: S (2-3 天)

---

### 4.3 [P1] 实现跨会话情景检索

**问题**: 每次 `run_game()` 从零开始, 智能体不记得之前的游戏经验。相同错误可能重复出现。

**方案**: 在游戏开始时, 查询 SQLite 中同一 game_id 的历史会话, 提取相关经验注入 system prompt。

```
run_game(game_id="SSD_00848P01"):
  → EpisodicMemory.find_similar_sessions(game_id, top_k=3)
  → 获取 3 个最相似的历史会话摘要
  → 注入到 LLM system prompt:
    "Previous session notes:
     - Session A (2026-06-29): Won at step 145
       Strategy: Follow cyan arrows, avoid red zones
     - Session B (2026-06-28): Lost at step 89
       Failure: Got stuck in bottom-left corner
     - Session C (2026-06-27): Timeout at step 200
       Lesson: Need faster response on conveyor belts"
```

**影响文件**: 新建 `src/agent/memory.py` (EpisodicMemory), 修改 `llm_agent.py` (system prompt 构建)

**工作量**: M (4-5 天)

---

### 4.4 [P1] 数据集收集自动持久化

**问题**: `collect_dataset=True` 产生的数据仅返回在 `result_summary["dataset"]` 中, 没有任何自动写入磁盘的机制。收集的数据无法被训练流程使用。

**方案**: 在 `_collect_dataset_step` 中添加自动 JSONL 写入器:

```python
class DatasetWriter:
    """自动写入 JSONL 数据集"""
    def __init__(self, output_dir: str = "./collected_datasets"):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._writer = None
        self._game_id = ""
    
    def start_session(self, game_id: str):
        """开始新会话, 创建新 JSONL 文件"""
        self._game_id = game_id
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._output_dir / f"{game_id}_{timestamp}.jsonl"
        self._writer = open(path, "w", encoding="utf-8")
    
    def write_step(self, state: dict, screenshot_path: str, decision: dict):
        """写入一步"""
        if self._writer:
            record = {
                "game_id": self._game_id,
                "timestamp": time.time(),
                "state": state,
                "screenshot_rel": screenshot_path,
                "decision": decision,
            }
            self._writer.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._writer.flush()
    
    def end_session(self):
        """关闭文件"""
        if self._writer:
            self._writer.close()
            self._writer = None
```

**影响文件**: 新建 `src/agent/dataset_writer.py`, 修改 `llm_agent.py` (替换 `_collect_dataset_step`)

**工作量**: S (1-2 天)

---

### 4.5 [P2] 可插拔决策注册表代替硬编码 if/elif

**问题**: HybridAgent 使用 7 路 `if/elif` 在模式间路由。添加新模式需要修改核心类, 违反开闭原则。

**方案**: 引入抽象基类和决策器注册表:

```python
from abc import ABC, abstractmethod

class BaseDecisionMaker(ABC):
    """所有决策器的抽象基类"""
    
    @abstractmethod
    async def decide(self, ctx: "AgentContext") -> dict:
        """根据上下文产生动作 dict"""
        ...


class DecisionRegistry:
    """可插拔的决策器注册表"""
    _registry: dict[str, type[BaseDecisionMaker]] = {}
    
    @classmethod
    def register(cls, name: str):
        """装饰器: 注册决策器"""
        def wrapper(maker_cls: type[BaseDecisionMaker]):
            cls._registry[name] = maker_cls
            return maker_cls
        return wrapper
    
    @classmethod
    def create(cls, name: str, **kwargs) -> BaseDecisionMaker:
        """创建决策器实例"""
        maker_cls = cls._registry.get(name)
        if maker_cls is None:
            raise ValueError(f"Unknown decision maker: {name}")
        return maker_cls(**kwargs)


@DecisionRegistry.register("api")
class APIDecisionMaker(BaseDecisionMaker):
    async def decide(self, ctx: AgentContext) -> dict:
        return await ctx.llm_agent._think_text(ctx.probe_state, ctx.working_memory)


@DecisionRegistry.register("vlm")
class VLMDecisionMaker(BaseDecisionMaker):
    async def decide(self, ctx: AgentContext) -> dict:
        pil = Image.open(BytesIO(ctx.screenshot)).convert("RGB")
        result = await loop.run_in_executor(
            None, ctx.vlm_engine.predict, pil, ctx.probe_state
        )
        return result


# 使用方式
maker = DecisionRegistry.create("vlm", ...)
action = await maker.decide(ctx)
```

**影响文件**: 新建 `src/agent/registry.py` + `src/agent/decision_makers/*.py`, 修改 `hybrid_agent.py`

**工作量**: M (4-5 天)

---

### 4.6 [P2] 经验回放缓冲 (Experience Replay Buffer)

**问题**: 训练数据来自人工演示 (~9K 样本), 但智能体自身产生的游戏轨迹没有被利用。成功和失败的游戏经验可以用于持续改进。

**方案**: 实现基于 FreshPER 的优先级回放缓冲:

```python
class ExperienceReplayBuffer:
    """优先级经验回放缓冲"""
    def __init__(self, capacity: int = 10000, alpha: float = 0.6):
        self._buffer: list[Trajectory] = []
        self._priorities: list[float] = []
        self._capacity = capacity
        self._alpha = alpha  # 优先级指数
    
    def push(self, trajectory: Trajectory):
        """添加完整游戏轨迹"""
        # 计算优先级: |score| × exp(-age/τ)
        priority = (abs(trajectory.score) + 1e-6) * math.exp(-len(self._buffer) / 500)
        self._buffer.append(trajectory)
        self._priorities.append(priority)
        # 容量管理
        if len(self._buffer) > self._capacity:
            # 移除最低优先级的轨迹
            min_idx = np.argmin(self._priorities)
            self._buffer.pop(min_idx)
            self._priorities.pop(min_idx)
    
    def sample(self, batch_size: int) -> list[Trajectory]:
        """按优先级采样"""
        probs = np.array(self._priorities) ** self._alpha
        probs /= probs.sum()
        indices = np.random.choice(len(self._buffer), batch_size, p=probs)
        return [self._buffer[i] for i in indices]
```

**影响文件**: 新建 `src/agent/replay_buffer.py`

**工作量**: M (3-4 天)

---

### 4.7 [P2] VLM 推理服务器添加预热和流式支持

**问题**: 当前 VLM 服务器启动后首次推理有 JIT 编译冷启动 (10-50x 慢)。推理期间完全阻塞, 无流式输出。

**方案**: 在 `GameAgentInference._load_model()` 中添加预热步骤, 并实现 SSE 流式端点。

**详见**: `src/inference/server.py` 的改进建议 (具体代码见第 3 节研究总结)。

**影响文件**: `src/inference/server.py`

**工作量**: S (2 天)

---

### 4.8 建议优先级汇总

| 编号 | 建议 | 优先级 | 工作量 | 影响范围 | 关键收益 |
|------|------|--------|-------|---------|---------|
| 4.1 | 黑板模式共享上下文 | P0 | S (2-3d) | hybrid_agent.py | 标准化组件间通信 |
| 4.2 | 步骤历史持久化 | P0 | S (2-3d) | memory.py (新建) | 可回放、分析、训练 |
| 4.3 | 跨会话情景检索 | P1 | M (4-5d) | memory.py, llm_agent.py | 减少重复错误 |
| 4.4 | 数据集自动持久化 | P1 | S (1-2d) | dataset_writer.py | 闭环数据收集 |
| 4.5 | 可插拔决策注册表 | P2 | M (4-5d) | registry.py, hybrid_agent.py | 开闭原则, 易扩展 |
| 4.6 | 经验回放缓冲 | P2 | M (3-4d) | replay_buffer.py | 利用自身经验改进 |
| 4.7 | VLM预热+流式 | P2 | S (2d) | server.py | 降低推理延迟 |

---

## 5. 架构演进路线图

### 路线图总览

```
现在 (Phase 0)          Phase 1 (Weeks 1-2)      Phase 2 (Weeks 3-4)        Phase 3 (Weeks 5-6)
─────────────────       ──────────────────       ──────────────────         ──────────────────
                        黑板模式                    跨会话情景检索               经验回放训练
文档化现有架构          步骤持久化                   语义记忆                    在线学习循环
                                                  VLM预热+流式                多GPU模型服务
稳定性修复              数据集自动写入                                            A2A 协议支持
```

### Phase 0: 现状文档化与稳定 (Week 0)

**目标**: 清晰地记录现有架构, 修复已知问题。

| 任务 | 交付物 | 依赖 |
|------|--------|------|
| 本文档审查与批准 | 最终版 `multi-agent-communication-and-memory-architecture.md` | 本文 |
| VLM 推理 4-bit 修复 | 修复 `server.py` 的 OOM 问题 (实验报告 §Mode 2) | 无 |
| HybridAgent 测试覆盖 | `tests/test_hybrid_agent.py` (当前测试缺口 #1) | 无 |
| VLM 规则提取 prompt 调优 | 修复模式 4 的 JSON schema 不匹配 (实验报告 §Mode 4) | 无 |

**验收标准**:
- [ ] 文档通过技术审查
- [ ] VLM 推理在 4-bit 模式下无 OOM
- [ ] HybridAgent 至少 3 种模式有单元测试
- [ ] 模式 4 规则提取返回有效 RuleSet

---

### Phase 1: 通信与记忆基础设施 (Weeks 1-2)

**目标**: 建立标准化的组件通信机制和持久化记忆。

**Week 1: 黑板模式 + 工作记忆**

```
Day 1-2: AgentContext 数据类设计
Day 2-3: WorkingMemory 实现 (结构化历史记录)
Day 3-4: 修改 HybridAgent 使用 AgentContext
Day 4-5: 修改所有 _decide_* 方法签名
```

**Week 2: 步骤持久化 + 数据集自动写入**

```
Day 1-2: SQLite 情景记忆表设计 + EpisodicMemory 实现
Day 2-3: 游戏步骤自动写入 SQLite
Day 3-4: DatasetWriter + 自动 JSONL 写入
Day 4-5: 集成测试 + 验证无回归
```

**关键架构变更**:

```
Before:                             After:
hybrid_agent.py                     hybrid_agent.py
  run_game():                         run_game():
    for step:                           ctx = AgentContext(...)
      state = observe()                 for step:
      decision = decide(state, ss)          state = observe() → ctx
      execute(decision)                     working_mem.push_action(...)
                                            decision = decide(ctx)
                                            execute(decision)
                                          episodic_mem.record_step(...)
```

**交付物**:
- [ ] `AgentContext` 数据类
- [ ] `WorkingMemory` 结构化历史 (替换 `history[-20:]`)
- [ ] `EpisodicMemory` SQLite 持久化
- [ ] `DatasetWriter` 自动 JSONL 写入
- [ ] 所有现有测试通过 (`pytest`)

---

### Phase 2: 跨会话记忆与推理优化 (Weeks 3-4)

**目标**: 智能体能够从过去的游戏中学习, 减少重复错误。

**Week 3: 跨会话情景检索**

```
Day 1-2: 情景记忆向量嵌入 (sqlite-vec / ChromaDB)
Day 2-3: 会话摘要生成 (LLM 分析)
Day 3-4: 历史经验注入 system prompt
Day 4-5: 集成测试 (多会话场景)
```

**Week 4: VLM 预热 + 语义记忆**

```
Day 1-2: VLM 预热实现 + 流式 SSE 端点
Day 2-3: 语义记忆实现 (ChromaDB)
Day 3-4: 游戏后知识提取管道 (LLM 分析)
Day 4-5: 可插拔决策注册表
```

**关键架构变更**:

```
Before:                             After:
LLMAgent.run_game():                LLMAgent.run_game():
  history = []                        ctx = AgentContext()
  for step:                           ctx.working_memory.load_history(game_id)
    prompt = TEXT_PROMPT              for step:
              .format(history=[])        similar = episodic.find_similar(...)
    → DeepSeek → action                  prompt = TEXT_PROMPT.format(
                                          history=ctx.working_memory.recent_actions(5),
                                          previous_sessions=similar)
                                        → DeepSeek → action
                                      semantic.add_knowledge(summary)
                                      episodic.record_step(...)
```

**交付物**:
- [ ] 跨会话情景检索功能
- [ ] 历史经验注入 system prompt
- [ ] VLM 推理预热 (首次推理加速 10-50x)
- [ ] 语义知识提取管道
- [ ] 可插拔决策注册表

**风险**:
- sqlite-vec 在 ARM Mac vs Linux x86 的兼容性
- 向量嵌入的延迟可能影响游戏步进周期

---

### Phase 3: 在线学习与系统强化 (Weeks 5-6)

**目标**: 智能体从自身游戏经验中持续改进。

**Week 5: 经验回放缓冲**

```
Day 1-2: FreshPER 优先级回放实现
Day 2-3: 轨迹收集 (完整游戏会话)
Day 3-4: 回放缓冲管理 (采样/训练)
Day 4-5: 集成到训练流水线
```

**Week 6: 多模型服务 + 生产强化**

```
Day 1-2: vLLM / Triton 集成评估
Day 2-3: OpenAI 兼容 API 格式迁移
Day 3-4: Prometheus 指标 + 监控
Day 4-5: 性能基准测试 + 文档
```

**交付物**:
- [ ] 经验回放缓冲 (FreshPER 算法)
- [ ] 自动数据集→训练管线
- [ ] (可选) vLLM 集成
- [ ] 性能监控仪表板
- [ ] 最终架构文档更新

**成功指标**:

| 指标 | Phase 1 目标 | Phase 2 目标 | Phase 3 目标 |
|------|-------------|-------------|-------------|
| 会话间学习 | ❌ 不支持 | ✅ 至少减少 30% 重复错误 | ✅ 跨游戏策略迁移 |
| 组件耦合度 | 紧耦合 | 🟡 黑板解耦 | 🟢 松耦合注册表 |
| 数据利用率 | 0% (丢弃) | ✅ 100% 会话持久化 | ✅ 回放训练闭环 |
| 推理延迟 (VLM) | ~10.5s (cold) | 🟡 ~2s (warm) | ✅ ~1s (optimized) |
| 测试覆盖率 | 缺口在 HybridAgent | ✅ 覆盖所有模式 | ✅ 记忆系统测试 |

---

### 附录 A: 记忆系统 API 参考

```python
# ============================================================
# WorkingMemory — src/agent/memory.py
# ============================================================

class WorkingMemory:
    """工作记忆: 当前会话的短期上下文"""
    
    def __init__(self, max_history: int = 60, ttl_seconds: int = 300): ...
    
    def push_action(self, state: dict, action: dict, 
                    screenshot: bytes | None = None) -> None: ...
    
    def recent_actions(self, n: int = 5) -> list[StepRecord]: ...
    
    def detect_stuck(self, current_pos: tuple[float, float], 
                     threshold: float = 0.05) -> bool: ...
    
    def to_prompt_context(self, n: int = 5) -> str: ...
    
    @property
    def is_expired(self) -> bool: ...
    
    def reset(self) -> None: ...


# ============================================================
# EpisodicMemory — src/agent/memory.py
# ============================================================

class EpisodicMemory:
    """情景记忆: 跨会话游戏历史"""
    
    def __init__(self, db_path: str = "game_memory.db"): ...
    
    async def start_session(self, game_id: str) -> str:
        """开始新会话, 返回 session_id"""
    
    async def record_step(self, session_id: str, step_number: int,
                          state: dict, action: dict, mode: str,
                          latency_ms: float, screenshot: bytes | None = None) -> None:
        """记录一步"""
    
    async def end_session(self, session_id: str, result: str, score: float) -> None:
        """结束会话, 触发摘要"""
    
    async def find_similar(self, game_id: str, embedding: list[float],
                           top_k: int = 3) -> list[SessionSummary]:
        """查找相似历史会话"""
    
    async def get_session_summary(self, session_id: str) -> SessionSummary | None:
        """获取会话摘要"""


# ============================================================
# SemanticMemory — src/agent/memory.py
# ============================================================

class SemanticMemory:
    """语义记忆: 游戏机制知识"""
    
    def __init__(self, persist_dir: str = "./semantic_memory"): ...
    
    async def query(self, query: str, game_id: str | None = None,
                    top_k: int = 5) -> list[KnowledgeEntry]:
        """语义搜索"""
    
    async def add_knowledge(self, content: str, metadata: dict,
                            importance: float = 0.5) -> str:
        """添加知识条目"""
    
    async def extract_from_session(self, session_summary: str,
                                   game_id: str) -> list[KnowledgeEntry]:
        """从会话摘要中提取知识"""


# ============================================================
# ProceduralMemory — src/agent/memory.py
# ============================================================

class ProceduralMemory:
    """程序记忆: IF/THEN 规则"""
    
    def __init__(self, json_path: str = "procedural_rules.json"): ...
    
    def match(self, state: dict, working: WorkingMemory) -> ProceduralRule | None:
        """匹配高优先级规则"""
    
    def learn(self, rule: ProceduralRule) -> None:
        """学习新规则 (VLM/API 提取)"""
    
    def update_success_rate(self, rule_name: str, succeeded: bool) -> None:
        """更新成功率 (强化学习信号)"""
```

---

### 附录 B: 通信协议决策矩阵

| 场景 | 推荐协议 | 备选 | 理由 |
|------|---------|------|------|
| 进程内组件 A → B | 黑板模式 | 直接调用 | 解耦 + 可观测 |
| 进程内管道处理 | asyncio.Queue | 回调 | 背压 + 异步 |
| VLM 推理服务器 | REST/OpenAI 兼容 | gRPC | 生态标准 |
| 外部 LLM/VLM API | REST/OpenAI | — | 已有 SDK |
| 跨语言组件 | gRPC | REST | 性能 + 类型安全 |
| 10+ 组件事务 | 事件总线 | 消息队列 | 最大解耦 |
| 跨组织代理 | A2A | REST | 标准化互操作 |

---

> **本文档是 SmallGameAgent 多代理通信和记忆架构的设计参考。建议按路线图的 Phase 顺序实施, 每一步都应保持向后兼容。**
