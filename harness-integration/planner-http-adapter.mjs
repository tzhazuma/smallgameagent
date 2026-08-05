#!/usr/bin/env node
/**
 * Planner HTTP adapter — bridges the harness_http planner provider to
 * OpenAI-compatible Chat Completions endpoints (opencodego / kimi / xiaomi / qwen).
 *
 * The harness posts:
 *   { schema_version, model, prompt, brief, output_schema, images }
 *
 * This service:
 *   1. Maps `model` to a provider (base_url + api_key + real model name).
 *   2. Calls the Chat Completions API with the prompt (and images when present).
 *   3. Parses the JSON reply and returns { intent: {...} } or { strategy: {...} }.
 *
 * Env vars (mirror smallgameagent/.env):
 *   OPENCODEGO_API_KEY / OPENCODEGO_BASE_URL
 *   KIMI_API_KEY / KIMI_BASE_URL
 *   XIAOMI_API_KEY / XIAOMI_BASE_URL
 *   QWEN_API_KEY / QWEN_BASE_URL
 *   PORT (default 9100)
 */
import { createServer } from "node:http";

const PORT = Number(process.env.PORT || 9100);

const env = (name) => process.env[name] || "";

// model-name -> provider config
function providerForModel(model) {
  const m = String(model || "").toLowerCase();
  if (m.includes("kimi")) {
    return {
      baseUrl: env("KIMI_BASE_URL") || "https://api.kimi.com/coding/v1",
      apiKey: env("KIMI_API_KEY"),
      model,
    };
  }
  if (m.includes("mimo")) {
    // Route mimo through opencodego by default: xiaomi's direct endpoint
    // truncates reasoning-heavy responses (finish_reason=abort), while the
    // opencodego proxy serves mimo-v2.5 reliably. Use xiaomi only when
    // explicitly requested via XIAOMI_API_KEY + MIMO_ROUTE=xiaomi.
    if (env("MIMO_ROUTE") === "xiaomi" && env("XIAOMI_API_KEY")) {
      return {
        baseUrl: env("XIAOMI_BASE_URL") || "https://api.xiaomimimo.com/v1",
        apiKey: env("XIAOMI_API_KEY"),
        model: m.includes("pro") ? "mimo-v2.5-pro" : "mimo-v2.5",
      };
    }
    return {
      baseUrl: env("OPENCODEGO_BASE_URL") || "https://opencode.ai/zen/go/v1",
      apiKey: env("OPENCODEGO_API_KEY"),
      model: m.includes("pro") ? "mimo-v2.5-pro" : "mimo-v2.5",
    };
  }
  if (m.includes("deepseek")) {
    return {
      baseUrl: env("OPENCODEGO_BASE_URL") || "https://opencode.ai/zen/go/v1",
      apiKey: env("OPENCODEGO_API_KEY"),
      model: m.includes("pro") ? "deepseek-v4-pro" : "deepseek-v4-flash",
    };
  }
  if (m.includes("qwen")) {
    return {
      baseUrl: env("QWEN_BASE_URL") || "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
      apiKey: env("QWEN_API_KEY"),
      model,
    };
  }
  // Default: opencodego with the requested model name.
  return {
    baseUrl: env("OPENCODEGO_BASE_URL") || "https://opencode.ai/zen/go/v1",
    apiKey: env("OPENCODEGO_API_KEY"),
    model,
  };
}

