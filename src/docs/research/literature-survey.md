# Multi-Agent Communication Research — Literature Survey

> Generated: 2026-07-05 | Sources: 7 parallel subagent searches

## 1. Agent-to-Agent Communication Protocols

### Agent-to-Agent Protocol (A2A)
- **Source**: Google → Linux Foundation, v1.0 (June 2026)
- **Core Idea**: JSON-RPC 2.0 over HTTP(S) for cross-framework agent interop
- **Discovery**: Agent Cards at `/.well-known/agent-card`
- **Task Lifecycle**: `submitted → working → completed/failed`
- **SDKs**: Python (1.0 GA), Go (1.0 GA), Java (Beta), .NET (Preview), JS/TS (v0.3)
- **Relevance**: Standard for future cross-org agent communication

### AutoGen Group Chat (Microsoft)
- **Source**: GitHub `microsoft/autogen`, 2024-2026
- **Core Idea**: Publish/subscribe topic-based communication with LLM-driven speaker selection
- **Pattern**: `SelectorGroupChat` — manager selects next speaker from participant pool
- **Relevance**: Topic-based routing for heterogeneous agents (API + local)

### LangGraph Checkpoint + Store
- **Source**: LangChain docs, 2024-2026
- **Core Idea**: Two-tier persistence: Checkpointer (thread-scoped) + Store (cross-thread)
- **Memory**: Reducers for parallel agent writes; `invoke(None)` for resume
- **Relevance**: State management pattern for multi-agent game sessions

## 2. Memory Architectures for AI Agents

### MemGPT / Letta (OS-Inspired)
- **Source**: `arxiv.org/abs/2310.08560`, GitHub `letta-ai/letta`
- **Core Idea**: Three-tier: Core Memory (in-context blocks), Recall Memory (message DB), Archival Memory (vector store)
- **Key Pattern**: Agent-directed memory management — self-editing memory blocks
- **Relevance**: Adopted as the 4-tier memory design in SmallGameAgent

### CoALA (Cognitive Architectures for Language Agents)
- **Source**: `arxiv.org/abs/2309.02427`
- **Core Idea**: Working Memory + Episodic Memory + Semantic Memory + Procedural Memory
- **Decision Loop**: propose → evaluate → select → execute
- **Relevance**: Theoretical foundation for the memory architecture

### BMAM (Brain-inspired Multi-Agent Memory)
- **Source**: ACL 2026 Findings, `aclanthology.org/2026.findings-acl.1973`
- **Core Idea**: Episodic, semantic, salience-aware, control-oriented subsystems
- **Result**: 78.45% on LoCoMo benchmark; 87.5% identity integrity on Soul Portability Test

### AgeMem (Agentic Memory with RL Training)
- **Source**: ACL 2026 Long Paper, `aclanthology.org/2026.acl-long.981`
- **Core Idea**: Unified LTM/STM using RL-trained memory operations; 3-stage progressive RL with GRPO
- **Relevance**: Reinforcement learning for memory management

## 3. Heterogeneous Multi-Agent Systems

### The Vision Wormhole
- **Source**: `arxiv.org/abs/2602.15382`
- **Core Idea**: Universal Visual Codec maps reasoning traces into latent images, bridging text-only and vision-capable models
- **Relevance**: Directly applicable to API text agent ↔ local VLM communication

### COS-PLAY (Game-Playing Multi-Agent)
- **Source**: GitHub `wuxiyang1996/cos-play`
- **Core Idea**: Two-agent co-evolution: Decision Agent retrieves from learnable Skill Bank
- **Games**: 2048, Candy Crush, Tetris, Super Mario Bros
- **Relevance**: Closest architecture to SmallGameAgent's game-playing design

## 4. VLM Inference Serving

### vLLM with LoRA Hot-Swapping
- **Source**: `docs.vllm.ai`, v0.23.0 (2026)
- **Core Idea**: PagedAttention + continuous batching + runtime LoRA adapter swap
- **Pattern**: OpenAI-compatible `/v1/chat/completions` with `lora_name` extra_body
- **Relevance**: Production VLM serving pattern; adopted for SmallGameAgent's inference server

### EPD (Encoder-Prefill-Decode) Disaggregation
- **Source**: SudoAll VLM Phase Mismatch analysis
- **Core Idea**: Split VLM inference into encoder (FLOPs), prefill (HBM), decode (bandwidth) on separate GPUs
- **Relevance**: Future optimization for multi-model serving

## 5. Production Memory Patterns

### SQLite + FTS5 + sqlite-vec
- **Source**: `github.com/sqliteai/sqlite-memory`, `github.com/asg017/sqlite-vec`
- **Core Idea**: Single-file zero-infrastructure vector search; FTS5 for keyword; vec0 for semantic
- **Pattern**: WAL mode + hybrid FTS/vector queries with adaptive ranking
- **Relevance**: Chosen as SmallGameAgent's memory backend (zero external services)

### Zep Temporal Knowledge Graph
- **Source**: `getzep.com`, GitHub `getzep/graphiti`
- **Core Idea**: Bitemporal facts (valid_at + learned_at); Episode + Semantic Entity + Community subgraphs
- **Result**: 94.8% DMR benchmark; beats MemGPT by 18.5% on temporal reasoning
- **Relevance**: Reference for future temporal memory upgrade

---

## References

1. Zhang, S. et al. (2023). "CoALA: Cognitive Architectures for Language Agents." arXiv:2309.02427.
2. Packer, C. et al. (2023). "MemGPT: Towards LLMs as Operating Systems." arXiv:2310.08560.
3. Ryu, J. et al. (2026). "BMAM: Brain-inspired Multi-Agent Memory." ACL 2026 Findings.
4. Li, M. et al. (2026). "AgeMem: Agentic Memory via Reinforcement Learning." ACL 2026 Long.
5. Wu, X. et al. (2026). "COS-PLAY: Co-evolving Skill Bank and Decision Agent for Games." GitHub.
6. Google LLC. (2026). "Agent-to-Agent Protocol v1.0." Linux Foundation.
7. Kwon, W. et al. (2026). "vLLM: Easy, Fast, and Cheap LLM Serving with PagedAttention."
8. An, Y. et al. (2026). "The Vision Wormhole: Bridging Heterogeneous Models via Visual Codec." arXiv:2602.15382.