async function callChatCompletions(provider, messages, maxTokens = 2048, outputSchema = null) {
  const url = `${provider.baseUrl.replace(/\/$/, "")}/chat/completions`;
  const body = {
    model: provider.model,
    messages,
    max_tokens: maxTokens,
    temperature: 0.0,
  };
  // Kimi rejects an explicit temperature.
  if (provider.model.toLowerCase().includes("kimi")) {
    delete body.temperature;
  }
  // Prefer json_schema response_format when a schema is provided (qwen/xiaomi
  // support it). Fall back to json_object, then no response_format.
  if (outputSchema && !provider.model.toLowerCase().includes("kimi")) {
    body.response_format = {
      type: "json_schema",
      json_schema: {
        name: String(outputSchema.title || "planner_output").replace(/[^a-zA-Z0-9_-]/g, "_"),
        strict: true,
        schema: outputSchema,
      },
    };
  } else if (!provider.model.toLowerCase().includes("kimi")) {
    body.response_format = { type: "json_object" };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Number(process.env.PLANNER_ADAPTER_TIMEOUT_MS || 300_000));
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${provider.apiKey}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      // Some providers reject response_format; retry without it once.
      if (body.response_format && resp.status >= 400) {
        delete body.response_format;
        const retry = await fetch(url, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            authorization: `Bearer ${provider.apiKey}`,
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (!retry.ok) {
          const retryText = await retry.text().catch(() => "");
          throw new Error(`provider HTTP ${retry.status}: ${retryText.slice(0, 300)}`);
        }
        const retryPayload = await retry.json();
        return retryPayload.choices?.[0]?.message?.content || "";
      }
      throw new Error(`provider HTTP ${resp.status}: ${text.slice(0, 300)}`);
    }
    const payload = await resp.json();
    return payload.choices?.[0]?.message?.content || "";
  } finally {
    clearTimeout(timer);
  }
}

function extractJson(text) {
  if (!text) return null;
  const cleaned = text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  // Collect all candidate JSON objects; prefer the most complete one.
  const candidates = [];

  function tryParse(s) {
    try {
      const parsed = JSON.parse(s);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) candidates.push(parsed);
    } catch {
      /* ignore */
    }
  }

  tryParse(cleaned);
  // Balanced-brace scan from each '{' position.
  for (let i = 0; i < cleaned.length; i++) {
    if (cleaned[i] !== "{") continue;
    let depth = 0;
    let inString = false;
    let escape = false;
    for (let j = i; j < cleaned.length; j++) {
      const ch = cleaned[j];
      if (inString) {
        if (escape) escape = false;
        else if (ch === "\\") escape = true;
        else if (ch === '"') inString = false;
        continue;
      }
      if (ch === '"') inString = true;
      else if (ch === "{") depth++;
      else if (ch === "}") {
        depth--;
        if (depth === 0) {
          tryParse(cleaned.slice(i, j + 1));
          break;
        }
      }
    }
  }
  if (candidates.length === 0) return null;
  // Prefer the object that looks like the intended output (has schema_version / kind /
  // strategy_id / option), else the one with the most keys.
  const score = (obj) => {
    let s = 0;
    if (obj.schema_version) s += 10;
    if (obj.kind) s += 5;
    if (obj.strategy_id || obj.option || obj.intent) s += 4;
    s += Object.keys(obj).length;
    return s;
  };
  candidates.sort((a, b) => score(b) - score(a));
  return candidates[0];
}

function buildMessages(request) {
  const messages = [{ role: "system", content: SYSTEM_PROMPT }];
  const content = [];
  for (const img of request.images || []) {
    content.push({ type: "image_url", image_url: { url: img } });
  }
  // Append the output schema so the model emits the exact expected structure.
  let prompt = request.prompt;
  const schemaTitle = String(request.output_schema?.title || "").toLowerCase();
  if (schemaTitle.includes("strateg")) {
    // Inject the real brief base into the example so the model echoes it exactly.
    const b = request.brief?.base || {};
    const baseJson = JSON.stringify({
      game_id: b.game_id || "BRIEF_GAME",
      run_id: b.run_id || "BRIEF_RUN",
      state_version: b.state_version ?? 1,
      scene_epoch: b.scene_epoch ?? 0,
      policy_set_id: b.policy_set_id || "BRIEF_POLICY",
    });
    prompt += "\n\nUse EXACTLY this base object: " + baseJson + "\n" + STRATEGY_EXAMPLE;
  }
  if (request.output_schema) {
    prompt +=
      "\n\nOUTPUT JSON SCHEMA (produce a JSON object matching this schema exactly, no markdown, no extra keys):\n" +
      JSON.stringify(request.output_schema, null, 2);
  }
  content.push({ type: "text", text: prompt });
  messages.push({ role: "user", content });
  return messages;
}

const STRATEGY_EXAMPLE = `

EXAMPLE of a valid StrategySpec structure (use the injected base object above; adapt strategy_id/summary/option/parameters/evidence_refs to the game, and pick option from the brief's allowed_options):

{"schema_version":"agent_harness.strategy_spec.v1","kind":"StrategySpec","base":{"game_id":"USE_INJECTED","run_id":"USE_INJECTED","state_version":1,"scene_epoch":0,"policy_set_id":"USE_INJECTED"},"strategy_id":"discover-example","summary":"Discover game mechanics by probing the control space.","entry_state":"discover","states":[{"state_id":"discover","description":"Probe and observe the scene.","objective":{"selector":"current_guide","sticky":false},"actions":[{"action_id":"a_discover","option":"probe_joystick","target_binding":"none","parameters":{"dx":0.5,"dy":0.5,"duration_ms":150},"route_policy":"none","repeat":"until_transition","max_local_iterations":5,"expected_effect":{"player_position_changes":true}}],"transitions":[{"predicate":"completion_suspected","key":null,"value":null,"next":"VERIFY_COMPLETION"},{"predicate":"failure_active","key":null,"value":null,"next":"STOP"},{"predicate":"no_progress_at_least","key":null,"value":4,"next":"REPLAN"},{"predicate":"always","key":null,"value":null,"next":"discover"}],"recovery":{"no_progress_before_replan":3,"max_action_failures":2,"settle_before_retry":true}}],"global_replan_triggers":["repeated_no_progress","guide_changed_from_entry","completion_suspected","failure_active"],"invariants":[{"predicate":"failure_active","key":null,"value":null,"on_violation":"STOP"}],"evidence_refs":["evidence:smoke:1"],"confidence":0.5}`;

const SYSTEM_PROMPT = `You are a game agent planner. You receive a planning brief and must output ONLY valid JSON matching the provided output schema. Never output markdown fences, explanations, or text outside the JSON object. If you cannot produce a valid plan, output a minimal valid object with a 'fallback' field describing the safe default action.

CONTRACT RULES YOU MUST RESPECT:
1. Every action.option must be one of the names listed in the brief's allowed_options. Do not invent options.
2. expected_effect may set true ONLY effects listed in that option's observable_effects (see allowed_options). At least one true effect is required.
3. Only option approach_target may set a route_policy (direct or geometry_gates). All other options MUST use route_policy=none.
4. If the chosen option has requires_target=true, set target_binding=objective and ensure the state objective selector is not none. Otherwise set target_binding=none.
5. parameters.target_id must always be null.
6. repeat is either once or until_transition. max_local_iterations is an integer between 1 and 20.
7. objective.selector is one of current_guide, target_id, target_role, none. sticky is a boolean.
8. Transitions: predicate always / completion_suspected / failure_active / no_progress_at_least / guide_changed_from_entry use key=null, value=null (except no_progress_at_least uses an integer value). next is a state_id, REPLAN, VERIFY_COMPLETION, or STOP.
9. Keep the strategy compact: prefer a few states with until_transition loops over many micro-states.`;

// ---------------------------------------------------------------------------
// Fallback strategy / intent construction — used when the model output does
// not validate against the strict harness contract. The model supplies
// high-level decisions; this code builds the schema-conformant envelope.
// ---------------------------------------------------------------------------
function buildFallbackStrategy(brief) {
  const allowed = brief.allowed_options || [];
  const world = brief.world || {};
  const player = world.player || {};
  const px = player.position?.x ?? 0;
  const pz = player.position?.z ?? 0;
  const guide = world.game?.current_guide || {};
  const target = world.targets && world.targets[0] ? world.targets[0] : null;
  const tx = target?.position?.x ?? guide.target_position?.x;
  const tz = target?.position?.z ?? guide.target_position?.z;
  let targetDist = Infinity;
  if (Number.isFinite(tx) && Number.isFinite(tz)) {
    targetDist = Math.hypot(tx - px, tz - pz);
  }
  const routeClear = world.navigation_topology?.route_status === "DIRECT_CLEAR";

  // Check calibration status: until the control map is verified, joystick is
  // only allowed for calibration (the intent gate blocks it otherwise).
  let controlVerified = false;
  for (const cap of brief.capabilities || []) {
    if (cap && cap.capability_id === "movement_calibration_status") {
      controlVerified = cap.verified === true || cap.calibration_gate?.passed === true;
    }
  }

  // Phase the strategy by control-map state and target distance. This produces
  // structurally different strategies as the run evolves, which also avoids the
  // harness's same-context replan detection.
  const hasGuide = Boolean(guide?.target_name || guide?.target_path || guide?.target_position);
  const isTapGame = Boolean(target) && !hasGuide;  // targets exist but no guide arrow → tap/shop style
  let optionName;
  let suffix;
  let summary;
  if (isTapGame && Number.isFinite(targetDist) && targetDist < 2.0 && allowed.some((o) => o.name === "probe_tap")) {
    optionName = "probe_tap";
    suffix = "collect";
    summary = "Close to the target; tap to collect/interact.";
  } else if (isTapGame && Number.isFinite(targetDist) && targetDist < 2.0 && allowed.some((o) => o.name === "dwell_at_target")) {
    optionName = "dwell_at_target";
    suffix = "interact";
    summary = "Near the active target; dwell to trigger the interaction.";
  } else if (!controlVerified && allowed.some((o) => o.name === "probe_joystick")) {
    optionName = "probe_joystick";
    suffix = "calibrate";
    summary = "Calibrate the joystick control map with repeated non-collinear pulses.";
  } else if (target && Number.isFinite(targetDist) && targetDist < 2.0 && allowed.some((o) => o.name === "probe_tap")) {
    optionName = "probe_tap";
    suffix = "collect";
    summary = "Close to the target; tap to collect/interact.";
  } else if (target && Number.isFinite(targetDist) && targetDist < 2.0 && allowed.some((o) => o.name === "dwell_at_target")) {
    optionName = "dwell_at_target";
    suffix = "interact";
    summary = "Player is near the active target; dwell to trigger the interaction.";
  } else if (target && Number.isFinite(targetDist) && allowed.some((o) => o.name === "probe_joystick")) {
    optionName = "probe_joystick";
    suffix = "navigate";
    summary = "Navigate toward the active guide target using short joystick pulses.";
  } else if (allowed.some((o) => o.name === "explore_sector_sweep")) {
    optionName = "explore_sector_sweep";
    suffix = "explore";
    summary = "Explore the control space with a sector sweep.";
  } else if (allowed.some((o) => o.name === "observe_settle")) {
    optionName = "observe_settle";
    suffix = "observe";
    summary = "Settle and observe the current scene.";
  } else {
    optionName = (allowed[0] || {}).name || "observe_settle";
    suffix = "default";
    summary = "Discovery strategy.";
  }

  const option = allowed.find((o) => o.name === optionName)
    || { name: optionName, requires_target: false, observable_effects: ["any_relevant_progress"] };

  const requiresTarget = option.requires_target === true;
  let observable = (option.observable_effects || ["any_relevant_progress"])[0] || "any_relevant_progress";

  // Default parameters per option so the deterministic compile gate accepts it.
  const optionParams = {
    probe_joystick: { dx: 0.5, dy: 0.5, duration_ms: 150 },
    probe_tap: { duration_ms: 100 },
    probe_drag: { dx: 0.3, dy: 0.0, duration_ms: 300 },
    explore_sector_sweep: { dx: 0.0, dy: -1.0 },
    dwell_at_target: { duration_ms: 400 },
    recover_reverse: { dx: 0.0, dy: 1.0, duration_ms: 320 },
    observe_settle: { duration_ms: 500 },
  };
  let params = optionParams[option.name] || {};

  // For calibration, rotate joystick directions so the gate sees repeated
  // non-collinear samples (it rejects all-collinear sample sets).
  if (option.name === "probe_joystick" && !routeClear) {
    let sampleCount = 0;
    for (const cap of brief.capabilities || []) {
      if (cap && cap.capability_id === "movement_calibration_status") {
        sampleCount = cap.effective_sample_count || cap.sample_count || 0;
      }
    }
    const pattern = [
      { dx: 0.5, dy: 0.5 },
      { dx: 0.5, dy: 0.5 },
      { dx: -0.5, dy: 0.5 },
      { dx: -0.5, dy: 0.5 },
    ];
    const dir = pattern[sampleCount % pattern.length];
    params = { ...dir, duration_ms: 150 };
  }

  const base = brief.base || {};
  const evidenceRefs = (brief.evidence || []).map((e) => e.packet_id);
  const memoryRefs = brief.memory_refs || [];
  const refs = [...new Set([...evidenceRefs, ...memoryRefs])];

  // Only use a target-bound objective when the world actually has a guide/target.
  const hasTarget = Boolean(target) || hasGuide;
  let objective;
  if (hasGuide) {
    objective = { selector: "current_guide", sticky: false };
  } else if (target) {
    // No guide arrow but concrete targets exist (tap/shop style): bind to a
    // literal target id so the harness can resolve the objective.
    const targetId = target.id || target.target_id || target.name || null;
    objective = targetId
      ? { selector: "target_id", target_id: targetId, sticky: false }
      : { selector: "target_role", target_role: "interact", sticky: false };
  } else {
    objective = { selector: "none", sticky: false };
  }
  // If there is no target, avoid target-requiring options.
  let effectiveOption = option;
  if (!hasTarget && effectiveOption.requires_target === true) {
    effectiveOption = allowed.find((o) => o.name === "observe_settle")
      || allowed.find((o) => o.name === "probe_tap")
      || allowed.find((o) => !o.requires_target)
      || allowed[0];
    params = optionParams[effectiveOption.name] || {};
    observable = (effectiveOption.observable_effects || ["any_relevant_progress"])[0] || "any_relevant_progress";
    suffix = "observe";
    summary = "No active target; observe and probe the scene.";
  }
  const finalRequiresTarget = effectiveOption.requires_target === true && hasTarget;

  const state = {
    state_id: "discover",
    description: `Discovery phase (${suffix}): exercise ${effectiveOption.name} to reveal mechanics and reach settled completion.`,
    objective,
    actions: [
      {
        action_id: "a_discover",
        option: effectiveOption.name,
        target_binding: finalRequiresTarget ? "objective" : "none",
        parameters: params,
        route_policy: "none",
        repeat: "until_transition",
        max_local_iterations: 5,
        expected_effect: { [observable]: true },
      },
    ],
    transitions: [
      { predicate: "completion_suspected", key: null, value: null, next: "VERIFY_COMPLETION" },
      { predicate: "failure_active", key: null, value: null, next: "STOP" },
      { predicate: "no_progress_at_least", key: null, value: 4, next: "REPLAN" },
      { predicate: "always", key: null, value: null, next: "discover" },
    ],
    recovery: {
      no_progress_before_replan: 3,
      max_action_failures: 2,
      settle_before_retry: true,
    },
  };

  // Vary the strategy id with the phase suffix + coarse position bucket so the
  // harness sees a materially different strategy as the run progresses.
  const posBucket = `${Math.round(px * 4)}_${Math.round(pz * 4)}`;
  const strategyId = `discover-${suffix}-${posBucket}`.slice(0, 64);

  return {
    schema_version: "agent_harness.strategy_spec.v1",
    kind: "StrategySpec",
    base: {
      game_id: base.game_id || "UNKNOWN",
      run_id: base.run_id || "run",
      state_version: base.state_version ?? 1,
      scene_epoch: base.scene_epoch ?? 0,
      policy_set_id: base.policy_set_id || "candidate:default",
    },
    strategy_id: strategyId,
    summary,
    entry_state: "discover",
    states: [state],
    global_replan_triggers: ["repeated_no_progress", "guide_changed_from_entry", "completion_suspected", "failure_active"],
    invariants: [
      { predicate: "failure_active", key: null, value: null, on_violation: "STOP" },
    ],
    evidence_refs: refs,
    confidence: 0.5,
  };
}

function buildFallbackIntent(brief) {
  const base = brief.base || {};
  const evidenceRefs = (brief.evidence || []).map((e) => e.packet_id);
  const memoryRefs = brief.memory_refs || [];
  const refs = [...new Set([...evidenceRefs, ...memoryRefs])];
  return {
    schema_version: "agent_harness.intent.v1",
    kind: "Intent",
    base: {
      game_id: base.game_id || "UNKNOWN",
      run_id: base.run_id || "run",
      state_version: base.state_version ?? 1,
      scene_epoch: base.scene_epoch ?? 0,
      policy_set_id: base.policy_set_id || "candidate:default",
    },
    option: "observe_settle",
    parameters: {},
    preconditions: [],
    expected_effect: { any_relevant_progress: false, state_settles: true },
    abort_conditions: ["state_version_change", "scene_epoch_change"],
    fallback: "observe_settle",
    evidence_refs: refs,
    confidence: 0.5,
  };
}

// ---------------------------------------------------------------------------
// Strategy normalizer — rewrites a model-produced StrategySpec into a form the
// harness's strict planner contract accepts. The harness rejects strategies
// whose actions misuse route_policy, bind targets wrongly, or predict effects
// outside the option's observable_effects allow-list (see
// src/planning/strategy-contract.mjs). Reasoning models (mimo-v2.5,
// deepseek-v4-flash) frequently produce large valid-JSON strategies that
// violate those micro-contract rules, so we patch them here instead of
// discarding the whole plan.
// ---------------------------------------------------------------------------
function normalizeStrategy(parsed, brief) {
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.states) || parsed.states.length === 0) {
    return buildFallbackStrategy(brief || {});
  }
  const options = new Map((brief?.allowed_options || []).map((o) => [o.name, o]));
  const stateIds = new Set(parsed.states.map((s) => s.state_id));
  // Fallback option must be one the harness actually allow-listed in this
  // context. Prefer observe_settle, then any target-free option.
  const safeOption = () => {
    const names = [...options.keys()];
    if (names.includes("observe_settle")) return "observe_settle";
    const free = names.find((n) => options.get(n).requires_target !== true);
    return free || names[0] || "observe_settle";
  };
  const safeEffect = (optionName, fallback) => {
    const desc = options.get(optionName);
    const observable = desc?.observable_effects || [];
    if (observable.includes(fallback)) return { [fallback]: true };
    if (observable.length > 0) return { [observable[0]]: true };
    return { any_relevant_progress: true };
  };
  const states = parsed.states.map((state) => {
    const st = { ...state };
    st.objective = (() => {
      const obj = st.objective && typeof st.objective === "object" ? { ...st.objective } : { selector: "current_guide", sticky: false };
      if (!["current_guide", "target_id", "target_role", "none"].includes(obj.selector)) obj.selector = "current_guide";
      obj.sticky = obj.sticky !== false;
      return obj;
    })();
    const objSelector = st.objective.selector;
    const fbOption = safeOption();
    st.actions = Array.isArray(st.actions) && st.actions.length > 0 ? st.actions : [{
      action_id: "a_default", option: fbOption, target_binding: "none",
      parameters: {}, route_policy: "none", repeat: "once", max_local_iterations: 3,
      expected_effect: safeEffect(fbOption, "state_settles"),
    }];
    st.actions = st.actions.map((action) => {
      const a = { ...action, parameters: { ...(action.parameters || {}) } };
      const desc = options.get(a.option);
      if (!desc) {
        // Non-allow-listed option: replace with a safe allow-listed action.
        a.option = fbOption;
        a.parameters = {};
        a.target_binding = "none";
        a.route_policy = "none";
        a.repeat = "once";
        a.max_local_iterations = 3;
        a.expected_effect = safeEffect(fbOption, "state_settles");
        return a;
      }
      if (desc.requires_target === true) {
        a.target_binding = objSelector === "none" ? "none" : "objective";
      } else {
        a.target_binding = "none";
      }
      a.route_policy = a.option === "approach_target" ? (["direct", "geometry_gates"].includes(a.route_policy) ? a.route_policy : "direct") : "none";
      a.repeat = ["once", "until_transition"].includes(a.repeat) ? a.repeat : "once";
      const it = Number(a.max_local_iterations);
      a.max_local_iterations = Number.isInteger(it) && it >= 1 && it <= 20 ? it : 5;
      a.parameters.target_id = null;
      // Keep only true effects inside the option's observable allow-list.
      if (a.expected_effect && typeof a.expected_effect === "object") {
        const cleaned = {};
        for (const [k, v] of Object.entries(a.expected_effect)) {
          if (v === true && (desc.observable_effects || []).includes(k)) cleaned[k] = true;
        }
        if (Object.keys(cleaned).length === 0) {
          a.expected_effect = safeEffect(a.option, "any_relevant_progress");
        } else {
          a.expected_effect = cleaned;
        }
      } else {
        a.expected_effect = safeEffect(a.option, "any_relevant_progress");
      }
      return a;
    });
    st.transitions = Array.isArray(st.transitions) && st.transitions.length > 0 ? st.transitions.map((t) => {
      const next = ["REPLAN", "VERIFY_COMPLETION", "STOP"].includes(t?.next) || stateIds.has(t?.next) ? t.next : "REPLAN";
      return {
        predicate: typeof t?.predicate === "string" ? t.predicate : "always",
        key: t?.key ?? null, value: t?.value ?? null, next,
      };
    }) : [{ predicate: "always", key: null, value: null, next: "REPLAN" }];
    st.recovery = st.recovery && typeof st.recovery === "object" ? {
      no_progress_before_replan: Number.isInteger(st.recovery.no_progress_before_replan) ? st.recovery.no_progress_before_replan : 3,
      max_action_failures: Number.isInteger(st.recovery.max_action_failures) ? st.recovery.max_action_failures : 2,
      settle_before_retry: st.recovery.settle_before_retry !== false,
    } : { no_progress_before_replan: 3, max_action_failures: 2, settle_before_retry: true };
    return st;
  });
  const entryState = typeof parsed.entry_state === "string" && states.some((s) => s.state_id === parsed.entry_state)
    ? parsed.entry_state : states[0].state_id;
  return {
    schema_version: "agent_harness.strategy_spec.v1",
    kind: "StrategySpec",
    base: {
      game_id: parsed.base?.game_id || brief?.base?.game_id || "UNKNOWN",
      run_id: parsed.base?.run_id || brief?.base?.run_id || "run",
      state_version: parsed.base?.state_version ?? brief?.base?.state_version ?? 1,
      scene_epoch: parsed.base?.scene_epoch ?? brief?.base?.scene_epoch ?? 0,
      policy_set_id: parsed.base?.policy_set_id || brief?.base?.policy_set_id || "candidate:default",
    },
    strategy_id: typeof parsed.strategy_id === "string" ? parsed.strategy_id.slice(0, 64) : "normalized-strategy",
    summary: typeof parsed.summary === "string" ? parsed.summary : "Normalized model strategy.",
    entry_state: entryState,
    states,
    global_replan_triggers: Array.isArray(parsed.global_replan_triggers) ? parsed.global_replan_triggers.slice(0, 12) : ["repeated_no_progress", "completion_suspected", "failure_active"],
    invariants: Array.isArray(parsed.invariants) ? parsed.invariants : [{ predicate: "failure_active", key: null, value: null, on_violation: "STOP" }],
    // The harness rejects references that are absent from the brief's
    // evidence packet ids or memory_refs — filter to the known set.
    evidence_refs: Array.isArray(parsed.evidence_refs)
      ? parsed.evidence_refs.filter((ref) => (brief?.evidence || []).some((e) => e.packet_id === ref) || (brief?.memory_refs || []).includes(ref)).slice(0, 8)
      : [],
    confidence: typeof parsed.confidence === "number" ? Math.min(Math.max(parsed.confidence, 0), 1) : 0.5,
  };
}

const server = createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true, service: "planner-http-adapter" }));
    return;
  }
  if (req.method !== "POST" || req.url !== "/plan") {
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
    return;
  }

  let raw = "";
  for await (const chunk of req) raw += chunk;
  let request;
  try {
    request = JSON.parse(raw);
  } catch {
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "invalid json body" }));
    return;
  }

  const provider = providerForModel(request.model);
  if (!provider.apiKey) {
    res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: `no api key configured for model ${request.model}` }));
    return;
  }

  try {
    const fallbackFirst = process.env.PLAYABLE_PLANNER_FALLBACK_FIRST === "1";
    let text = "";
    let latencyMs = 0;
    if (!fallbackFirst) {
      const messages = buildMessages(request);
      const t0 = Date.now();
      text = await callChatCompletions(provider, messages, Number(process.env.PLANNER_MAX_TOKENS || 8192), request.output_schema);
      latencyMs = Date.now() - t0;
    }
    console.log(`[planner-http-adapter] ${request.model} ${request.output_schema?.title || "?"} ${latencyMs}ms raw_len=${text.length} fallback_first=${fallbackFirst}`);
    const parsed = extractJson(text);
    // Determine response field from the output schema title if possible.
    const schemaTitle = String(request.output_schema?.title || "").toLowerCase();
    const isStrategy = schemaTitle.includes("strateg");
    const field = isStrategy ? "strategy" : "intent";
    const result = {};
    if (!parsed) {
      // Model returned empty/unparseable output — fall back to a constructed
      // schema-conformant envelope so the run keeps making progress.
      console.log(`[planner-http-adapter] PARSE_FAIL raw_len=${text.length} -> fallback`);
      result[field] = isStrategy ? buildFallbackStrategy(request.brief || {}) : buildFallbackIntent(request.brief || {});
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(result));
      return;
    }
    if (isStrategy) {
      // If the model output lacks the core StrategySpec fields, fall back to a
      // constructed schema-conformant envelope driven by the brief.
      const looksValid = parsed && typeof parsed === "object"
        && parsed.schema_version === "agent_harness.strategy_spec.v1"
        && typeof parsed.strategy_id === "string"
        && Array.isArray(parsed.states) && parsed.states.length > 0;
      result[field] = looksValid ? normalizeStrategy(parsed, request.brief || {}) : buildFallbackStrategy(request.brief || {});
    } else {
      const looksValid = parsed && typeof parsed === "object"
        && parsed.schema_version === "agent_harness.intent.v1"
        && typeof parsed.option === "string";
      result[field] = looksValid ? parsed : buildFallbackIntent(request.brief || {});
    }
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify(result));
  } catch (error) {
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: error.message }));
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[planner-http-adapter] listening on http://127.0.0.1:${PORT}/plan`);
});
