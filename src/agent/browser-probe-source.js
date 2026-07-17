export const browserProbeSource = String.raw `
(function installPlayableAgentProbe() {
  if (window.__playableAgentProbe && window.__playableAgentProbe.__version) return;
  var MAX_NODES = 500;
  var MAX_ITEMS = 80;
  var TEXT_LIMIT = 120;
  var MAX_COMPONENTS = 160;
  var MAX_FIELDS_PER_COMPONENT = 80;
  var PLAYER_RE = /player|actor|hero|character|char|car|truck|vehicle|mainactor|主角/i;
  var MANAGER_RE = /game|main|manager|mgr|director|level|guide|controller/i;
  var UI_RE = /win|victory|success|finish|complete|endcard|cta|install|download|lose|fail|retry|gamewin|ui_win/i;
  var INTERESTING_RE = /player|actor|hero|enemy|target|wood|fish|log|logs|smalllog|sell|shop|table|spot|saw|bag|collect|drop|upgrade|money|coin|home|base|goal|button|btn|start|next|skip|guide|hand|win|lose|endcard|cta|install|download/i;
  var DEFAULT_NODE_INTEREST_RE = /log|logs|smalllog|wood|sell|table|spot|target|guide|saw|bag|collect|drop|upgrade|money|coin/i;
  var FLAG_KEYS = /^(isGameOver|isWin|isFinish|gameWin|hasWin|isComplete|levelComplete|isLose|gameOver|win|finish|complete|lose|fail|failed|success|victory)$/i;
  var NUM_KEYS = /(score|coin|money|wood|fish|log|level|progress|count|num|hp|health|wave|time|capacity)/i;
  var DEFAULT_COMPONENT_RE = /Actor|Bag|Laser|Guide|Target|smallLog|massiveLog|Log|Wood|Sell|Spot|Button|Saw|Manager|Game|Controller|Collider|Trigger|Rigid/i;
  var DEFAULT_METHOD_RE = /trigger|enter|stay|exit|collision|contact|touch|click|tap|button|collect|pickup|pick|bag|log|wood|cut|saw|laser|sell|add|remove|fall|guide|target|complete|finish|win|getOff|offButton|buttonUp|laserButtonUp|getOffLaserButton|release|unlock|limit|resume|enableMove|disableMove|setMove|next|step|onCollisionEnter|onCollisionExit|DiTie|update|lateUpdate/i;
  var DEFAULT_FIELD_TRACE_COMPONENT_RE = /Actor|GuideManager|GuideController|DiTie|Spot|Button|Laser|MassiveLog|Sell|Game|Manager/i;
  var DEFAULT_FIELD_TRACE_FIELD_RE = /isLimitMove|isOnButton|isGetOnButton|isFinish|tempPrice|guide|step|index|state|lock|move|button|laser|finish|complete/i;
  var GUIDE_COMPONENT_RE = /GuideManager|GuideController|GuideTarget|Guide/i;
  var GUIDE_FIELD_RE = /current|cur|step|index|guide|target|node|targetNode|guideNode|hand|arrow|isFinish|isComplete|state/i;
  var DEFAULT_SOURCE_COMPONENT_RE = /Actor|GuideManager|GuideController|Guide|Laser|MassiveLog|MassiveLogController|Log|Wood|Bag|Sell|DiTie|Spot|Button|Game|Manager|Controller/i;
  var DEFAULT_SOURCE_METHOD_RE = /findMaxWeightGuide|clearAllGuide|updateGuide|guide|target|next|complete|finish|laser|button|down|up|getOn|getOff|cut|saw|fall|drop|spawn|create|collect|pickup|bag|wood|log|sell|update|lateUpdate|onTrigger|onCollision/i;
  var SOURCE_KEYWORDS = ["fallCount", "smallLogCount", "guidSmallLogs", "laser1", "guidWoodSpot", "massiveLog", "isLimitMove", "isOnButton", "isGetOnButton", "isLeavingButton", "guide", "target", "weight", "active", "finish", "complete", "bag", "wood", "log", "collect", "pickup", "sell", "onTriggerEnter", "onTriggerStay", "onTriggerExit"];
  var TAG_RE = {
    collider: /collider|trigger|rigid|physics|contact|collision/i,
    button: /button|btn|click|tap|touch/i,
    guide: /guide|tutorial|hand|target/i,
    laser: /laser|saw|cut/i,
    bag: /bag|capacity|carry/i,
    log: /log|wood|tree|smalllog|massivelog/i,
    sell: /sell|money|coin|shop|spot|table/i
  };
  var WIN_KEYS = /(isWin|isFinish|gameWin|hasWin|isComplete|levelComplete|win|finish|complete|success|victory)/i;
  var LOSE_KEYS = /(isLose|lose|fail|failed)/i;
  var GAMEOVER_KEYS = /(isGameOver|gameOver)/i;

  window.__playableAgentEvents = window.__playableAgentEvents || [];
  window.__playableAgentMethodTrace = window.__playableAgentMethodTrace || { active: false, calls: [], updateCounts: {}, wrappers: [] };
  window.__playableAgentFieldTrace = window.__playableAgentFieldTrace || { active: false, changes: [], failedFields: [], wrappers: [] };
  window.__playableAgentCallCounters = window.__playableAgentCallCounters || { calls: {}, wrapped: {} };
  var __playableAgentComputingScreen = false;
  var __playableAgentCameraCache = null;
  var __playableAgentFastCache = { scene: null, resolvedAt: 0, rawNodes: [], keyRaw: {}, comps: {}, actorComponent: null };
  var __playableAgentActorMoveCalibration = null;

  function safeString(value) {
    try {
      if (value == null) return "";
      return String(value).slice(0, TEXT_LIMIT);
    } catch (e) {
      return "";
    }
  }

  function className(component) {
    try {
      return safeString(component && (component.__classname__ || (component.constructor && component.constructor.name) || component.name || ""));
    } catch (e) {
      return "";
    }
  }

  function nodeName(node) {
    try { return safeString(node && node.name); } catch (e) { return ""; }
  }

  function nodeActive(node) {
    try {
      if (!node) return false;
      if (typeof node.activeInHierarchy === "boolean") return node.activeInHierarchy;
      if (typeof node.active === "boolean") return node.active;
      return true;
    } catch (e) {
      return false;
    }
  }

  function vec3(value) {
    try {
      if (!value) return undefined;
      var x = Number(value.x), y = Number(value.y), z = Number(value.z || 0);
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) return undefined;
      return { x: x, y: y, z: z };
    } catch (e) {
      return undefined;
    }
  }

  function worldPosition(node) {
    try {
      if (!node) return undefined;
      if (node.worldPosition) return vec3(node.worldPosition);
      if (typeof node.getWorldPosition === "function") return vec3(node.getWorldPosition());
      if (node.position) return vec3(node.position);
    } catch (e) {}
    return undefined;
  }

  function findActiveCamera() {
    try {
      if (__playableAgentCameraCache) return __playableAgentCameraCache;
      var scene = getScene();
      if (!scene) return null;
      var nodes = traverse(scene);
      for (var i = 0; i < nodes.length; i++) {
        var comps;
        try { comps = nodes[i].node && (nodes[i].node._components || nodes[i].node.components) || []; } catch (e) { comps = []; }
        for (var j = 0; j < comps.length; j++) {
          var cls = className(comps[j]);
          if (/camera/i.test(cls) && comps[j] && comps[j].enabled !== false) {
            __playableAgentCameraCache = comps[j];
            return comps[j];
          }
        }
      }
    } catch (e) {}
    return null;
  }

  function screenPosition(node) {
    try {
      if (__playableAgentComputingScreen) return undefined;
      var wp = worldPosition(node);
      if (!wp) return undefined;
      __playableAgentComputingScreen = true;
      var camera = findActiveCamera();
      __playableAgentComputingScreen = false;
      if (camera && typeof camera.worldToScreen === "function") {
        var out = camera.worldToScreen(wp);
        var x = Number(out && out.x), y = Number(out && out.y);
        if (isFinite(x) && isFinite(y)) return { x: x, y: y };
      }
      if (camera && camera.camera && typeof camera.camera.worldToScreen === "function") {
        var out2 = camera.camera.worldToScreen(wp);
        var x2 = Number(out2 && out2.x), y2 = Number(out2 && out2.y);
        if (isFinite(x2) && isFinite(y2)) return { x: x2, y: y2 };
      }
    } catch (e) {
      __playableAgentComputingScreen = false;
    }
    return undefined;
  }

  function components(node) {
    try {
      var list = node && (node._components || node.components);
      if (!Array.isArray(list)) return [];
      return list.slice(0, 20).map(className).filter(Boolean);
    } catch (e) {
      return [];
    }
  }

  function children(node) {
    try {
      return Array.isArray(node && node.children) ? node.children : [];
    } catch (e) {
      return [];
    }
  }

  function compactNode(node, path) {
    return {
      name: nodeName(node),
      path: safeString(path),
      active: nodeActive(node),
      worldPosition: worldPosition(node),
      screenPosition: screenPosition(node),
      components: components(node)
    };
  }

  function regexFromString(value, fallback) {
    try {
      if (!value) return fallback;
      var text = String(value);
      var match = text.match(/^\/(.+)\/([gimsuy]*)$/);
      if (match) return new RegExp(match[1], match[2]);
      return new RegExp(text, "i");
    } catch (e) {
      try {
        return new RegExp(String(value).replace(/[.*+?^\${}()|[\]\\]/g, "\\$&"), "i");
      } catch (e2) {
        return fallback;
      }
    }
  }

  function getScene() {
    try {
      var cc = window.cc;
      if (!cc || !cc.director || typeof cc.director.getScene !== "function") return null;
      return cc.director.getScene();
    } catch (e) {
      return null;
    }
  }

  function traverse(scene) {
    var out = [];
    var queue = [{ node: scene, path: "/" + nodeName(scene) }];
    var visited = [];
    while (queue.length && out.length < MAX_NODES) {
      var item = queue.shift();
      var node = item.node;
      if (!node || visited.indexOf(node) !== -1) continue;
      visited.push(node);
      var compact = compactNode(node, item.path);
      out.push({ node: node, compact: compact });
      var kids = children(node);
      for (var i = 0; i < kids.length && queue.length < MAX_NODES; i++) {
        queue.push({ node: kids[i], path: item.path + "/" + nodeName(kids[i]) });
      }
    }
    return out;
  }

  function traverseRaw(scene) {
    var out = [];
    var queue = [{ node: scene, path: "/" + nodeName(scene) }];
    var visited = [];
    while (queue.length && out.length < MAX_NODES) {
      var item = queue.shift();
      var node = item.node;
      if (!node || visited.indexOf(node) !== -1) continue;
      visited.push(node);
      out.push(item);
      var kids = children(node);
      for (var i = 0; i < kids.length && queue.length < MAX_NODES; i++) {
        queue.push({ node: kids[i], path: item.path + "/" + nodeName(kids[i]) });
      }
    }
    return out;
  }

  function fastFindRawByPathOrName(scene, nameOrPath) {
    try {
      var wanted = safeString(nameOrPath);
      var raw = traverseRaw(scene);
      var best = null;
      for (var i = 0; i < raw.length; i++) {
        if (raw[i].path === wanted || raw[i].path.slice(-wanted.length) === wanted) return raw[i];
        if (nodeName(raw[i].node) === wanted) best = best || raw[i];
      }
      if (best) return best;
      var lower = wanted.toLowerCase();
      for (var j = 0; j < raw.length; j++) {
        if (nodeName(raw[j].node).toLowerCase().indexOf(lower) !== -1 || raw[j].path.toLowerCase().indexOf(lower) !== -1) return raw[j];
      }
    } catch (e) {}
    return null;
  }

  function compactRaw(item, includeScreen) {
    if (!item) return null;
    var summary = {
      name: nodeName(item.node),
      path: safeString(item.path),
      active: nodeActive(item.node),
      worldPosition: worldPosition(item.node),
      components: components(item.node)
    };
    if (includeScreen) summary.screenPosition = screenPosition(item.node);
    return summary;
  }

  function pushComp(cache, cls, comp, item) {
    try {
      if (!cache.comps[cls]) cache.comps[cls] = [];
      cache.comps[cls].push({ component: comp, item: item, className: cls });
    } catch (e) {}
  }

  function resolveFastCache(force) {
    var started = Date.now();
    var scene = getScene();
    if (!scene) return { cache: null, resolveCacheMs: Date.now() - started };
    if (!force && __playableAgentFastCache.scene === scene && Date.now() - __playableAgentFastCache.resolvedAt < 2000) {
      return { cache: __playableAgentFastCache, resolveCacheMs: Date.now() - started };
    }
    var cache = { scene: scene, resolvedAt: Date.now(), rawNodes: traverseRaw(scene), keyRaw: {}, comps: {}, actorComponent: null };
    if (!__playableAgentFastCache.scene || __playableAgentFastCache.scene !== scene) {
      // Scene switch (e.g. Loading -> main): end-state UI baseline must be
      // re-recorded so a persistent CTA in the new scene is baselined.
      __endStateBaselinePaths = null;
    }
    var keyNames = [
      "Actor", "laserButtonModel", "upgradeLaserDiTie", "laser1", "guidSmallLogs", "guidWoodSpot",
      "collectArea", "collectCollider", "massiveLog", "Machine", "MachineInputSpot", "getWoodSpot",
      "inputPoint", "outputPoint", "sellSpotRoot", "woodOnTable", "woodBagOnTable",
      "guidRecruit", "recruitWorkerDiTie", "workerNode", "moneySpotRoot", "moneySpot",
      "laser2", "guidConveyor1", "guidConveyor2", "upgradeLaserDiTie2", "conveyor1DiTie", "conveyor2DiTie",
      "upgradeKnifeDiTie", "guidKnife", "knife"
    ];
    function maybeKey(item) {
      for (var k = 0; k < keyNames.length; k++) {
        var key = keyNames[k];
        if (cache.keyRaw[key]) continue;
        var n = nodeName(item.node);
        if (n === key || item.path.slice(-key.length) === key || item.path.toLowerCase().indexOf(key.toLowerCase()) !== -1) {
          cache.keyRaw[key] = item;
        }
      }
      if (item.path === "/game/GameScene/Actor") cache.keyRaw.Actor = item;
    }
    for (var i = 0; i < cache.rawNodes.length; i++) {
      var item = cache.rawNodes[i];
      maybeKey(item);
      var compsRaw = componentList(item.node);
      for (var c = 0; c < compsRaw.length; c++) {
        var cls = className(compsRaw[c]);
        if (!cls) continue;
        pushComp(cache, cls, compsRaw[c], item);
        if (!cache.actorComponent && cls === "Actor") cache.actorComponent = { component: compsRaw[c], item: item, className: cls };
      }
    }
    __playableAgentFastCache = cache;
    return { cache: cache, resolveCacheMs: Date.now() - started };
  }

  function firstComp(cache, regex) {
    try {
      var re = typeof regex === "string" ? new RegExp(regex, "i") : regex;
      var keys = Object.keys(cache.comps || {});
      for (var i = 0; i < keys.length; i++) {
        if (!re.test(keys[i])) continue;
        var list = cache.comps[keys[i]];
        if (list && list.length) return list[0];
      }
    } catch (e) {}
    return null;
  }

  function compEntries(cache, regex, limit) {
    var out = [];
    try {
      var re = typeof regex === "string" ? new RegExp(regex, "i") : regex;
      var keys = Object.keys(cache.comps || {});
      for (var i = 0; i < keys.length && out.length < (limit || 40); i++) {
        var list = cache.comps[keys[i]] || [];
        for (var j = 0; j < list.length && out.length < (limit || 40); j++) {
          var text = keys[i] + " " + list[j].item.path + " " + nodeName(list[j].item.node);
          if (!re || re.test(text)) out.push(list[j]);
        }
      }
    } catch (e) {}
    return out;
  }

  function compOnRaw(raw, regex) {
    try {
      var re = regex ? (typeof regex === "string" ? new RegExp(regex, "i") : regex) : null;
      var compsRaw = componentList(raw && raw.node);
      for (var i = 0; i < compsRaw.length; i++) {
        var cls = className(compsRaw[i]);
        if (!re || re.test(cls)) return { component: compsRaw[i], className: cls, item: raw };
      }
    } catch (e) {}
    return null;
  }

  function primitiveFieldsOf(obj, pattern) {
    var out = {};
    try {
      var re = pattern ? regexFromString(pattern, null) : null;
      var keys = ownKeys(obj).slice(0, 120);
      for (var i = 0; i < keys.length; i++) {
        var key = keys[i];
        if (re && !re.test(key)) continue;
        var value = obj[key];
        if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
          out[key] = typeof value === "string" ? safeString(value) : value;
        }
      }
    } catch (e) {}
    return out;
  }

  function refToNodeSummary(value) {
    try {
      if (!value) return null;
      if (looksLikeNode(value)) return summarizeNodeRef(value);
      if (looksLikeComponent(value)) return summarizeComponentRef(value);
      if (value.node) return summarizeNodeRef(value.node);
    } catch (e) {}
    return null;
  }

  function summarizeArrayNodeRefs(value, limit) {
    var out = [];
    try {
      if (!Array.isArray(value)) return out;
      for (var i = 0; i < value.length && out.length < (limit || 12); i++) {
        var ref = refToNodeSummary(value[i]);
        if (!ref && value[i] && value[i].node) ref = refToNodeSummary(value[i].node);
        if (!ref && looksLikeComponent(value[i])) ref = summarizeComponentRef(value[i]);
        if (ref) out.push(ref);
      }
    } catch (e) {}
    return out;
  }

  function summarizeArrayItems(value, limit) {
    var out = [];
    try {
      if (!Array.isArray(value)) return out;
      for (var i = 0; i < value.length && out.length < (limit || 6); i++) {
        out.push(summarizeAny(value[i]));
      }
    } catch (e) {}
    return out;
  }

  function bagCount(value) {
    try {
      if (!value) return undefined;
      if (typeof value.getBagItemCount === "function") return Number(value.getBagItemCount());
      if (Array.isArray(value.items)) return value.items.length;
      if (Array.isArray(value.children)) return value.children.length;
      if (Array.isArray(value._items)) return value._items.length;
      if (typeof value.length === "number") return value.length;
      if (typeof value.count === "number") return value.count;
      if (typeof value._count === "number") return value._count;
    } catch (e) {}
    return undefined;
  }

  function distanceToActor(actor, node) {
    try {
      var a = actor && actor.worldPosition;
      var b = node && node.worldPosition;
      if (!a || !b) return undefined;
      return Math.sqrt(Math.pow(b.x - a.x, 2) + Math.pow(b.z - a.z, 2));
    } catch (e) {}
    return undefined;
  }

  function distanceXZBetween(a, b) {
    try {
      if (!a || !b) return undefined;
      return Math.sqrt(Math.pow(Number(b.x) - Number(a.x), 2) + Math.pow(Number(b.z) - Number(a.z), 2));
    } catch (e) {}
    return undefined;
  }

  function rotationOf(node) {
    try {
      if (!node) return undefined;
      if (node.eulerAngles) return vec3(node.eulerAngles);
      if (node.rotation) return summarizeAny(node.rotation);
      if (typeof node.angle === "number") return { z: node.angle };
    } catch (e) {}
    return undefined;
  }

  function scaleOf(node) {
    try {
      if (!node) return undefined;
      if (node.worldScale) return vec3(node.worldScale);
      if (node.scale) return vec3(node.scale);
    } catch (e) {}
    return undefined;
  }

  function colorOf(node) {
    try {
      var compsRaw = componentList(node);
      for (var i = 0; i < compsRaw.length; i++) {
        var c = compsRaw[i];
        var color = c && (c.color || c._color);
        if (color) {
          return {
            r: Number(color.r),
            g: Number(color.g),
            b: Number(color.b),
            a: color.a == null ? undefined : Number(color.a)
          };
        }
        var mat = c && (c.material || c._material);
        if (mat && mat.color) return summarizeAny(mat.color);
      }
    } catch (e) {}
    return undefined;
  }

  function isBlueNodeSummary(summary) {
    try {
      var text = (summary.name || "") + " " + (summary.path || "") + " " + ((summary.components || []).join(" "));
      if (/blue|guid|guide|arrow|line|indicator/i.test(text)) return true;
      var c = summary.color;
      if (c && typeof c.b === "number") return c.b > 120 && c.b > (Number(c.r) + 35) && c.b > (Number(c.g) + 20);
    } catch (e) {}
    return false;
  }

  function summarizeCallValue(value) {
    try {
      var summary = summarizeAny(value);
      if (summary && typeof summary === "object") return summary;
      return summary;
    } catch (e) {
      return safeString(value);
    }
  }

  function safeBagRealCount(value) {
    try {
      if (!value) return undefined;
      if (typeof value.getBagRealItemCount === "function") return Number(value.getBagRealItemCount());
      return undefined;
    } catch (e) {
      return undefined;
    }
  }

  function tableBagRefs(cache, managerComp) {
    var woodOnTableRaw = cache && (cache.keyRaw.woodOnTable || fastFindRawByPathOrName(cache.scene, "/game/env/woodOnTable") || fastFindRawByPathOrName(cache.scene, "woodOnTable"));
    var woodBagRaw = cache && (cache.keyRaw.woodBagOnTable || fastFindRawByPathOrName(cache.scene, "woodBagOnTable"));
    try {
      var managers = managerComp ? [{ component: managerComp }] : compEntries(cache, /CustomerManager|Customer/i, 30);
      managers.some(function(entry) {
        var comp = entry.component;
        var keys = ownKeys(comp).filter(function(key) { return /woodBagOnTable|woodOnTable|woodBag|table/i.test(key); });
        for (var i = 0; i < keys.length; i++) {
          var ref = refToNodeSummary(comp[keys[i]]);
          var refPath = ref && (ref.nodePath || ref.path);
          if (!refPath) continue;
          var raw = fastFindRawByPathOrName(cache.scene, refPath);
          if (!raw) continue;
          if (/woodOnTable/i.test(refPath) || /woodOnTable/i.test(keys[i])) woodOnTableRaw = raw;
          if (/woodBagOnTable|woodBag|table/i.test(refPath + " " + keys[i])) woodBagRaw = raw;
          if (!woodOnTableRaw && /wood/i.test(refPath)) woodOnTableRaw = raw;
          if (!woodBagRaw && /bag|table/i.test(refPath)) woodBagRaw = raw;
        }
        return Boolean(woodOnTableRaw && woodBagRaw);
      });
    } catch (e) {}
    if (!woodBagRaw && woodOnTableRaw) woodBagRaw = woodOnTableRaw;
    var woodOnEntry = woodOnTableRaw ? compOnRaw(woodOnTableRaw, /WoodOnTable|WoodBag|Bag|Spot|Table|Wood/i) : null;
    var woodBagEntry = woodBagRaw ? compOnRaw(woodBagRaw, /WoodBag|Bag|WoodOnTable|Table|Wood/i) : null;
    return {
      woodOnTableRaw: woodOnTableRaw,
      woodBagRaw: woodBagRaw,
      woodOnTableComp: woodOnEntry && woodOnEntry.component,
      woodBagComp: woodBagEntry && woodBagEntry.component
    };
  }

  function checkoutStateSnapshot(managerComp) {
    var out = {};
    try {
      var resolved = resolveFastCache(false);
      var cache = resolved.cache;
      if (!cache) return out;
      var refs = tableBagRefs(cache, managerComp);
      out.woodOnTableCount = bagCount(refs.woodOnTableComp);
      out.woodBagOnTableCount = bagCount(refs.woodBagComp);
      out.woodOnTableRealCount = safeBagRealCount(refs.woodOnTableComp);
      out.woodBagOnTableRealCount = safeBagRealCount(refs.woodBagComp);
      out.activeCustomerCount = compEntries(cache, /Customer|Buyer|NPC/i, 80).filter(function(entry) {
        return !/CustomerManager/i.test(entry.className) && nodeActive(entry.item.node);
      }).length;
      var manager = managerComp || (compEntries(cache, /CustomerManager/i, 5)[0] || {}).component;
      if (manager) {
        out.canSell = manager.canSell;
        try {
          var worker = manager.workerNode || (window.g && window.g.sceneManager && window.g.sceneManager.workerNode);
          if (worker) out.workerNodeActive = nodeActive(worker.node || worker);
        } catch (e) {}
      }
      compEntries(cache, /Money|Coin|Score|Game|Manager|MainGame|MoneNum/i, 100).forEach(function(entry) {
        var fields = primitiveFieldsOf(entry.component, /money|coin|playCoin|score/i);
        Object.keys(fields).forEach(function(key) {
          if (typeof fields[key] === "number" && out[key] == null) out[key] = fields[key];
        });
      });
    } catch (e) {
      out.error = safeString(e && e.message ? e.message : e);
    }
    return out;
  }

  function getCallCounters() {
    try {
      var calls = (window.__playableAgentCallCounters && window.__playableAgentCallCounters.calls) || {};
      var out = {};
      Object.keys(calls).forEach(function(key) {
        var item = calls[key] || {};
        out[key] = {
          callCount: item.callCount || 0,
          lastCalledAt: item.lastCalledAt || 0,
          lastArgs: item.lastArgs,
          lastReturn: item.lastReturn,
          lastError: item.lastError,
          lastBefore: item.lastBefore,
          lastAfter: item.lastAfter,
          possibleFailure: item.possibleFailure
        };
      });
      return out;
    } catch (e) {
      return {};
    }
  }

  function findNodeEntryByPath(pathText) {
    var scene = getScene();
    if (!scene) return null;
    var wanted = safeString(pathText);
    if (!wanted) return null;
    var normalized = wanted.charAt(0) === "/" ? wanted : "/" + wanted;
    var nodes = traverse(scene);
    var suffixMatches = [];
    for (var i = 0; i < nodes.length; i++) {
      var nodePath = nodes[i].compact.path;
      if (nodePath === wanted || nodePath === normalized) return nodes[i];
      if (nodePath.slice(-wanted.length) === wanted || nodePath.slice(-normalized.length) === normalized) suffixMatches.push(nodes[i]);
    }
    return suffixMatches.length ? suffixMatches[0] : null;
  }

  function findNodeSummariesByNameImpl(nameOrRegex) {
    var scene = getScene();
    if (!scene) return [];
    var needle = safeString(nameOrRegex);
    if (!needle) return [];
    var regex = regexFromString(needle, null);
    var lower = needle.toLowerCase();
    var nodes = traverse(scene);
    var out = [];
    for (var i = 0; i < nodes.length && out.length < MAX_ITEMS; i++) {
      var name = nodes[i].compact.name;
      var haystack = name + " " + nodes[i].compact.path;
      var exact = name === needle;
      var contains = name.toLowerCase().indexOf(lower) !== -1 || nodes[i].compact.path.toLowerCase().indexOf(lower) !== -1;
      var regexMatch = false;
      try { regexMatch = regex ? regex.test(haystack) : false; } catch (e) {}
      if (exact || contains || regexMatch) out.push(nodes[i].compact);
    }
    return out;
  }

  function findInterestingNodeSummariesImpl(pattern) {
    var scene = getScene();
    if (!scene) return [];
    var regex = regexFromString(pattern, DEFAULT_NODE_INTEREST_RE);
    var nodes = traverse(scene);
    var out = [];
    for (var i = 0; i < nodes.length && out.length < MAX_ITEMS; i++) {
      var compact = nodes[i].compact;
      var keyText = compact.name + " " + compact.path + " " + compact.components.join(" ");
      try {
        if (regex.test(keyText)) out.push(compact);
      } catch (e) {}
    }
    return out;
  }

  function componentList(node) {
    try {
      var list = node && (node._components || node.components);
      return Array.isArray(list) ? list : [];
    } catch (e) {
      return [];
    }
  }

  function componentEnabled(component) {
    try {
      if (!component) return undefined;
      if (typeof component.enabled === "boolean") return component.enabled;
      if (typeof component._enabled === "boolean") return component._enabled;
    } catch (e) {}
    return undefined;
  }

  function looksLikeNode(value) {
    try {
      return Boolean(value && typeof value === "object" && (Array.isArray(value.children) || value._components || value.worldPosition || typeof value.getWorldPosition === "function") && typeof value.name !== "undefined");
    } catch (e) {
      return false;
    }
  }

  function looksLikeComponent(value) {
    try {
      return Boolean(value && typeof value === "object" && !looksLikeNode(value) && (value.node || value.__classname__ || (value.constructor && value.constructor.name)));
    } catch (e) {
      return false;
    }
  }

  function findPathForNode(node) {
    try {
      var scene = getScene();
      if (!scene || !node) return "";
      var nodes = traverse(scene);
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i].node === node) return nodes[i].compact.path;
      }
    } catch (e) {}
    return "";
  }

  function summarizeNodeRef(node) {
    try {
      return {
        kind: "node",
        name: nodeName(node),
        nodeName: nodeName(node),
        path: findPathForNode(node),
        nodePath: findPathForNode(node),
        worldPosition: worldPosition(node),
        screenPosition: screenPosition(node),
        active: nodeActive(node)
      };
    } catch (e) {
      return { kind: "node", name: "" };
    }
  }

  function summarizeComponentRef(component) {
    try {
      var node = component && component.node;
      return {
        kind: "component",
        className: className(component),
        nodeName: nodeName(node),
        nodePath: findPathForNode(node),
        worldPosition: worldPosition(node),
        screenPosition: screenPosition(node),
        active: nodeActive(node)
      };
    } catch (e) {
      return { kind: "component", className: "" };
    }
  }

  function summarizeAny(value) {
    try {
      if (value == null) return null;
      var type = typeof value;
      if (type === "string") return safeString(value);
      if (type === "number" || type === "boolean") return value;
      if (looksLikeNode(value)) return summarizeNodeRef(value);
      if (looksLikeComponent(value)) return summarizeComponentRef(value);
      if (Array.isArray(value)) return { kind: "array", length: value.length };
      if (type === "object") return { kind: "object", className: className(value) || safeString(Object.prototype.toString.call(value)) };
    } catch (e) {}
    return safeString(value);
  }

  function classifyTags(text) {
    var tags = [];
    Object.keys(TAG_RE).forEach(function(key) {
      try { if (TAG_RE[key].test(text)) tags.push(key); } catch (e) {}
    });
    return tags;
  }

  function methodNames(component) {
    var names = [];
    var seen = {};
    function add(key) {
      if (!key || seen[key] || key === "constructor") return;
      seen[key] = true;
      names.push(safeString(key));
    }
    try {
      Object.keys(component || {}).forEach(function(key) {
        try { if (typeof component[key] === "function") add(key); } catch (e) {}
      });
    } catch (e) {}
    try {
      var proto = component && Object.getPrototypeOf(component);
      Object.getOwnPropertyNames(proto || {}).forEach(function(key) {
        try { if (typeof component[key] === "function") add(key); } catch (e) {}
      });
    } catch (e) {}
    return names.slice(0, 80);
  }

  function componentSummary(component, nodeEntry, options) {
    var primitiveFields = {};
    var numericFields = {};
    var booleanFields = {};
    var stringFields = {};
    var objectRefs = {};
    var arrayFields = {};
    var cls = className(component);
    var guideLike = GUIDE_COMPONENT_RE.test(cls + " " + nodeEntry.compact.name + " " + nodeEntry.compact.path);
    var keys = ownKeys(component).slice(0, guideLike ? 120 : MAX_FIELDS_PER_COMPONENT);
    function collectField(key, value) {
      if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
        primitiveFields[key] = typeof value === "string" ? safeString(value) : value;
        if (typeof value === "number" && isFinite(value)) numericFields[key] = value;
        if (typeof value === "boolean") booleanFields[key] = value;
        if (typeof value === "string") stringFields[key] = safeString(value);
      } else if (Array.isArray(value)) {
        arrayFields[key] = { length: value.length, sample: value.slice(0, 5).map(summarizeAny) };
      } else if (looksLikeNode(value) || looksLikeComponent(value) || (value && typeof value === "object" && value.node)) {
        objectRefs[key] = summarizeAny(value);
      }
    }
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (/^(__|_objFlags|_name|_id)/.test(key)) continue;
      try {
        var value = component[key];
        if (typeof value === "function") continue;
        collectField(key, value);
      } catch (e) {}
    }
    if (guideLike) {
      var guideKeys = ownKeys(component);
      for (var g = 0; g < guideKeys.length; g++) {
        var guideKey = guideKeys[g];
        if (!GUIDE_FIELD_RE.test(guideKey)) continue;
        try {
          var guideValue = component[guideKey];
          if (typeof guideValue !== "function") collectField(guideKey, guideValue);
        } catch (e) {}
      }
    }
    var methods = methodNames(component);
    var tagText = cls + " " + nodeEntry.compact.name + " " + nodeEntry.compact.path + " " + methods.join(" ") + " " + Object.keys(primitiveFields).join(" ");
    return {
      className: cls,
      nodeName: nodeEntry.compact.name,
      nodePath: nodeEntry.compact.path,
      nodeActive: nodeEntry.compact.active,
      nodeWorldPosition: nodeEntry.compact.worldPosition,
      screenPosition: nodeEntry.compact.screenPosition,
      enabled: componentEnabled(component),
      primitiveFields: primitiveFields,
      numericFields: numericFields,
      booleanFields: booleanFields,
      stringFields: stringFields,
      methodNames: methods,
      objectRefs: objectRefs,
      arrayFields: arrayFields,
      tags: classifyTags(tagText)
    };
  }

  function getComponentSummariesImpl(pattern, options) {
    var scene = getScene();
    if (!scene) return [];
    var regex = regexFromString(pattern, DEFAULT_COMPONENT_RE);
    var nodes = traverse(scene);
    var out = [];
    for (var i = 0; i < nodes.length && out.length < MAX_COMPONENTS; i++) {
      var comps = componentList(nodes[i].node);
      for (var j = 0; j < comps.length && out.length < MAX_COMPONENTS; j++) {
        var cls = className(comps[j]);
        var text = cls + " " + nodes[i].compact.name + " " + nodes[i].compact.path;
        var matched = false;
        try { matched = regex ? regex.test(text) : true; } catch (e) {}
        if (!matched) continue;
        out.push(componentSummary(comps[j], nodes[i], options || {}));
      }
    }
    return out;
  }

  function getNodeDeepSummaryImpl(pathOrName) {
    var entry = findNodeEntryByPath(pathOrName);
    if (!entry) {
      var matches = findNodeSummariesByNameImpl(pathOrName);
      if (matches.length) entry = findNodeEntryByPath(matches[0].path);
    }
    if (!entry) return null;
    var comps = componentList(entry.node).map(function(component) { return componentSummary(component, entry, {}); }).slice(0, 40);
    var kids = children(entry.node).slice(0, 40).map(function(child) {
      return compactNode(child, entry.compact.path + "/" + nodeName(child));
    });
    var parentPath = "";
    var siblings = [];
    try {
      var parent = entry.node.parent;
      parentPath = findPathForNode(parent);
      siblings = children(parent).map(nodeName).slice(0, 80);
    } catch (e) {}
    var text = entry.compact.name + " " + entry.compact.path + " " + entry.compact.components.join(" ") + " " + comps.map(function(c) { return c.tags.join(" ") + " " + c.methodNames.join(" "); }).join(" ");
    return { node: entry.compact, components: comps, children: kids, parentPath: parentPath, siblingNames: siblings, tags: classifyTags(text) };
  }

  function getGuideSummaryImpl() {
    var summaries = getComponentSummariesImpl("GuideManager|GuideController|GuideTarget|Guide", {});
    var managers = [];
    var controllers = [];
    var candidateTargets = [];
    var likelyCurrentTarget = undefined;
    function pushTarget(ref, reason, owner) {
      try {
        if (!ref || !ref.kind) return;
        var nodePath = ref.nodePath || ref.path;
        if (!nodePath && ref.kind === "component") nodePath = ref.nodePath;
        if (!nodePath) return;
        var entry = findNodeEntryByPath(nodePath);
        var nodeSummary = entry ? entry.compact : {
          name: ref.name || ref.nodeName || "",
          path: nodePath,
          active: ref.active,
          worldPosition: ref.worldPosition,
          screenPosition: ref.screenPosition,
          components: []
        };
        candidateTargets.push({ reason: reason, owner: owner, node: nodeSummary, ref: ref });
        if (!likelyCurrentTarget && /cur|current|targetNode|target|guide/i.test(reason)) likelyCurrentTarget = nodeSummary;
      } catch (e) {}
    }
    for (var i = 0; i < summaries.length; i++) {
      var summary = summaries[i];
      if (/GuideManager/i.test(summary.className)) managers.push(summary);
      else controllers.push(summary);
      var refs = summary.objectRefs || {};
      Object.keys(refs).forEach(function(key) {
        if (GUIDE_FIELD_RE.test(key)) pushTarget(refs[key], key, summary.className + "@" + summary.nodePath);
      });
    }
    if (!likelyCurrentTarget && candidateTargets.length) likelyCurrentTarget = candidateTargets[0].node;
    return {
      managers: managers.slice(0, 20),
      controllers: controllers.slice(0, 60),
      likelyCurrentTarget: likelyCurrentTarget,
      candidateTargets: candidateTargets.slice(0, 80)
    };
  }

  function getMethodSourcesImpl(componentPattern, methodPattern, options) {
    var out = [];
    try {
      var compRegex = regexFromString(componentPattern, DEFAULT_SOURCE_COMPONENT_RE);
      var methodRegex = regexFromString(methodPattern, DEFAULT_SOURCE_METHOD_RE);
      var maxChars = Number(options && options.maxChars) || 20000;
      var includeUpdateFull = Boolean(options && options.includeUpdateFull);
      var nodes = traverse(getScene());
      function addSource(fn, owner, methodName, comp, entry) {
        try {
          if (!fn || typeof fn !== "function" || !methodRegex.test(methodName)) return;
          var source = "";
          try { source = Function.prototype.toString.call(fn); } catch (e) { source = ""; }
          var isUpdate = /^(update|lateUpdate)$/i.test(methodName);
          var sourceLength = source.length;
          var full = isUpdate && !includeUpdateFull ? source.slice(0, 1600) : source.slice(0, maxChars);
          var lower = source.toLowerCase();
          var keywords = [];
          for (var k = 0; k < SOURCE_KEYWORDS.length; k++) {
            if (lower.indexOf(SOURCE_KEYWORDS[k].toLowerCase()) !== -1) keywords.push(SOURCE_KEYWORDS[k]);
          }
          out.push({
            className: className(comp),
            nodeName: entry.compact.name,
            nodePath: entry.compact.path,
            methodName: safeString(methodName),
            owner: owner,
            source: full,
            sourceLength: sourceLength,
            sourcePreview: source.slice(0, 500),
            keywords: keywords
          });
        } catch (e) {}
      }
      for (var i = 0; i < nodes.length && out.length < 500; i++) {
        var compsRaw = componentList(nodes[i].node);
        for (var j = 0; j < compsRaw.length && out.length < 500; j++) {
          var comp = compsRaw[j];
          var text = className(comp) + " " + nodes[i].compact.name + " " + nodes[i].compact.path;
          if (!compRegex.test(text)) continue;
          var names = methodNames(comp);
          for (var n = 0; n < names.length && out.length < 500; n++) {
            var name = names[n];
            try {
              if (Object.prototype.hasOwnProperty.call(comp, name)) addSource(comp[name], "instance", name, comp, nodes[i]);
              var proto = Object.getPrototypeOf(comp);
              if (proto && typeof proto[name] === "function") addSource(proto[name], "prototype", name, comp, nodes[i]);
            } catch (e) {}
          }
        }
      }
    } catch (e) {}
    return out;
  }

  function searchMethodSourcesImpl(query, componentPattern) {
    var queries = Array.isArray(query) ? query.map(safeString).filter(Boolean) : [safeString(query)].filter(Boolean);
    var sources = getMethodSourcesImpl(componentPattern, DEFAULT_SOURCE_METHOD_RE, { maxChars: 60000, includeUpdateFull: true });
    var out = [];
    for (var i = 0; i < sources.length && out.length < 300; i++) {
      var src = sources[i].source || "";
      var lower = src.toLowerCase();
      var matched = [];
      var snippets = [];
      for (var q = 0; q < queries.length; q++) {
        var needle = queries[q].toLowerCase();
        var index = lower.indexOf(needle);
        if (index === -1) continue;
        matched.push(queries[q]);
        snippets.push({
          query: queries[q],
          index: index,
          text: src.slice(Math.max(0, index - 200), Math.min(src.length, index + queries[q].length + 200))
        });
      }
      if (matched.length) {
        out.push({
          className: sources[i].className,
          nodePath: sources[i].nodePath,
          methodName: sources[i].methodName,
          matchedQueries: matched,
          snippets: snippets.slice(0, 8)
        });
      }
    }
    return out;
  }

  function getComponentDependencyGraphImpl(pattern) {
    var summaries = getComponentSummariesImpl(pattern || "Actor|GuideManager|GuideController|Laser|MassiveLog|MassiveLogController|Bag|Sell", {});
    return summaries.map(function(summary) {
      var refs = [];
      Object.keys(summary.objectRefs || {}).forEach(function(key) {
        var ref = summary.objectRefs[key];
        refs.push({
          field: key,
          kind: ref && ref.kind,
          className: ref && ref.className,
          nodeName: ref && (ref.nodeName || ref.name),
          nodePath: ref && (ref.nodePath || ref.path),
          worldPosition: ref && ref.worldPosition,
          screenPosition: ref && ref.screenPosition,
          active: ref && ref.active
        });
      });
      var fields = {};
      var allFields = Object.assign({}, summary.primitiveFields || {}, summary.numericFields || {}, summary.booleanFields || {}, summary.stringFields || {});
      Object.keys(allFields).forEach(function(key) {
        var owner = summary.className + " " + summary.nodeName + " " + summary.nodePath;
        if (/Guide/i.test(owner) && GUIDE_FIELD_RE.test(key)) fields[key] = allFields[key];
        if (/MassiveLog|Log|Wood/i.test(owner) && /log|wood|fall|small|progress|count/i.test(key)) fields[key] = allFields[key];
        if (/Bag/i.test(owner) && /capacity|count|list|item|wood|log|bag/i.test(key)) fields[key] = allFields[key];
        if (/Laser/i.test(owner) && /active|work|progress|button|target|laser|cut|saw|finish/i.test(key)) fields[key] = allFields[key];
      });
      return {
        id: summary.className + "@" + summary.nodePath,
        className: summary.className,
        nodeName: summary.nodeName,
        nodePath: summary.nodePath,
        nodeWorldPosition: summary.nodeWorldPosition,
        active: summary.nodeActive,
        enabled: summary.enabled,
        fields: fields,
        objectRefs: refs.slice(0, 60),
        arrayFields: summary.arrayFields,
        tags: summary.tags
      };
    });
  }

  function getHarvestChainSummaryImpl() {
    var timings = {};
    var t0 = Date.now();
    var resolved = resolveFastCache(false);
    timings.resolveCacheMs = resolved.resolveCacheMs;
    var cache = resolved.cache;
    if (!cache) return { ready: false, timings: timings };

    var actorEntry = cache.actorComponent;
    var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), false);
    var actorComp = actorEntry && actorEntry.component;
    var actorState = actorComp ? {
      isLimitMove: actorComp.isLimitMove,
      isOnButton: actorComp.isOnButton,
      isGetOnButton: actorComp.isGetOnButton,
      isLeavingButton: actorComp.isLeavingButton
    } : {};
    var collectAreaRef = actorComp ? (refToNodeSummary(actorComp.collectArea) || refToNodeSummary(actorComp._collectArea)) : null;
    var collectColliderRef = actorComp ? (refToNodeSummary(actorComp.collectCollider) || refToNodeSummary(actorComp._collectCollider)) : null;
    var bagLogSummary = actorComp ? {
      count: bagCount(actorComp.bagLog || actorComp.logBag || actorComp._bagLog),
      ref: summarizeAny(actorComp.bagLog || actorComp.logBag || actorComp._bagLog)
    } : {};
    timings.readActorMs = Date.now() - t0 - (timings.resolveCacheMs || 0);

    var harvestStarted = Date.now();
    var massiveLogControllers = [];
    var smallLogs = [];
    var controllerKeys = Object.keys(cache.comps || {}).filter(function(key) { return /MassiveLog|MassiveLogController|LogController/i.test(key); });
    for (var ci = 0; ci < controllerKeys.length; ci++) {
      var list = cache.comps[controllerKeys[ci]] || [];
      for (var li = 0; li < list.length && massiveLogControllers.length < 12; li++) {
        var comp = list[li].component;
        var fields = primitiveFieldsOf(comp, /log|wood|fall|small|progress|count|cut|saw|active|enabled|time|percent/i);
        var arrays = [];
        ["_smallLogs", "smallLogs", "logSegments", "_logs", "logs"].forEach(function(key) {
          try {
            var refs = summarizeArrayNodeRefs(comp[key], 16);
            if (refs.length) arrays.push({ field: key, length: comp[key].length, refs: refs });
          } catch (e) {}
        });
        var ctrlSummary = compactRaw(list[li].item, false);
        massiveLogControllers.push({
          className: list[li].className,
          nodePath: ctrlSummary && ctrlSummary.path,
          worldPosition: ctrlSummary && ctrlSummary.worldPosition,
          fields: fields,
          smallLogs: arrays,
          collectAreaRefs: Object.keys(comp || {}).filter(function(key) { return /collect|area/i.test(key); }).slice(0, 20).map(function(key) { return { field: key, ref: refToNodeSummary(comp[key]) || summarizeAny(comp[key]) }; }),
          colliderRefs: Object.keys(comp || {}).filter(function(key) { return /collider|trigger/i.test(key); }).slice(0, 20).map(function(key) { return { field: key, ref: refToNodeSummary(comp[key]) || summarizeAny(comp[key]) }; })
        });
        arrays.forEach(function(arrayInfo) {
          (arrayInfo.refs || []).forEach(function(ref, refIndex) {
            var fallbackPath = (ctrlSummary && ctrlSummary.path ? ctrlSummary.path : "/unknown") + "/" + arrayInfo.field + "[" + refIndex + "]";
            smallLogs.push({
              nodeName: ref.nodeName || ref.name,
              nodePath: ref.nodePath || ref.path || fallbackPath,
              active: ref.active,
              worldPosition: ref.worldPosition,
              screenPosition: ref.screenPosition,
              components: [],
              collider: /collider|trigger/i.test(JSON.stringify(ref)),
              distanceToActor: distanceToActor(actorSummary, { worldPosition: ref.worldPosition })
            });
          });
        });
      }
    }
    if (smallLogs.length < 8) {
      for (var rn = 0; rn < cache.rawNodes.length && smallLogs.length < 24; rn++) {
        var compact = compactRaw(cache.rawNodes[rn], false);
        var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
        if (/smallLog|smalllog|log|wood/i.test(text) && /collider|trigger|log|wood/i.test(text)) {
          smallLogs.push({
            nodeName: compact.name,
            nodePath: compact.path,
            active: compact.active,
            worldPosition: compact.worldPosition,
            components: compact.components,
            collider: /collider|trigger/i.test(text),
            distanceToActor: distanceToActor(actorSummary, compact)
          });
        }
      }
    }
    timings.readHarvestMs = Date.now() - harvestStarted;

    var sellStarted = Date.now();
    var woodBagEntry = firstComp(cache, /woodBagOnTable|Bag/i);
    var customerManager = firstComp(cache, /CustomerManager|Customer|Sell/i);
    var sellChain = {
      sellSpotRoot: compactRaw(cache.keyRaw.sellSpotRoot, false),
      woodOnTable: compactRaw(cache.keyRaw.woodOnTable, false),
      woodBagOnTable: compactRaw(cache.keyRaw.woodBagOnTable || (woodBagEntry && woodBagEntry.item), false),
      customerManager: customerManager ? { className: customerManager.className, nodePath: customerManager.item.path, fields: primitiveFieldsOf(customerManager.component, /sell|buy|money|coin|wood|bag|count|customer|active/i) } : null,
      candidateSellTargets: []
    };
    [sellChain.sellSpotRoot, sellChain.woodOnTable, sellChain.woodBagOnTable].forEach(function(node) {
      if (node && node.worldPosition) sellChain.candidateSellTargets.push({ name: node.name, path: node.path, worldPosition: node.worldPosition, reason: "sellChain", priority: 1, distanceToActor: distanceToActor(actorSummary, node) });
    });
    timings.readSellMs = Date.now() - sellStarted;

    var guideStarted = Date.now();
    var guideSummary = getGuideSummaryImpl();
    timings.readGuideMs = Date.now() - guideStarted;

    var collectTargets = [];
    function addTarget(node, reason, priority) {
      try {
        if (!node || !node.worldPosition) return;
        var path = node.path || node.nodePath;
        if (!path) return;
        if (collectTargets.some(function(item) { return item.path === path; })) return;
        collectTargets.push({
          name: node.name || node.nodeName,
          path: path,
          worldPosition: node.worldPosition,
          screenPosition: node.screenPosition,
          reason: reason,
          priority: priority,
          distanceToActor: distanceToActor(actorSummary, node)
        });
      } catch (e) {}
    }
    addTarget(collectAreaRef, "Actor.collectArea", 1);
    addTarget(collectColliderRef, "Actor.collectCollider", 1);
    smallLogs.slice(0, 16).forEach(function(item) { addTarget({ name: item.nodeName, path: item.nodePath, worldPosition: item.worldPosition, screenPosition: item.screenPosition }, "smallLogs", 2); });
    for (var kn in cache.keyRaw) {
      if (/massiveLog|laser1|guidSmallLogs/i.test(kn)) addTarget(compactRaw(cache.keyRaw[kn], false), kn, /guidSmallLogs/i.test(kn) ? 5 : 4);
    }
    addTarget(guideSummary && guideSummary.likelyCurrentTarget, "guide target", 6);
    collectTargets.sort(function(a, b) {
      return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999));
    });

    var keyNumbers = {};
    massiveLogControllers.forEach(function(ctrl) {
      Object.keys(ctrl.fields || {}).forEach(function(key) {
        if (/fallCount|smallLogCount|log|wood|bag|capacity|money|coin|score|count/i.test(key) && typeof ctrl.fields[key] === "number") keyNumbers[key] = ctrl.fields[key];
      });
    });
    keyNumbers.bagLogCount = bagLogSummary.count;
    try {
      var woodBagComp = woodBagEntry && woodBagEntry.component;
      keyNumbers.woodBagOnTableCount = bagCount(woodBagComp);
    } catch (e) {}
    timings.totalMs = Date.now() - t0;

    return {
      ready: true,
      timings: timings,
      actor: {
        nodePath: actorSummary && actorSummary.path,
        worldPosition: actorSummary && actorSummary.worldPosition,
        state: actorState,
        bagLogSummary: bagLogSummary,
        collectAreaRef: collectAreaRef,
        collectColliderRef: collectColliderRef,
        methodNames: actorComp ? methodNames(actorComp).slice(0, 80) : []
      },
      massiveLogControllers: massiveLogControllers,
      smallLogs: smallLogs.slice(0, 40),
      collectTargets: collectTargets.slice(0, 40),
      sellChain: sellChain,
      keyNumbers: keyNumbers,
      guideSummary: guideSummary
    };
  }

  function ensureLightCallCounters(cache) {
    try {
      var store = window.__playableAgentCallCounters = window.__playableAgentCallCounters || { calls: {}, wrapped: {} };
      var methodRegex = /^(getLog|getBagItemCount|getBagRealItemCount|checkWoodOnTable|TryBuy|tryBuy|buy|Buy|sell|Sell|onTriggerEnter|onTriggerStay|onTriggerExit|put|drop|deposit|leave|fly|leaveBag|flyToBag|putLog|dropLog|putWood|dropWood|putFromBag|addLog|addMoney|addCoin|AddMoney|AddCoin|getWood|output|take|pickup|collect|arrive|move|target|updateCount|setCount|recruit|Recruit|hire|Hire|unlock|Unlock|upgrade|Upgrade|complete|finish|active|enable|next|guide|start|Start|run|Run|transport|conveyor|Conveyor|laser|Laser|Progress100|ShowEndCard|gameOver|GameOver|onCompleted|onShowEndCard)$/i;
      var classRegex = /Actor|Bag|WoodBag|WoodBagOnTable|WoodOnTable|Spot|SellNPC|CustomerManager|Customer|HeadBubbleCtrl|Money|Coin|GameManager|MainGame|MoneNum|GuideManager|GuideController|DiTie|Worker|WorkerManager|WorkerController|Laser|LaserController|Conveyor|ConveyorManager|ConveyorLine|Machine/i;
      var entries = compEntries(cache, classRegex, 120);
      function wrap(target, name, meta) {
        try {
          if (!target || typeof target[name] !== "function") return;
          if (!methodRegex.test(name)) return;
          var key = meta.className + "@" + meta.nodePath + "." + name;
          if (store.wrapped[key]) return;
          var original = target[name];
          target[name] = function() {
            var item = store.calls[key] || { callCount: 0, lastCalledAt: 0 };
            item.callCount += 1;
            item.lastCalledAt = Date.now();
            var captureCheckout = /CustomerManager/i.test(meta.className) && /^(checkWoodOnTable|TryBuy|tryBuy)$/i.test(name);
            if (captureCheckout) {
              item.lastArgs = Array.prototype.slice.call(arguments, 0, 6).map(summarizeCallValue);
              item.lastBefore = checkoutStateSnapshot(this);
            }
            try {
              var result = original.apply(this, arguments);
              if (captureCheckout) {
                item.lastReturn = summarizeCallValue(result);
                item.lastAfter = checkoutStateSnapshot(this);
                item.possibleFailure = result === false || result == null;
              }
              store.calls[key] = item;
              return result;
            } catch (error) {
              if (captureCheckout) {
                item.lastError = safeString(error && error.message ? error.message : error);
                item.lastAfter = checkoutStateSnapshot(this);
                item.possibleFailure = true;
              }
              store.calls[key] = item;
              throw error;
            }
          };
          try { target[name].__playableAgentCounterWrapped = true; } catch (e) {}
          store.wrapped[key] = true;
        } catch (e) {}
      }
      entries.forEach(function(entry) {
        var meta = { className: entry.className, nodePath: entry.item.path };
        methodNames(entry.component).forEach(function(name) {
          wrap(entry.component, name, meta);
          try { wrap(Object.getPrototypeOf(entry.component), name, meta); } catch (e) {}
        });
      });
      return getCallCounters();
    } catch (e) {
      return getCallCounters();
    }
  }

  function getSellChainSummaryImpl() {
    var resolved = resolveFastCache(false);
    var cache = resolved.cache;
    if (!cache) return { ready: false };
    ensureLightCallCounters(cache);
    var actorEntry = cache.actorComponent;
    var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), false);
    var actorComp = actorEntry && actorEntry.component;
    var actorBag = actorComp && (actorComp.bagLog || actorComp.logBag || actorComp._bagLog);
    var actor = {
      nodePath: actorSummary && actorSummary.path,
      worldPosition: actorSummary && actorSummary.worldPosition,
      bagLogCount: bagCount(actorBag),
      bagLogSummary: { count: bagCount(actorBag), items: summarizeArrayItems(actorBag, 6), ref: summarizeAny(actorBag) },
      lastPickLogTime: actorComp && actorComp.lastPickLogTime,
      state: actorComp ? {
        isLimitMove: actorComp.isLimitMove,
        isOnButton: actorComp.isOnButton,
        isGetOnButton: actorComp.isGetOnButton,
        isLeavingButton: actorComp.isLeavingButton
      } : {}
    };

    var sellTargets = [];
    function addTarget(node, reason, priority) {
      try {
        if (!node || !node.worldPosition) return;
        var path = node.path || node.nodePath;
        if (!path) return;
        if (sellTargets.some(function(item) { return item.path === path; })) return;
        sellTargets.push({
          name: node.name || node.nodeName,
          path: path,
          worldPosition: node.worldPosition,
          active: node.active,
          components: node.components || [],
          reason: reason,
          priority: priority,
          distanceToActor: distanceToActor(actorSummary, node)
        });
      } catch (e) {}
    }

    var woodBagRaw = cache.keyRaw.woodBagOnTable || fastFindRawByPathOrName(cache.scene, "woodBagOnTable");
    var woodBagEntry = woodBagRaw ? compOnRaw(woodBagRaw, /Bag|WoodBag/i) : null;
    if (!woodBagEntry) {
      var woodBagEntries = compEntries(cache, /woodBagOnTable|WoodBag|Bag/i, 40).filter(function(entry) {
        return /woodBagOnTable|wood.*bag.*table/i.test(entry.item.path + " " + nodeName(entry.item.node) + " " + entry.className);
      });
      woodBagEntry = woodBagEntries[0];
      if (!woodBagRaw && woodBagEntry) woodBagRaw = woodBagEntry.item;
    }
    var woodBagNode = compactRaw(woodBagRaw, false);
    var woodBagComp = woodBagEntry && woodBagEntry.component;
    var woodBagOnTable = woodBagNode ? {
      nodePath: woodBagNode.path,
      worldPosition: woodBagNode.worldPosition,
      active: woodBagNode.active,
      components: woodBagNode.components,
      bagItemCount: bagCount(woodBagComp),
      primitiveFields: primitiveFieldsOf(woodBagComp, /bag|wood|log|count|item|capacity|time|active|enabled|full|empty/i),
      methodNames: woodBagComp ? methodNames(woodBagComp) : []
    } : null;
    addTarget(woodBagNode, "woodBagOnTable", 1);

    var spots = [];
    var spotEntries = compEntries(cache, /Spot|Sell|Table|Wood|Bag/i, 80);
    spotEntries.forEach(function(entry) {
      var text = entry.className + " " + entry.item.path + " " + nodeName(entry.item.node);
      if (!/spot|sell|table|woodBagOnTable|woodOnTable|lastLog/i.test(text)) return;
      var compact = compactRaw(entry.item, false);
      var fields = primitiveFieldsOf(entry.component, /log|wood|bag|sell|lastLogTime|time|count|active|finish|price|money|coin/i);
      var item = {
        nodePath: compact && compact.path,
        worldPosition: compact && compact.worldPosition,
        active: compact && compact.active,
        components: compact && compact.components,
        primitiveFields: fields,
        lastLogTime: fields.lastLogTime,
        distanceToActor: distanceToActor(actorSummary, compact)
      };
      spots.push(item);
      addTarget(compact, /woodBagOnTable/i.test(text) ? "woodBagOnTable spot" : /woodOnTable/i.test(text) ? "woodOnTable spot" : "Spot/sell/table", /woodBagOnTable/i.test(text) ? 1 : 2);
    });

    var sellNPCs = [];
    compEntries(cache, /SellNPC|Customer|Buyer|NPC/i, 60).forEach(function(entry) {
      var compact = compactRaw(entry.item, false);
      var fields = primitiveFieldsOf(entry.component, /buy|sell|customer|wood|money|coin|count|state|active|need|price/i);
      sellNPCs.push({
        nodePath: compact && compact.path,
        worldPosition: compact && compact.worldPosition,
        active: compact && compact.active,
        components: compact && compact.components,
        primitiveFields: fields,
        distanceToActor: distanceToActor(actorSummary, compact)
      });
      addTarget(compact, "SellNPC/Customer", 3);
    });

    var customerManagers = [];
    compEntries(cache, /CustomerManager|Customer|GameManager|MainGame/i, 60).forEach(function(entry) {
      var names = methodNames(entry.component);
      customerManagers.push({
        className: entry.className,
        nodePath: entry.item.path,
        primitiveFields: primitiveFieldsOf(entry.component, /buy|sell|customer|wood|money|coin|count|state|active|need|price|time/i),
        methodNames: names,
        interestingMethods: names.filter(function(name) { return /checkWoodOnTable|TryBuy|tryBuy|buy/i.test(name); }).slice(0, 12)
      });
    });

    var sellRoot = compactRaw(cache.keyRaw.sellSpotRoot || fastFindRawByPathOrName(cache.scene, "sellSpotRoot"), false);
    var woodOnTable = compactRaw(cache.keyRaw.woodOnTable || fastFindRawByPathOrName(cache.scene, "woodOnTable"), false);
    for (var i = 0; i < cache.rawNodes.length; i++) {
      var compact = compactRaw(cache.rawNodes[i], false);
      var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
      if (/sellSpotRoot\//i.test(compact.path) || /woodBagOnTable|woodOnTable|sell|table|spot/i.test(text)) {
        addTarget(compact, /sellSpotRoot\//i.test(compact.path) ? "sellSpotRoot child" : "sell node", /woodBagOnTable/i.test(text) ? 1 : /spot|table/i.test(text) ? 2 : 4);
      }
    }
    addTarget(sellRoot, "sellSpotRoot", 5);
    addTarget(woodOnTable, "woodOnTable", 5);
    sellTargets.sort(function(a, b) {
      return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999));
    });

    var keyNumbers = {
      actorBagLogCount: actor.bagLogCount,
      woodBagOnTableCount: woodBagOnTable && woodBagOnTable.bagItemCount
    };
    compEntries(cache, /Money|Coin|Score|Game|Manager|MainGame|MoneNum/i, 80).forEach(function(entry) {
      var fields = primitiveFieldsOf(entry.component, /money|coin|playCoin|score/i);
      Object.keys(fields).forEach(function(key) {
        if (typeof fields[key] === "number") keyNumbers[key] = fields[key];
      });
    });

    return {
      ready: true,
      actor: actor,
      sellTargets: sellTargets.slice(0, 50),
      woodBagOnTable: woodBagOnTable,
      spots: spots.slice(0, 40),
      sellNPCs: sellNPCs.slice(0, 40),
      customerManagers: customerManagers.slice(0, 40),
      keyNumbers: keyNumbers,
      callCounters: getCallCounters()
    };
  }

  function depositNodeDetail(raw, actorSummary) {
    try {
      var compact = compactRaw(raw, false);
      if (!compact) return null;
      var comp = compOnRaw(raw, /Spot|DiTie|Bag|Wood|Sell|Collider|Trigger/i);
      return {
        nodePath: compact.path,
        worldPosition: compact.worldPosition,
        active: compact.active,
        components: compact.components,
        primitiveFields: comp ? primitiveFieldsOf(comp.component, /log|wood|bag|sell|lastLogTime|time|count|active|finish|price|money|coin|input|machine|spot|fly|leave/i) : {},
        methodNames: comp ? methodNames(comp.component) : [],
        distanceToActor: distanceToActor(actorSummary, compact)
      };
    } catch (e) {}
    return null;
  }

  function depositCallCounters(rawCounters) {
    var calls = (rawCounters && rawCounters.calls) || {};
    function count(re) {
      var total = 0;
      var lastCalledAt = 0;
      Object.keys(calls).forEach(function(key) {
        if (!re.test(key)) return;
        total += Number(calls[key].callCount || 0);
        lastCalledAt = Math.max(lastCalledAt, Number(calls[key].lastCalledAt || 0));
      });
      return { callCount: total, lastCalledAt: lastCalledAt || undefined };
    }
    return {
      actorGetLog: count(/Actor@.*\.getLog/i),
      bagLeave: count(/Bag|WoodBag.*\.(leave|leaveBag|drop|put|deposit)/i),
      bagFly: count(/Bag|WoodBag.*\.(fly|flyToBag)/i),
      spotTriggerEnter: count(/Spot.*\.onTriggerEnter/i),
      spotTriggerStay: count(/Spot.*\.onTriggerStay/i),
      spotLastLog: count(/Spot.*\.(put|drop|deposit|leave|onTrigger)/i),
      woodBagGetBagItemCount: count(/woodOnTable|woodBagOnTable|WoodBag.*\.getBagItemCount/i),
      customerCheckWoodOnTable: count(/CustomerManager.*\.checkWoodOnTable/i),
      tryBuy: count(/TryBuy|tryBuy|\.buy/i)
    };
  }

  function getDepositChainSummaryImpl() {
    var resolved = resolveFastCache(false);
    var cache = resolved.cache;
    if (!cache) return { ready: false };
    ensureLightCallCounters(cache);
    var actorEntry = cache.actorComponent;
    var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), false);
    var actorComp = actorEntry && actorEntry.component;
    var actorBag = actorComp && (actorComp.bagLog || actorComp.logBag || actorComp._bagLog);
    var guide = getGuideVisualSummaryImpl();

    var machineRaw = fastFindRawByPathOrName(cache.scene, "/game/env/Spot/MachineInputSpot") || fastFindRawByPathOrName(cache.scene, "MachineInputSpot");
    var getWoodRaw = fastFindRawByPathOrName(cache.scene, "/game/env/Spot/getWoodSpot") || fastFindRawByPathOrName(cache.scene, "getWoodSpot");
    var woodOnTableRaw = cache.keyRaw.woodOnTable || fastFindRawByPathOrName(cache.scene, "woodOnTable");
    var woodBagRaw = cache.keyRaw.woodBagOnTable || fastFindRawByPathOrName(cache.scene, "woodBagOnTable");
    var sellRootRaw = cache.keyRaw.sellSpotRoot || fastFindRawByPathOrName(cache.scene, "sellSpotRoot");
    var woodBagEntry = woodBagRaw ? compOnRaw(woodBagRaw, /Bag|WoodBag/i) : null;
    var woodBagNode = compactRaw(woodBagRaw, false);
    var woodBagComp = woodBagEntry && woodBagEntry.component;
    var woodBagOnTable = woodBagNode ? {
      nodePath: woodBagNode.path,
      worldPosition: woodBagNode.worldPosition,
      active: woodBagNode.active,
      components: woodBagNode.components,
      bagItemCount: bagCount(woodBagComp),
      primitiveFields: primitiveFieldsOf(woodBagComp, /bag|wood|log|count|item|capacity|time|active|enabled|full|empty|fly|leave/i),
      methodNames: woodBagComp ? methodNames(woodBagComp) : []
    } : null;

    var spots = [];
    compEntries(cache, /Spot|DiTie|Sell|Table|Wood|Bag/i, 120).forEach(function(entry) {
      var text = entry.className + " " + entry.item.path + " " + nodeName(entry.item.node);
      if (!/Spot|spot|getWoodSpot|MachineInputSpot|sell|table|wood|bag|lastLog/i.test(text)) return;
      var compact = compactRaw(entry.item, false);
      var fields = primitiveFieldsOf(entry.component, /log|wood|bag|sell|lastLogTime|time|count|active|finish|price|money|coin|input|machine|spot|fly|leave/i);
      spots.push({
        nodePath: compact && compact.path,
        worldPosition: compact && compact.worldPosition,
        active: compact && compact.active,
        components: compact && compact.components,
        primitiveFields: fields,
        lastLogTime: fields.lastLogTime,
        distanceToActor: distanceToActor(actorSummary, compact)
      });
    });

    var bags = [];
    compEntries(cache, /Bag|WoodBag/i, 80).forEach(function(entry) {
      var fields = primitiveFieldsOf(entry.component, /bag|wood|log|count|item|capacity|time|active|enabled|full|empty|fly|leave|move/i);
      bags.push({
        className: entry.className,
        nodePath: entry.item.path,
        primitiveFields: fields,
        leavingBagTime: fields.leavingBagTime,
        flyToBagTime: fields.flyToBagTime,
        bagItemCount: bagCount(entry.component),
        methodNames: methodNames(entry.component).filter(function(name) { return /put|drop|leave|fly|deposit|bag|log|wood|getBagItemCount/i.test(name); }).slice(0, 40)
      });
    });

    var customerManagers = [];
    compEntries(cache, /CustomerManager|Customer|GameManager|MainGame/i, 80).forEach(function(entry) {
      customerManagers.push({
        className: entry.className,
        nodePath: entry.item.path,
        primitiveFields: primitiveFieldsOf(entry.component, /buy|sell|customer|wood|money|coin|count|state|active|need|price|time/i),
        methodNames: methodNames(entry.component).filter(function(name) { return /checkWoodOnTable|TryBuy|tryBuy|buy|customer|wood/i.test(name); }).slice(0, 40),
        callCounters: getCallCounters()
      });
    });

    var sellRoot = compactRaw(sellRootRaw, false);
    var nearbySpotChildren = [];
    for (var i = 0; i < cache.rawNodes.length && nearbySpotChildren.length < 60; i++) {
      var compact = compactRaw(cache.rawNodes[i], false);
      if (compact && (/\/game\/env\/Spot\//i.test(compact.path) || /\/game\/env\/sellSpotRoot\//i.test(compact.path) || /getWoodSpot|MachineInputSpot|woodOnTable|woodBagOnTable/i.test(compact.name + " " + compact.path))) {
        nearbySpotChildren.push({
          name: compact.name,
          path: compact.path,
          worldPosition: compact.worldPosition,
          active: compact.active,
          components: compact.components,
          distanceToActor: distanceToActor(actorSummary, compact)
        });
      }
    }

    var keyNumbers = {
      actorBagLogCount: bagCount(actorBag),
      woodBagOnTableCount: woodBagOnTable && woodBagOnTable.bagItemCount
    };
    compEntries(cache, /Money|Coin|Score|Game|Manager|MainGame|MoneNum/i, 80).forEach(function(entry) {
      var fields = primitiveFieldsOf(entry.component, /money|coin|playCoin|score/i);
      Object.keys(fields).forEach(function(key) {
        if (typeof fields[key] === "number") keyNumbers[key] = fields[key];
      });
    });

    var counters = getCallCounters();
    return {
      ready: true,
      actor: {
        nodePath: actorSummary && actorSummary.path,
        worldPosition: actorSummary && actorSummary.worldPosition,
        bagLogCount: bagCount(actorBag),
        bagLogSummary: { count: bagCount(actorBag), items: summarizeArrayItems(actorBag, 6), ref: summarizeAny(actorBag) },
        lastPickLogTime: actorComp && actorComp.lastPickLogTime,
        state: actorComp ? {
          isLimitMove: actorComp.isLimitMove,
          isOnButton: actorComp.isOnButton,
          isGetOnButton: actorComp.isGetOnButton,
          isLeavingButton: actorComp.isLeavingButton
        } : {}
      },
      guide: {
        likelyGuideTarget: guide && guide.likelyGuideTarget,
        guideDirection: guide && guide.guideDirection,
        blueIndicators: guide && guide.blueIndicators
      },
      depositNodes: {
        machineInputSpot: compactRaw(machineRaw, false),
        getWoodSpot: compactRaw(getWoodRaw, false),
        woodOnTable: compactRaw(woodOnTableRaw, false),
        woodBagOnTable: compactRaw(woodBagRaw, false),
        sellSpotRoot: sellRoot,
        nearbySpotChildren: nearbySpotChildren
      },
      machineInputSpot: depositNodeDetail(machineRaw, actorSummary),
      getWoodSpot: depositNodeDetail(getWoodRaw, actorSummary),
      spots: spots.slice(0, 60),
      bags: bags.slice(0, 40),
      woodBagOnTable: woodBagOnTable,
      customerManagers: customerManagers.slice(0, 40),
      callCounters: depositCallCounters(counters),
      rawCallCounters: counters,
      keyNumbers: keyNumbers
    };
  }

  function machineCallCounters(counters) {
    function count(regex) {
      var total = 0;
      var lastCalledAt = 0;
      Object.keys(counters || {}).forEach(function(key) {
        if (!regex.test(key)) return;
        var item = counters[key] || {};
        total += Number(item.callCount || 0);
        if (item.lastCalledAt && item.lastCalledAt > lastCalledAt) lastCalledAt = item.lastCalledAt;
      });
      return { callCount: total, lastCalledAt: lastCalledAt || undefined };
    }
    return {
      machineAddLog: count(/Machine.*\.addLog|addLog/i),
      machineOutput: count(/Machine.*\.(output|drop|spawn|create|finish|complete)/i),
      machineGetWood: count(/getWood|take|pickup|collect/i),
      getWoodSpotTriggerEnter: count(/getWoodSpot|Spot.*\.onTriggerEnter/i),
      getWoodSpotTriggerStay: count(/getWoodSpot|Spot.*\.onTriggerStay/i),
      woodBagGetBagItemCount: count(/woodOnTable|woodBagOnTable|WoodBag.*\.getBagItemCount/i),
      customerCheckWoodOnTable: count(/CustomerManager.*\.checkWoodOnTable/i),
      tryBuy: count(/TryBuy|tryBuy|\.buy/i)
    };
  }

  function pickNumber(fields, regex) {
    var keys = Object.keys(fields || {});
    for (var i = 0; i < keys.length; i++) {
      if (regex.test(keys[i]) && typeof fields[keys[i]] === "number") return fields[keys[i]];
    }
    return undefined;
  }

  function machineNodeDetail(raw, actorSummary) {
    var compact = compactRaw(raw, false);
    if (!compact) return null;
    var entry = compOnRaw(raw, /Machine|Spot|Bag|Wood/i);
    var comp = entry && entry.component;
    return {
      nodePath: compact.path,
      worldPosition: compact.worldPosition,
      active: compact.active,
      components: compact.components,
      distanceToActor: distanceToActor(actorSummary, compact),
      lastLogTime: comp && comp.lastLogTime,
      primitiveFields: primitiveFieldsOf(comp, /pendingWoodCount|woodCount|logCount|outputCount|productCount|processedCount|progress|timer|workTime|cd|isWorking|isRunning|isFinish|isComplete|lastLogTime|bag|wood|log|count|item|active|finish/i)
    };
  }

  function getMachineChainSummaryImpl() {
    var resolved = resolveFastCache(false);
    var cache = resolved.cache;
    if (!cache) return { ready: false };
    ensureLightCallCounters(cache);
    var actorEntry = cache.actorComponent;
    var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), false);
    var actorComp = actorEntry && actorEntry.component;
    var actorBagLog = actorComp && (actorComp.bagLog || actorComp.logBag || actorComp._bagLog);
    var actorBagWood = actorComp && (actorComp.bagWood || actorComp.woodBag || actorComp._bagWood);
    var inventoryFields = actorComp ? primitiveFieldsOf(actorComp, /bagLog|bagWood|woodBag|bagItem|items|logs|woods|product|count|lastPickLogTime|log|wood|bag/i) : {};

    var machineEntries = compEntries(cache, /(^|\.|\/)Machine$|Machine|Conveyor|Wood|BagSort/i, 120);
    var machineEntry = null;
    for (var mi = 0; mi < machineEntries.length; mi++) {
      var text = machineEntries[mi].className + " " + machineEntries[mi].item.path + " " + nodeName(machineEntries[mi].item.node);
      if (/^Machine$/i.test(machineEntries[mi].className) && !/MachineInputSpot/i.test(text)) {
        machineEntry = machineEntries[mi];
        break;
      }
    }
    if (!machineEntry) {
      for (var mj = 0; mj < machineEntries.length; mj++) {
        var text2 = machineEntries[mj].className + " " + machineEntries[mj].item.path + " " + nodeName(machineEntries[mj].item.node);
        if (/Machine/i.test(machineEntries[mj].className) && !/MachineInputSpot/i.test(text2)) {
          machineEntry = machineEntries[mj];
          break;
        }
      }
    }
    if (!machineEntry && machineEntries.length) machineEntry = machineEntries[0];
    var machineComp = machineEntry && machineEntry.component;
    var machineCompact = machineEntry && compactRaw(machineEntry.item, false);
    var machineFields = primitiveFieldsOf(machineComp, /pendingWoodCount|woodCount|logCount|outputCount|productCount|processedCount|progress|timer|workTime|cd|isWorking|isRunning|isFinish|isComplete|active|finish|input|output|bag|wood|log|count/i);
    var inputPoint = machineComp ? (refToNodeSummary(machineComp.inputPoint) || refToNodeSummary(machineComp._inputPoint)) : null;
    var outputPoint = machineComp ? (refToNodeSummary(machineComp.outputPoint) || refToNodeSummary(machineComp._outputPoint) || refToNodeSummary(machineComp.outPoint)) : null;

    var machineInputRaw = fastFindRawByPathOrName(cache.scene, "/game/env/Spot/MachineInputSpot") || fastFindRawByPathOrName(cache.scene, "MachineInputSpot");
    var getWoodRaw = fastFindRawByPathOrName(cache.scene, "/game/env/Spot/getWoodSpot") || fastFindRawByPathOrName(cache.scene, "getWoodSpot");
    var woodOnTableRaw = cache.keyRaw.woodOnTable || fastFindRawByPathOrName(cache.scene, "woodOnTable");
    var woodBagRaw = cache.keyRaw.woodBagOnTable || fastFindRawByPathOrName(cache.scene, "woodBagOnTable");
    var woodBagEntry = woodBagRaw ? compOnRaw(woodBagRaw, /Bag|WoodBag/i) : null;
    var woodBagComp = woodBagEntry && woodBagEntry.component;
    var woodBagCompact = compactRaw(woodBagRaw, false);

    var outputNodes = [];
    function addOutput(nodeLike, reason) {
      try {
        if (!nodeLike) return;
        var node = nodeLike.node ? compactRaw(nodeLike, false) : nodeLike;
        if (!node || !node.worldPosition) return;
        var path = node.path || node.nodePath;
        if (!path || outputNodes.some(function(item) { return item.path === path; })) return;
        outputNodes.push({
          name: node.name || node.nodeName,
          path: path,
          active: node.active,
          worldPosition: node.worldPosition,
          components: node.components || [],
          primitiveFields: node.primitiveFields || {},
          distanceToActor: distanceToActor(actorSummary, node),
          reason: reason
        });
      } catch (e) {}
    }
    addOutput(outputPoint, "Machine.outputPoint");
    for (var oi = 0; oi < cache.rawNodes.length && outputNodes.length < 40; oi++) {
      var compact = compactRaw(cache.rawNodes[oi], false);
      var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
      var nearGetWood = distanceToActor(compactRaw(getWoodRaw, false), compact);
      var inMachine = machineCompact && compact.path.indexOf(machineCompact.path + "/") === 0;
      if ((compact.active && /wood|log|product|output|board/i.test(text) && (inMachine || (typeof nearGetWood === "number" && nearGetWood <= 5))) || /getWoodSpot|woodOnTable|woodBagOnTable/i.test(text)) {
        compact.primitiveFields = {};
        addOutput(compact, inMachine ? "machine child" : "near getWoodSpot");
      }
    }

    var customerManagers = [];
    compEntries(cache, /CustomerManager|Customer|GameManager|MainGame/i, 60).forEach(function(entry) {
      customerManagers.push({
        className: entry.className,
        nodePath: entry.item.path,
        primitiveFields: primitiveFieldsOf(entry.component, /buy|sell|customer|wood|money|coin|count|state|active|need|price|time/i),
        methodNames: methodNames(entry.component).filter(function(name) { return /checkWoodOnTable|TryBuy|tryBuy|buy|customer|wood/i.test(name); }).slice(0, 40)
      });
    });

    var counters = getCallCounters();
    var keyNumbers = {
      actorBagLogCount: bagCount(actorBagLog),
      actorBagWoodCount: bagCount(actorBagWood),
      pendingWoodCount: pickNumber(machineFields, /pendingWoodCount|pending/i),
      outputWoodCount: pickNumber(machineFields, /outputWoodCount|outputCount|woodCount|productCount/i),
      processedWoodCount: pickNumber(machineFields, /processedWoodCount|processedCount|productCount/i),
      woodBagOnTableCount: bagCount(woodBagComp)
    };
    compEntries(cache, /Money|Coin|Score|Game|Manager|MainGame|MoneNum/i, 80).forEach(function(entry) {
      var fields = primitiveFieldsOf(entry.component, /money|coin|playCoin|score/i);
      Object.keys(fields).forEach(function(key) {
        if (typeof fields[key] === "number") keyNumbers[key] = fields[key];
      });
    });

    return {
      ready: true,
      actor: {
        nodePath: actorSummary && actorSummary.path,
        worldPosition: actorSummary && actorSummary.worldPosition,
        bagLogCount: bagCount(actorBagLog),
        bagWoodCount: bagCount(actorBagWood),
        bagItemCount: bagCount(actorBagLog) || bagCount(actorBagWood),
        possibleInventoryFields: inventoryFields,
        bagLogSummary: { count: bagCount(actorBagLog), items: summarizeArrayItems(actorBagLog, 6), ref: summarizeAny(actorBagLog) },
        bagWoodSummary: { count: bagCount(actorBagWood), items: summarizeArrayItems(actorBagWood, 6), ref: summarizeAny(actorBagWood) },
        lastPickLogTime: actorComp && actorComp.lastPickLogTime,
        state: actorComp ? {
          isLimitMove: actorComp.isLimitMove,
          isOnButton: actorComp.isOnButton,
          isGetOnButton: actorComp.isGetOnButton,
          isLeavingButton: actorComp.isLeavingButton
        } : {}
      },
      machine: machineEntry ? {
        nodePath: machineCompact && machineCompact.path,
        worldPosition: machineCompact && machineCompact.worldPosition,
        className: machineEntry.className,
        primitiveFields: machineFields,
        pendingWoodCount: keyNumbers.pendingWoodCount,
        outputWoodCount: keyNumbers.outputWoodCount,
        processedWoodCount: keyNumbers.processedWoodCount,
        progress: pickNumber(machineFields, /progress|percent/i),
        timer: pickNumber(machineFields, /timer|time|workTime|cd/i),
        isWorking: Boolean(machineFields.isWorking || machineFields.working),
        isRunning: Boolean(machineFields.isRunning || machineFields.running),
        inputPoint: inputPoint,
        outputPoint: outputPoint,
        methodNames: methodNames(machineComp).filter(function(name) { return /addLog|getWood|output|product|processed|wood|log|finish|complete|update/i.test(name); }).slice(0, 80),
        callCounters: machineCallCounters(counters)
      } : null,
      machineInputSpot: machineNodeDetail(machineInputRaw, actorSummary),
      getWoodSpot: machineNodeDetail(getWoodRaw, actorSummary),
      outputNodes: outputNodes.slice(0, 40),
      woodOnTable: compactRaw(woodOnTableRaw, false),
      woodBagOnTable: woodBagCompact ? {
        nodePath: woodBagCompact.path,
        worldPosition: woodBagCompact.worldPosition,
        bagItemCount: bagCount(woodBagComp),
        primitiveFields: primitiveFieldsOf(woodBagComp, /bag|wood|log|count|item|capacity|time|active|enabled|full|empty|fly|leave/i),
        methodNames: woodBagComp ? methodNames(woodBagComp) : []
      } : null,
      customerManagers: customerManagers,
      callCounters: machineCallCounters(counters),
      rawCallCounters: counters,
      keyNumbers: keyNumbers
    };
  }

  function tableCallCounters(counters) {
    function count(regex) {
      var total = 0;
      var lastCalledAt = 0;
      Object.keys(counters || {}).forEach(function(key) {
        if (!regex.test(key)) return;
        var item = counters[key] || {};
        total += Number(item.callCount || 0);
        if (item.lastCalledAt && item.lastCalledAt > lastCalledAt) lastCalledAt = item.lastCalledAt;
      });
      return { callCount: total, lastCalledAt: lastCalledAt || undefined };
    }
    return {
      woodBagGetBagItemCount: count(/woodBagOnTable|WoodBag.*\.getBagItemCount/i),
      woodOnTableGetBagItemCount: count(/woodOnTable|WoodOnTable.*\.getBagItemCount/i),
      customerCheckWoodOnTable: count(/CustomerManager.*\.checkWoodOnTable/i),
      tryBuy: count(/TryBuy|tryBuy|\.buy/i),
      tableSpotTriggerEnter: count(/woodOnTable|woodBagOnTable|Spot.*\.onTriggerEnter/i),
      tableSpotTriggerStay: count(/woodOnTable|woodBagOnTable|Spot.*\.onTriggerStay/i),
      bagLeave: count(/Bag|WoodBag.*\.(leave|leaveBag|put|putFromBag|drop|deposit)/i),
      bagFly: count(/Bag|WoodBag.*\.(fly|flyToBag)/i),
      actorPutWood: count(/Actor.*\.(putWood|put|dropWood|deposit|leave)/i),
      actorDropWood: count(/Actor.*\.(dropWood|drop|putWood|deposit)/i)
    };
  }

  function countByNamedBag(comp, names) {
    try {
      if (!comp) return undefined;
      for (var i = 0; i < names.length; i++) {
        var count = bagCount(comp[names[i]]);
        if (typeof count === "number") return count;
      }
    } catch (e) {}
    return undefined;
  }

  function tableNodeDetail(raw, actorSummary, compRegex) {
    try {
      var compact = compactRaw(raw, false);
      if (!compact) return null;
      var entry = compOnRaw(raw, compRegex || /Spot|Bag|Wood|Customer|Table|Collider|Trigger/i);
      var comp = entry && entry.component;
      return {
        nodePath: compact.path,
        worldPosition: compact.worldPosition,
        active: compact.active,
        components: compact.components,
        primitiveFields: comp ? primitiveFieldsOf(comp, /bagWood|woodBag|bagItem|items|woods|product|processed|count|lastLogTime|lastWoodTime|lastBagTime|inputTime|stayTime|bag|wood|table|customer|buy|sell|price|money|coin|active|enabled|fly|leave/i) : {},
        methodNames: comp ? methodNames(comp).filter(function(name) { return /put|drop|leave|fly|deposit|table|bag|wood|product|processed|sell|buy|checkWood|TryBuy|customer|trigger|getBagItemCount/i.test(name); }).slice(0, 80) : [],
        distanceToActor: distanceToActor(actorSummary, compact)
      };
    } catch (e) {}
    return null;
  }

  function getTableCustomerChainSummaryImpl() {
    var resolved = resolveFastCache(false);
    var cache = resolved.cache;
    if (!cache) return { ready: false };
    ensureLightCallCounters(cache);
    var actorEntry = cache.actorComponent;
    var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), false);
    var actorComp = actorEntry && actorEntry.component;
    var actorBagLog = actorComp && (actorComp.bagLog || actorComp.logBag || actorComp._bagLog);
    var actorBagWood = actorComp && (actorComp.bagWood || actorComp.woodBag || actorComp._bagWood || actorComp.bagProduct || actorComp.productBag);
    var inventoryFields = actorComp ? primitiveFieldsOf(actorComp, /bagLog|bagWood|woodBag|bagItem|items|woods|product|processed|count|lastPickLogTime|log|wood|bag/i) : {};
    var guideVisual = getGuideVisualSummaryImpl();
    var woodOnTableRaw = cache.keyRaw.woodOnTable || fastFindRawByPathOrName(cache.scene, "/game/env/woodOnTable") || fastFindRawByPathOrName(cache.scene, "woodOnTable");
    var woodBagRaw = cache.keyRaw.woodBagOnTable || fastFindRawByPathOrName(cache.scene, "woodBagOnTable");
    compEntries(cache, /CustomerManager|Customer/i, 20).some(function(entry) {
      try {
        var keys = ownKeys(entry.component).filter(function(key) { return /woodBagOnTable|woodOnTable|woodBag/i.test(key); });
        for (var i = 0; i < keys.length; i++) {
          var ref = refToNodeSummary(entry.component[keys[i]]);
          if (ref && (ref.nodePath || ref.path)) {
            var raw = fastFindRawByPathOrName(cache.scene, ref.nodePath || ref.path);
            if (raw && !woodBagRaw) woodBagRaw = raw;
            if (raw && !woodOnTableRaw && /woodOnTable/i.test(ref.nodePath || ref.path || "")) woodOnTableRaw = raw;
          }
        }
      } catch (e) {}
      return Boolean(woodBagRaw);
    });
    var woodOnTableEntry = woodOnTableRaw ? compOnRaw(woodOnTableRaw, /WoodOnTable|WoodBag|Bag|Spot|Table|Wood/i) : null;
    var woodBagEntry = woodBagRaw ? compOnRaw(woodBagRaw, /WoodBag|Bag|WoodOnTable|Table|Wood/i) : null;
    var woodOnTableNode = compactRaw(woodOnTableRaw, false);
    var woodBagNode = compactRaw(woodBagRaw, false);
    var woodOnTableComp = woodOnTableEntry && woodOnTableEntry.component;
    var woodBagComp = woodBagEntry && woodBagEntry.component;
    var tableTargets = [];
    function addTarget(node, reason, priority) {
      try {
        if (!node || !node.worldPosition) return;
        var path = node.path || node.nodePath;
        if (!path || tableTargets.some(function(item) { return item.path === path; })) return;
        var raw = fastFindRawByPathOrName(cache.scene, path);
        var comp = raw ? compOnRaw(raw, /Spot|Bag|Wood|Customer|Table|Collider|Trigger/i) : null;
        tableTargets.push({
          name: node.name || node.nodeName,
          path: path,
          worldPosition: node.worldPosition,
          active: node.active,
          components: node.components || [],
          primitiveFields: comp ? primitiveFieldsOf(comp.component, /bagWood|woodBag|bagItem|items|woods|product|processed|count|lastLogTime|lastWoodTime|lastBagTime|inputTime|stayTime|bag|wood|table|customer|buy|sell|price|money|coin|active|enabled|fly|leave/i) : {},
          methodNames: comp ? methodNames(comp.component).filter(function(name) { return /put|drop|leave|fly|deposit|table|bag|wood|product|sell|buy|checkWood|TryBuy|trigger|getBagItemCount/i.test(name); }).slice(0, 50) : [],
          reason: reason,
          priority: priority,
          distanceToActor: distanceToActor(actorSummary, node)
        });
      } catch (e) {}
    }
    var guideTarget = guideVisual && guideVisual.likelyGuideTarget;
    if (guideTarget && guideTarget.worldPosition) {
      for (var gi = 0; gi < cache.rawNodes.length; gi++) {
        var gCompact = compactRaw(cache.rawNodes[gi], false);
        var gText = gCompact.name + " " + gCompact.path + " " + gCompact.components.join(" ");
        var dGuide = distanceXZBetween(guideTarget.worldPosition, gCompact.worldPosition);
        if (dGuide != null && dGuide <= 5 && /table|woodOnTable|woodBag|desk|customer|buy|sell|spot|wood/i.test(gText)) {
          addTarget(gCompact, "near guide table/customer target", /woodOnTable|woodBag/i.test(gText) ? 1 : /spot|table/i.test(gText) ? 2 : 3);
        }
      }
    }
    addTarget(woodOnTableNode, "woodOnTable", 2);
    addTarget(woodBagNode, "woodBagOnTable", 2);
    for (var ti = 0; ti < cache.rawNodes.length && tableTargets.length < 80; ti++) {
      var compact = compactRaw(cache.rawNodes[ti], false);
      var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
      if (/getWoodSpot/i.test(text)) addTarget(compact, "getWoodSpot fallback", 9);
      else if (/woodOnTable|woodBagOnTable|woodBag|table|desk|customer|buy|sell/i.test(text)) {
        addTarget(compact, "table/customer node", /woodOnTable|woodBagOnTable|woodBag/i.test(text) ? 2 : /spot|table|desk/i.test(text) ? 3 : /customer/i.test(text) ? 5 : 6);
      }
    }
    tableTargets.sort(function(a, b) {
      return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999));
    });
    var tableSpots = [];
    compEntries(cache, /Spot|Table|Wood|Bag|Customer/i, 120).forEach(function(entry) {
      var text = entry.className + " " + entry.item.path + " " + nodeName(entry.item.node);
      if (!/Spot|spot|table|woodOnTable|woodBag|customer|sell|buy/i.test(text)) return;
      var compact = compactRaw(entry.item, false);
      var fields = primitiveFieldsOf(entry.component, /lastLogTime|lastWoodTime|lastBagTime|inputTime|stayTime|log|wood|bag|table|customer|buy|sell|count|time|active|enabled/i);
      tableSpots.push({
        nodePath: compact && compact.path,
        worldPosition: compact && compact.worldPosition,
        active: compact && compact.active,
        components: compact && compact.components,
        primitiveFields: fields,
        lastLogTime: fields.lastLogTime,
        lastWoodTime: fields.lastWoodTime,
        distanceToActor: distanceToActor(actorSummary, compact)
      });
    });
    var customers = [];
    compEntries(cache, /Customer|SellNPC|Buyer|NPC/i, 80).forEach(function(entry) {
      var compact = compactRaw(entry.item, false);
      var fields = primitiveFieldsOf(entry.component, /customer|buy|sell|wood|money|coin|count|state|active|need|price|time|wait/i);
      customers.push({
        nodePath: compact && compact.path,
        worldPosition: compact && compact.worldPosition,
        active: compact && compact.active,
        components: compact && compact.components,
        primitiveFields: fields,
        distanceToActor: distanceToActor(actorSummary, compact)
      });
      addTarget(compact, "Customer/CustomerManager nearby", 6);
    });
    var customerManagers = [];
    compEntries(cache, /CustomerManager|Customer|GameManager|MainGame/i, 80).forEach(function(entry) {
      customerManagers.push({
        className: entry.className,
        nodePath: entry.item.path,
        primitiveFields: primitiveFieldsOf(entry.component, /buy|sell|customer|wood|money|coin|count|state|active|need|price|time|wait/i),
        methodNames: methodNames(entry.component).filter(function(name) { return /checkWoodOnTable|TryBuy|tryBuy|buy|customer|wood|table/i.test(name); }).slice(0, 80),
        callCounters: tableCallCounters(getCallCounters())
      });
    });
    var bags = [];
    compEntries(cache, /Bag|WoodBag/i, 100).forEach(function(entry) {
      var fields = primitiveFieldsOf(entry.component, /bagLog|bagWood|woodBag|bagItem|items|woods|product|processed|count|leavingBagTime|flyToBagTime|bag|wood|log|item|capacity|time|active|enabled|full|empty|fly|leave|move/i);
      bags.push({
        className: entry.className,
        nodePath: entry.item.path,
        primitiveFields: fields,
        bagLogCount: countByNamedBag(entry.component, ["bagLog", "logBag", "_bagLog", "logs"]),
        bagWoodCount: countByNamedBag(entry.component, ["bagWood", "woodBag", "_bagWood", "woods", "products"]),
        bagItemCount: bagCount(entry.component),
        leavingBagTime: fields.leavingBagTime,
        flyToBagTime: fields.flyToBagTime,
        methodNames: methodNames(entry.component).filter(function(name) { return /put|drop|leave|fly|deposit|table|bag|log|wood|getBagItemCount/i.test(name); }).slice(0, 80)
      });
    });
    var counters = getCallCounters();
    var keyNumbers = {
      actorBagLogCount: bagCount(actorBagLog),
      actorBagWoodCount: bagCount(actorBagWood),
      actorBagItemCount: bagCount(actorBagLog) || bagCount(actorBagWood),
      woodBagOnTableCount: bagCount(woodBagComp),
      woodOnTableCount: bagCount(woodOnTableComp)
    };
    compEntries(cache, /Money|Coin|Score|Game|Manager|MainGame|MoneNum/i, 80).forEach(function(entry) {
      var fields = primitiveFieldsOf(entry.component, /money|coin|playCoin|score/i);
      Object.keys(fields).forEach(function(key) {
        if (typeof fields[key] === "number") keyNumbers[key] = fields[key];
      });
    });
    return {
      ready: true,
      actor: {
        nodePath: actorSummary && actorSummary.path,
        worldPosition: actorSummary && actorSummary.worldPosition,
        bagLogCount: bagCount(actorBagLog),
        bagWoodCount: bagCount(actorBagWood),
        bagItemCount: bagCount(actorBagLog) || bagCount(actorBagWood),
        possibleInventoryFields: inventoryFields,
        lastPickLogTime: actorComp && actorComp.lastPickLogTime,
        state: actorComp ? {
          isLimitMove: actorComp.isLimitMove,
          isOnButton: actorComp.isOnButton,
          isGetOnButton: actorComp.isGetOnButton,
          isLeavingButton: actorComp.isLeavingButton
        } : {}
      },
      guide: {
        likelyGuideTarget: guideVisual && guideVisual.likelyGuideTarget,
        guideDirection: guideVisual && guideVisual.guideDirection,
        blueIndicators: guideVisual && guideVisual.blueIndicators
      },
      tableTargets: tableTargets.slice(0, 80),
      woodOnTable: woodOnTableNode ? Object.assign(tableNodeDetail(woodOnTableRaw, actorSummary, /WoodOnTable|WoodBag|Bag|Spot|Table|Wood/i) || {}, { bagItemCount: bagCount(woodOnTableComp) }) : null,
      woodBagOnTable: woodBagNode ? Object.assign(tableNodeDetail(woodBagRaw, actorSummary, /WoodBag|Bag|WoodOnTable|Table|Wood/i) || {}, { bagItemCount: bagCount(woodBagComp) }) : null,
      tableSpots: tableSpots.slice(0, 80),
      customers: customers.slice(0, 80),
      customerManagers: customerManagers.slice(0, 40),
      bags: bags.slice(0, 60),
      callCounters: tableCallCounters(counters),
      rawCallCounters: counters,
      keyNumbers: keyNumbers
    };
  }

  function fieldsByType(fields) {
    var booleanFields = {};
    var numericFields = {};
    var stringFields = {};
    try {
      Object.keys(fields || {}).forEach(function(key) {
        var value = fields[key];
        if (typeof value === "boolean") booleanFields[key] = value;
        else if (typeof value === "number") numericFields[key] = value;
        else if (typeof value === "string") stringFields[key] = value;
      });
    } catch (e) {}
    return { booleanFields: booleanFields, numericFields: numericFields, stringFields: stringFields };
  }

  function latestCounter(counters, regex) {
    var best = null;
    try {
      Object.keys(counters || {}).forEach(function(key) {
        if (!regex.test(key)) return;
        var item = counters[key] || {};
        if (!best || Number(item.lastCalledAt || 0) > Number(best.lastCalledAt || 0)) {
          best = Object.assign({ key: key }, item);
        }
      });
    } catch (e) {}
    return best;
  }

  function findComponentByClassPriority(cache, pattern, options) {
    options = options || {};
    var regex = pattern instanceof RegExp ? pattern : new RegExp(String(pattern || ""), "i");
    var excluded = options.excludedClassRegex || /^(cc\.)?(UITransform|RenderRoot2D|Sprite|Label|Canvas|Widget|Button|Mask|Graphics|RichText|Layout)$/i;
    var candidates = [];
    var rejected = [];
    try {
      compEntries(cache, regex, options.limit || 160).forEach(function(entry) {
        var className = String(entry.className || "");
        var methods = methodNames(entry.component);
        var path = String(entry.item && entry.item.path || "");
        var node = entry.item && entry.item.node;
        var hasRequiredMethod = (options.preferMethods || []).some(function(name) { return methods.indexOf(name) >= 0; });
        var hasRequiredRef = false;
        try {
          hasRequiredRef = (options.preferRefs || []).some(function(refName) { return entry.component && entry.component[refName] != null; });
        } catch (e) {}
        var classMatches = regex.test(className);
        var exactClass = options.exactClass && (className === options.exactClass || className.slice(-options.exactClass.length - 1) === "." + options.exactClass);
        var rejectReason = "";
        if (!classMatches && options.classOnly !== false) rejectReason = "className does not match";
        if (!rejectReason && excluded.test(className)) rejectReason = "excluded UI/render component";
        if (!rejectReason && options.requireMethod && !hasRequiredMethod) rejectReason = "missing required method";
        var item = {
          className: className,
          nodeName: nodeName(node),
          nodePath: path,
          methodNames: methods.filter(function(name) { return /(checkWoodOnTable|TryBuy|tryBuy|buy|sell|customer|wood|table)/i.test(name); }).slice(0, 30),
          hasPreferredMethod: hasRequiredMethod,
          hasPreferredRef: hasRequiredRef,
          rejectReason: rejectReason || undefined,
          component: entry.component,
          item: entry.item,
          score: 0
        };
        if (rejectReason) {
          rejected.push(item);
          return;
        }
        item.score += exactClass ? 1000 : 0;
        item.score += classMatches ? 300 : 0;
        if (options.preferPath && path === options.preferPath) item.score += 800;
        if (hasRequiredMethod) item.score += 250;
        if (hasRequiredRef) item.score += 120;
        if (/Manager$/i.test(className)) item.score += 50;
        candidates.push(item);
      });
      candidates.sort(function(a, b) {
        return (b.score - a.score) || String(a.nodePath).localeCompare(String(b.nodePath));
      });
    } catch (e) {}
    var selected = candidates[0] || null;
    return {
      selected: selected,
      candidates: candidates.map(function(item) {
        return {
          className: item.className,
          nodeName: item.nodeName,
          nodePath: item.nodePath,
          methodNames: item.methodNames,
          hasPreferredMethod: item.hasPreferredMethod,
          hasPreferredRef: item.hasPreferredRef,
          score: item.score
        };
      }),
      rejected: rejected.map(function(item) {
        return {
          className: item.className,
          nodeName: item.nodeName,
          nodePath: item.nodePath,
          methodNames: item.methodNames,
          rejectReason: item.rejectReason
        };
      }).slice(0, 40),
      reason: selected ? "selected by className/method/ref/path priority" : "no matching component"
    };
  }

  function getCustomerBuyChainSummaryImpl() {
    var resolved = resolveFastCache(false);
    var cache = resolved.cache;
    if (!cache) return { ready: false };
    ensureLightCallCounters(cache);
    var tableSummary = getTableCustomerChainSummaryImpl();
    var counters = getCallCounters();
    var actorEntry = cache.actorComponent;
    var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), false);
    var actorComp = actorEntry && actorEntry.component;
    var actorBagLog = actorComp && (actorComp.bagLog || actorComp.logBag || actorComp._bagLog);
    var actorBagWood = actorComp && (actorComp.bagWood || actorComp.woodBag || actorComp._bagWood || actorComp.bagProduct || actorComp.productBag);
    var inventoryFields = actorComp ? primitiveFieldsOf(actorComp, /bagLog|bagWood|woodBag|bagItem|items|woods|product|processed|count|lastPickLogTime|log|wood|bag/i) : {};

    var managerSelection = findComponentByClassPriority(cache, /CustomerManager/i, {
      exactClass: "CustomerManager",
      preferPath: "/game/CustomerManager",
      preferMethods: ["checkWoodOnTable", "TryBuy", "tryBuy"],
      preferRefs: ["woodBagOnTable", "woodOnTable"],
      requireMethod: false,
      classOnly: true,
      limit: 120
    });
    var managerEntry = managerSelection.selected || null;
    var managerComp = managerEntry && managerEntry.component;
    var managerFields = managerComp ? primitiveFieldsOf(managerComp, /canSell|isSell|selling|customer|customers|queue|buyer|target|workerNode|worker|money|coin|playCoin|score|woodBagOnTable|woodOnTable|table|need|count|price|active|state|wait|time/i) : {};
    var managerTyped = fieldsByType(managerFields);
    var managerRefs = {};
    if (managerComp) {
      ownKeys(managerComp).slice(0, 140).forEach(function(key) {
        try {
          if (!/customer|queue|buyer|target|worker|woodBagOnTable|woodOnTable|table|money|spot/i.test(key)) return;
          var value = managerComp[key];
          if (Array.isArray(value)) managerRefs[key] = { kind: "array", length: value.length, sample: summarizeArrayNodeRefs(value, 5) };
          else {
            var ref = refToNodeSummary(value);
            if (ref) managerRefs[key] = ref;
          }
        } catch (e) {}
      });
    }
    var latestTryBuy = latestCounter(counters, /CustomerManager.*\.(TryBuy|tryBuy)$/i);
    var latestCheck = latestCounter(counters, /CustomerManager.*\.checkWoodOnTable$/i);

    var tableRefs = tableBagRefs(cache, managerComp);
    var woodOnTableNode = compactRaw(tableRefs.woodOnTableRaw, false);
    var woodBagNode = compactRaw(tableRefs.woodBagRaw, false);

    var customers = [];
    compEntries(cache, /Customer|Buyer|NPC/i, 100).forEach(function(entry) {
      if (/CustomerManager/i.test(entry.className)) return;
      var compact = compactRaw(entry.item, false);
      var fields = primitiveFieldsOf(entry.component, /state|target|canSell|isBuying|isMoving|isArrive|arrive|need|count|price|headBubble|bubble|buy|table|wood|item|active|wait|move|customer/i);
      var typed = fieldsByType(fields);
      var targetRefs = {};
      ownKeys(entry.component).slice(0, 100).forEach(function(key) {
        try {
          if (!/target|table|head|bubble|wood|spot|customer|money/i.test(key)) return;
          var ref = refToNodeSummary(entry.component[key]);
          if (ref) targetRefs[key] = ref;
        } catch (e) {}
      });
      var headBubble = targetRefs.headBubble || targetRefs.bubble || targetRefs._headBubble || null;
      customers.push({
        nodePath: compact && compact.path,
        worldPosition: compact && compact.worldPosition,
        active: compact && compact.active,
        components: compact && compact.components,
        primitiveFields: fields,
        booleanFields: typed.booleanFields,
        numericFields: typed.numericFields,
        stateFields: fields,
        distanceToTable: distanceToActor(woodOnTableNode || woodBagNode, compact),
        distanceToActor: distanceToActor(actorSummary, compact),
        headBubble: headBubble,
        canSell: fields.canSell,
        targetRefs: targetRefs,
        methodNames: methodNames(entry.component).filter(function(name) { return /buy|sell|customer|arrive|leave|move|target|table|head|bubble|count/i.test(name); }).slice(0, 80)
      });
    });

    var headBubbles = [];
    compEntries(cache, /HeadBubbleCtrl|HeadBubble|Bubble/i, 80).forEach(function(entry) {
      var compact = compactRaw(entry.item, false);
      var fields = primitiveFieldsOf(entry.component, /_curCount|curCount|count|needCount|targetCount|maxCount|itemType|wood|state|active|show|hide/i);
      headBubbles.push({
        nodePath: compact && compact.path,
        active: compact && compact.active,
        primitiveFields: fields,
        curCount: fields._curCount != null ? fields._curCount : fields.curCount,
        targetCount: fields.targetCount != null ? fields.targetCount : fields.maxCount,
        needCount: fields.needCount,
        itemType: fields.itemType,
        methodNames: methodNames(entry.component).filter(function(name) { return /count|set|update|show|hide|head|bubble/i.test(name); }).slice(0, 60)
      });
    });

    var workers = [];
    function addWorker(ref, reason) {
      try {
        if (!ref) return;
        var node = ref.node || ref;
        var compact = looksLikeNode(node) ? summarizeNodeRef(node) : refToNodeSummary(ref);
        if (!compact) return;
        workers.push({
          nodePath: compact.nodePath || compact.path,
          active: compact.active,
          primitiveFields: {},
          workerNodeActive: compact.active,
          distanceToTable: distanceToActor(woodOnTableNode || woodBagNode, { worldPosition: compact.worldPosition }),
          methodNames: [],
          reason: reason
        });
      } catch (e) {}
    }
    if (managerComp) addWorker(managerComp.workerNode, "CustomerManager.workerNode");
    try { addWorker(window.g && window.g.sceneManager && window.g.sceneManager.workerNode, "g.sceneManager.workerNode"); } catch (e) {}
    compEntries(cache, /Worker|Staff/i, 40).forEach(function(entry) {
      var compact = compactRaw(entry.item, false);
      workers.push({
        nodePath: compact && compact.path,
        active: compact && compact.active,
        primitiveFields: primitiveFieldsOf(entry.component, /worker|state|active|move|target|buy|sell/i),
        workerNodeActive: compact && compact.active,
        distanceToTable: distanceToActor(woodOnTableNode || woodBagNode, compact),
        methodNames: methodNames(entry.component).slice(0, 50)
      });
    });

    var moneyTargets = [];
    for (var i = 0; i < cache.rawNodes.length && moneyTargets.length < 80; i++) {
      var compactNode = compactRaw(cache.rawNodes[i], false);
      var text = compactNode.name + " " + compactNode.path + " " + compactNode.components.join(" ");
      if (!/moneySpotRoot|moneySpot|cash|coin|money|playCoin/i.test(text)) continue;
      moneyTargets.push({
        name: compactNode.name,
        path: compactNode.path,
        worldPosition: compactNode.worldPosition,
        active: compactNode.active,
        components: compactNode.components,
        primitiveFields: compOnRaw(cache.rawNodes[i], /Money|Coin|Spot|Collider|Trigger/i) ? primitiveFieldsOf(compOnRaw(cache.rawNodes[i], /Money|Coin|Spot|Collider|Trigger/i).component, /money|coin|playCoin|score|active|count|state|time/i) : {},
        distanceToActor: distanceToActor(actorSummary, compactNode),
        reason: /moneySpotRoot/i.test(text) ? "moneySpotRoot" : /moneySpot/i.test(text) ? "moneySpot" : /coin|cash|money/i.test(text) ? "money/coin node" : "money candidate",
        priority: /moneySpotRoot/i.test(text) ? 1 : /moneySpot/i.test(text) ? 2 : /cash|coin|money/i.test(text) ? 3 : 5
      });
    }
    moneyTargets.sort(function(a, b) {
      return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999));
    });

    var tableCounts = {
      woodOnTableCount: bagCount(tableRefs.woodOnTableComp),
      woodBagOnTableCount: bagCount(tableRefs.woodBagComp),
      realCount: safeBagRealCount(tableRefs.woodBagComp) != null ? safeBagRealCount(tableRefs.woodBagComp) : safeBagRealCount(tableRefs.woodOnTableComp),
      itemCount: bagCount(tableRefs.woodBagComp) != null ? bagCount(tableRefs.woodBagComp) : bagCount(tableRefs.woodOnTableComp)
    };
    var keyNumbers = Object.assign({}, tableSummary.keyNumbers || {}, {
      woodOnTableCount: tableCounts.woodOnTableCount,
      woodBagOnTableCount: tableCounts.woodBagOnTableCount,
      customerCount: customers.length,
      activeCustomerCount: customers.filter(function(c) { return c.active; }).length,
      tryBuyCount: tableCallCounters(counters).tryBuy.callCount,
      checkWoodOnTableCount: tableCallCounters(counters).customerCheckWoodOnTable.callCount
    });

    return {
      ready: true,
      actor: {
        nodePath: actorSummary && actorSummary.path,
        worldPosition: actorSummary && actorSummary.worldPosition,
        state: actorComp ? {
          isLimitMove: actorComp.isLimitMove,
          isMoving: actorComp.isMoving,
          isOnButton: actorComp.isOnButton,
          isGetOnButton: actorComp.isGetOnButton,
          isLeavingButton: actorComp.isLeavingButton
        } : {},
        inventory: {
          bagLogCount: bagCount(actorBagLog),
          bagWoodCount: bagCount(actorBagWood),
          bagItemCount: bagCount(actorBagLog) || bagCount(actorBagWood),
          possibleInventoryFields: inventoryFields
        }
      },
      table: {
        woodOnTable: woodOnTableNode ? Object.assign(tableNodeDetail(tableRefs.woodOnTableRaw, actorSummary, /WoodOnTable|WoodBag|Bag|Spot|Table|Wood/i) || {}, { bagItemCount: bagCount(tableRefs.woodOnTableComp), bagRealItemCount: safeBagRealCount(tableRefs.woodOnTableComp) }) : null,
        woodBagOnTable: woodBagNode ? Object.assign(tableNodeDetail(tableRefs.woodBagRaw, actorSummary, /WoodBag|Bag|WoodOnTable|Table|Wood/i) || {}, { bagItemCount: bagCount(tableRefs.woodBagComp), bagRealItemCount: safeBagRealCount(tableRefs.woodBagComp) }) : null,
        tableCounts: tableCounts
      },
      customerManager: managerEntry ? {
        className: managerEntry.className,
        nodePath: managerEntry.item.path,
        primitiveFields: managerFields,
        booleanFields: managerTyped.booleanFields,
        numericFields: managerTyped.numericFields,
        stringFields: managerTyped.stringFields,
        methodNames: methodNames(managerComp).filter(function(name) { return /TryBuy|tryBuy|checkWoodOnTable|buy|sell|customer|money|coin|target|worker/i.test(name); }).slice(0, 100),
        objectRefs: managerRefs,
        callCounters: tableCallCounters(counters),
        lastTryBuyArgs: latestTryBuy && latestTryBuy.lastArgs,
        lastTryBuyReturn: latestTryBuy && latestTryBuy.lastReturn,
        lastTryBuyError: latestTryBuy && latestTryBuy.lastError,
        lastCheckWoodOnTableReturn: latestCheck && latestCheck.lastReturn,
        lastCheckWoodOnTableAt: latestCheck && latestCheck.lastCalledAt
      } : null,
      selectedCustomerManager: managerEntry ? {
        className: managerEntry.className,
        nodePath: managerEntry.item.path,
        selectionReason: managerSelection.reason
      } : null,
      customerManagerCandidates: managerSelection.candidates,
      rejectedCustomerManagerCandidates: managerSelection.rejected,
      selectionReason: managerSelection.reason,
      customers: customers.slice(0, 80),
      headBubbles: headBubbles.slice(0, 50),
      workers: workers.slice(0, 20),
      moneyTargets: moneyTargets.slice(0, 20),
      guide: tableSummary.guide,
      callCounters: Object.assign({}, tableCallCounters(counters), {
        customerBuy: tableCallCounters(counters).tryBuy,
        customerArrive: latestCounter(counters, /Customer.*\.(arrive|Arrive)/i) || { callCount: 0 },
        customerLeave: latestCounter(counters, /Customer.*\.(leave|Leave)/i) || { callCount: 0 },
        headBubbleUpdate: latestCounter(counters, /HeadBubble.*\.(updateCount|setCount|show|hide)/i) || { callCount: 0 },
        moneyAdd: latestCounter(counters, /(Money|Game|MainGame).*\.(addMoney|AddMoney|money)/i) || { callCount: 0 },
        coinAdd: latestCounter(counters, /(Coin|Game|MainGame).*\.(addCoin|AddCoin|coin)/i) || { callCount: 0 },
        playCoinAdd: latestCounter(counters, /(Game|MainGame).*\.(playCoin|addPlayCoin)/i) || { callCount: 0 }
      }),
      rawCallCounters: counters,
      keyNumbers: keyNumbers
    };
  }

  function getCompletionSummaryImpl() {
    try {
      var resolved = resolveFastCache(false);
      var cache = resolved.cache;
      if (!cache) return { ready: false };
      ensureLightCallCounters(cache);
      var activeEndNodes = [];
      for (var i = 0; i < cache.rawNodes.length && activeEndNodes.length < 80; i++) {
        var compact = compactRaw(cache.rawNodes[i], true);
        if (!compact || !compact.active) continue;
        var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
        if (!/endcard|endCard|CTA|install|download|win|victory|complete|completed|success|finish|gameEnd|ui_win|GameWin|final/i.test(text)) continue;
        activeEndNodes.push({
          name: compact.name,
          path: compact.path,
          active: compact.active,
          worldPosition: compact.worldPosition,
          screenPosition: compact.screenPosition,
          components: compact.components,
          reason: /CTA|install|download/i.test(text) ? "CTA/install/download node active" : "completion-like node active"
        });
      }

      var managerFlags = [];
      var flagMap = {};
      compEntries(cache, /Game|MainGame|Manager|Guide|Level|Scene|Controller|CustomerManager/i, 140).forEach(function(entry) {
        var fields = primitiveFieldsOf(entry.component, /isGameOver|isWin|isFinish|gameWin|hasWin|isComplete|levelComplete|complete|completed|finish|success|money|coin|playCoin|score|checkout|cycle|count|state|guide|step|end|win|over/i);
        var flags = {};
        var numbers = {};
        Object.keys(fields).forEach(function(key) {
          if (typeof fields[key] === "boolean" && /isGameOver|isWin|isFinish|gameWin|hasWin|isComplete|levelComplete|complete|completed|finish|success|gameOver|win|over/i.test(key)) {
            flags[key] = fields[key];
            flagMap[entry.className + "." + key] = fields[key];
            flagMap[key] = fields[key];
          }
          if (typeof fields[key] === "number") numbers[key] = fields[key];
        });
        if (Object.keys(flags).length || Object.keys(numbers).length) {
          managerFlags.push({
            className: entry.className,
            nodePath: entry.item.path,
            flags: flags,
            numbers: numbers,
            primitiveFields: fields
          });
        }
      });

      var end = readEndState(activeEndNodes, flagMap);
      var events = (window.__playableAgentEvents || []).slice(-40);
      var customer = getCustomerBuyChainSummaryImpl();
      var table = customer && customer.table && customer.table.tableCounts || {};
      var economy = {
        money: customer && customer.keyNumbers && customer.keyNumbers.money,
        coin: customer && customer.keyNumbers && customer.keyNumbers.coin,
        playCoin: customer && customer.keyNumbers && customer.keyNumbers.playCoin,
        score: customer && customer.keyNumbers && customer.keyNumbers.score,
        checkoutCount: customer && customer.keyNumbers && customer.keyNumbers.tryBuyCount,
        checkoutDelta: undefined,
        cyclesCompleted: undefined,
        woodOnTableCount: table.woodOnTableCount,
        woodBagOnTableCount: table.woodBagOnTableCount
      };
      var guideVisual = getGuideVisualSummaryImpl();
      var candidates = [];
      activeEndNodes.forEach(function(node) {
        candidates.push({ type: "node", name: node.path, value: true, reason: node.reason, confidence: /CTA|install|download|endcard|win/i.test(node.path) ? 0.9 : 0.75 });
      });
      managerFlags.forEach(function(manager) {
        Object.keys(manager.flags || {}).forEach(function(key) {
          if (manager.flags[key]) candidates.push({ type: "managerFlag", name: manager.className + "." + key, value: true, reason: "completion-like manager flag", confidence: WIN_KEYS.test(key) ? 0.9 : 0.65 });
        });
      });
      events.forEach(function(event) {
        var eventName = safeString(event && event.name);
        if (/ENDCARD_SHOWN|COMPLETED|CHALLENGE_SOLVED|ShowEndCard|Completed/i.test(eventName)) {
          candidates.push({ type: "analytics", name: eventName, value: true, reason: "playable analytics completion event", confidence: 0.95 });
        }
      });
      ["money", "coin", "playCoin", "score"].forEach(function(key) {
        var value = economy[key];
        if (typeof value === "number" && value > 0) candidates.push({ type: "economy", name: key, value: value, reason: "economy progressed after checkout", confidence: 0.45 });
      });
      var isGameManagerWin = managerFlags.some(function(manager) {
        return Object.keys(manager.flags || {}).some(function(key) {
          return manager.flags[key] && /(isGameOver|gameOver|isWin|gameWin|hasWin|isComplete|levelComplete)$/i.test(key);
        });
      }) || Boolean(end.win);
      var isEndcardShown = activeEndNodes.some(function(node) {
        return /endcard|endCard|win|victory|success|finish|complete|GameWin|ui_win/i.test((node.name || "") + " " + (node.path || ""));
      });
      var isCtaVisible = activeEndNodes.some(function(node) {
        return /CTA|install|download/i.test((node.name || "") + " " + (node.path || ""));
      });
      var isAnalyticsCompleted = events.some(function(event) {
        var eventName = safeString(event && (event.name || event.event || event.type || event));
        return /ENDCARD_SHOWN|COMPLETED|CHALLENGE_SOLVED|ShowEndCard|Completed/i.test(eventName);
      });
      var completionKind = [];
      if (isGameManagerWin) completionKind.push("game-manager-win");
      if (isEndcardShown) completionKind.push("endcard-shown");
      if (isAnalyticsCompleted) completionKind.push("analytics-completed");
      if (isCtaVisible) completionKind.push("cta-visible");
      return {
        ready: true,
        endState: {
          done: end.done,
          win: end.win,
          reason: end.doneReason,
          signals: candidates.slice(0, 40)
        },
        completionKind: completionKind,
        isGameManagerWin: isGameManagerWin,
        isEndcardShown: isEndcardShown,
        isAnalyticsCompleted: isAnalyticsCompleted,
        isCtaVisible: isCtaVisible,
        isPlayableComplete: isGameManagerWin || isEndcardShown || isAnalyticsCompleted,
        activeEndNodes: activeEndNodes,
        managerFlags: managerFlags.slice(0, 80),
        analyticsEventsTail: events,
        playableAnalyticsState: {
          hasALPlayableAnalytics: Boolean(window.ALPlayableAnalytics),
          hasPlayableAnalytics: Boolean(window.PlayableAnalytics),
          eventCount: (window.__playableAgentEvents || []).length
        },
        guide: {
          likelyGuideTarget: guideVisual && guideVisual.likelyGuideTarget,
          guideDirection: guideVisual && guideVisual.guideDirection,
          activeGuideNodes: (guideVisual && guideVisual.activeGuideNodes || []).slice(0, 20)
        },
        economy: economy,
        completionCandidates: candidates.slice(0, 80),
        callCounters: getCallCounters()
      };
    } catch (e) {
      return { ready: false, error: safeString(e && e.message || e) };
    }
  }

  function recruitCallCounters(counters) {
    function count(regex) {
      var total = 0;
      var lastCalledAt = 0;
      Object.keys(counters || {}).forEach(function(key) {
        if (!regex.test(key)) return;
        var item = counters[key] || {};
        total += Number(item.callCount || 0);
        if (item.lastCalledAt && item.lastCalledAt > lastCalledAt) lastCalledAt = item.lastCalledAt;
      });
      return { callCount: total, lastCalledAt: lastCalledAt || undefined };
    }
    return {
      recruit: count(/recruit|hire/i),
      unlockWorker: count(/unlock.*worker|worker.*unlock|enable.*worker|active.*worker/i),
      buyDiTie: count(/DiTie.*(buy|Buy|upgrade|Upgrade)|upgrade.*DiTie/i),
      diTieFinish: count(/DiTie.*(finish|complete)|finish.*DiTie/i),
      workerActive: count(/Worker.*(active|enable|start|work)/i),
      guideUpdate: count(/Guide.*(update|next|target|guide)/i),
      guideComplete: count(/Guide.*(complete|finish)/i),
      endcardShown: count(/endcard|ShowEndCard|ENDCARD/i),
      completed: count(/Completed|COMPLETED|complete|finish/i)
    };
  }

  function getRecruitChainSummaryImpl() {
    try {
      var resolved = resolveFastCache(false);
      var cache = resolved.cache;
      if (!cache) return { ready: false };
      ensureLightCallCounters(cache);
      var actorEntry = cache.actorComponent;
      var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), true);
      var actorComp = actorEntry && actorEntry.component;
      var customer = getCustomerBuyChainSummaryImpl();
      var completion = getCompletionSummaryImpl();
      var customer = getCustomerBuyChainSummaryImpl();
      var guideVisual = getGuideVisualSummaryImpl();
      var counters = getCallCounters();
      var guidRecruitRaw = cache.keyRaw.guidRecruit || fastFindRawByPathOrName(cache.scene, "/game/GameScene/GuideTargetParent/guidRecruit") || fastFindRawByPathOrName(cache.scene, "guidRecruit");
      var guidRecruitSummary = compactRaw(guidRecruitRaw, true);
      var recruitRe = /recruit|worker|guidRecruit|recruitWorker|Worker|DiTie|upgrade|hire|employee|staff|员工|招工/i;
      var recruitTargets = [];
      function addRecruitTarget(nodeLike, reason, priority) {
        try {
          if (!nodeLike) return;
          var node = nodeLike.node ? compactRaw(nodeLike, true) : nodeLike;
          if (!node || !node.worldPosition) return;
          var path = node.path || node.nodePath;
          if (!path || recruitTargets.some(function(item) { return item.path === path; })) return;
          var entry = nodeLike.node ? compOnRaw(nodeLike, /DiTie|Spot|Worker|Guide|Collider|Trigger|CustomerManager/i) : null;
          var comp = entry && entry.component;
          recruitTargets.push({
            name: node.name || node.nodeName,
            path: path,
            worldPosition: node.worldPosition,
            active: node.active,
            components: node.components || [],
            primitiveFields: comp ? primitiveFieldsOf(comp, /price|tempPrice|cost|isFinish|finish|isEnough|enough|canBuy|isBuy|isComplete|worker|recruit|hire|unlock|active|enabled|state|guide/i) : (node.primitiveFields || {}),
            methodNames: comp ? methodNames(comp).filter(function(name) { return /recruit|worker|hire|unlock|buy|DiTie|upgrade|finish|complete|guide|active|enable|trigger/i.test(name); }).slice(0, 80) : (node.methodNames || []),
            reason: reason,
            priority: priority,
            distanceToActor: distanceToActor(actorSummary, node)
          });
        } catch (e) {}
      }
      var likely = guideVisual && guideVisual.likelyGuideTarget;
      if (likely && /recruit|worker|guidRecruit/i.test((likely.name || "") + " " + (likely.path || ""))) addRecruitTarget(likely, "guide likely target", 1);
      if (guidRecruitSummary) addRecruitTarget(guidRecruitSummary, "guidRecruit marker", 4);
      [
        "/game/env/Spot/recruitWorkerDiTie",
        "/game/env/Spot/recruitWorkerSpot",
        "recruitWorkerDiTie",
        "recruitWorkerSpot"
      ].forEach(function(pathOrName) {
        var raw = fastFindRawByPathOrName(cache.scene, pathOrName);
        if (raw) addRecruitTarget(raw, "exact recruit/worker spot", /DiTie/i.test(pathOrName) ? 1 : 2);
      });
      for (var ri = 0; ri < cache.rawNodes.length && recruitTargets.length < 60; ri++) {
        var compact = compactRaw(cache.rawNodes[ri], true);
        var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
        if (recruitRe.test(text)) {
          var pri = /recruitWorkerDiTie|worker.*DiTie|DiTie.*worker|upgrade.*worker/i.test(text) ? 1 : /workerNode|Worker/i.test(text) ? 2 : /guidRecruit/i.test(text) ? 4 : /DiTie|upgrade|Spot|button|collider/i.test(text) ? 3 : 6;
          addRecruitTarget(cache.rawNodes[ri], "worker/recruit node scan", pri);
        }
      }

      var diTieTargets = [];
      compEntries(cache, /DiTie|upgrade|Spot/i, 100).forEach(function(entry) {
        try {
          var compact = compactRaw(entry.item, true);
          var text = entry.className + " " + compact.name + " " + compact.path;
          if (!/recruit|worker|hire|employee|staff|DiTie|upgrade/i.test(text)) return;
          var fields = primitiveFieldsOf(entry.component, /price|tempPrice|cost|isFinish|finish|isEnough|enough|canBuy|isBuy|isComplete|worker|recruit|hire|unlock|active|enabled|state/i);
          diTieTargets.push({
            nodePath: compact.path,
            worldPosition: compact.worldPosition,
            active: compact.active,
            components: compact.components,
            primitiveFields: fields,
            price: pickNumber(fields, /price|cost/i),
            tempPrice: pickNumber(fields, /tempPrice/i),
            isFinish: fields.isFinish ?? fields.finish ?? fields.isComplete,
            isEnough: fields.isEnough ?? fields.enough,
            canBuy: fields.canBuy ?? fields.isBuy,
            distanceToActor: distanceToActor(actorSummary, compact)
          });
          addRecruitTarget(compact, "DiTie/upgrade target", /recruit|worker/i.test(text) ? 1 : 3);
        } catch (e) {}
      });

      var workers = [];
      function addWorker(nodeLike, comp, reason) {
        try {
          var node = nodeLike && nodeLike.node ? compactRaw(nodeLike, true) : nodeLike;
          if (!node || !node.path || workers.some(function(item) { return item.nodePath === node.path; })) return;
          workers.push({
            nodePath: node.path || node.nodePath,
            worldPosition: node.worldPosition,
            active: node.active,
            components: node.components || [],
            primitiveFields: comp ? primitiveFieldsOf(comp, /active|enabled|isWorking|isUnlock|unlock|worker|state|target|canSell|finish|complete/i) : {},
            workerNodeActive: node.active,
            enabled: comp ? componentEnabled(comp) : undefined,
            distanceToActor: distanceToActor(actorSummary, node)
          });
        } catch (e) {}
      }
      compEntries(cache, /Worker|WorkerManager|WorkerController|CustomerManager/i, 120).forEach(function(entry) {
        addWorker(entry.item, entry.component, "worker component");
      });

      var managerSelection = findComponentByClassPriority(cache, /CustomerManager/i, {
        exactClass: "CustomerManager",
        preferPath: "/game/CustomerManager",
        preferMethods: ["checkWoodOnTable", "TryBuy", "tryBuy"],
        preferRefs: ["workerNode", "woodBagOnTable", "woodOnTable"],
        requireMethod: false,
        classOnly: true,
        limit: 120
      });
      var managerEntry = managerSelection.selected || null;
      var managerComp = managerEntry && managerEntry.component;
      var managerFields = primitiveFieldsOf(managerComp, /canSell|isSell|selling|customer|customers|queue|buyer|target|workerNode|worker|money|coin|playCoin|score|woodBagOnTable|table|need|count|price|recruit|hire|unlock|active|state/i);
      var managerSummary = null;
      if (managerEntry) {
        var refs = {};
        ownKeys(managerComp).forEach(function(key) {
          try {
            if (!/worker|customer|target|table|wood|money|spot|recruit|guide/i.test(key)) return;
            var ref = summarizeAny(managerComp[key]);
            if (ref && (ref.nodePath || ref.path || ref.kind)) refs[key] = ref;
            if (ref && (ref.nodePath || ref.path) && /worker/i.test(key)) {
              var raw = fastFindRawByPathOrName(cache.scene, ref.nodePath || ref.path);
              if (raw) {
                addRecruitTarget(compactRaw(raw, true), "CustomerManager.workerNode", 2);
                addWorker(raw, compOnRaw(raw, /Worker|Customer|Actor|Controller/i)?.component, "CustomerManager.workerNode");
              }
            }
          } catch (e) {}
        });
        managerSummary = {
          className: managerEntry.className,
          nodePath: managerEntry.item.path,
          primitiveFields: managerFields,
          booleanFields: Object.keys(managerFields).reduce(function(out, key) { if (typeof managerFields[key] === "boolean") out[key] = managerFields[key]; return out; }, {}),
          numericFields: Object.keys(managerFields).reduce(function(out, key) { if (typeof managerFields[key] === "number") out[key] = managerFields[key]; return out; }, {}),
          objectRefs: refs,
          canSell: managerFields.canSell,
          workerNodeActive: workers.some(function(worker) { return worker.active; }),
          selectedReason: managerSelection.reason
        };
      }

      var workerManagers = compEntries(cache, /Worker|WorkerManager|WorkerController/i, 80).map(function(entry) {
        var fields = primitiveFieldsOf(entry.component, /active|enabled|isWorking|isUnlock|unlock|worker|state|target|count|price|finish|complete|guide/i);
        return {
          className: entry.className,
          nodePath: entry.item.path,
          primitiveFields: fields,
          booleanFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "boolean") out[key] = fields[key]; return out; }, {}),
          numericFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "number") out[key] = fields[key]; return out; }, {}),
          methodNames: methodNames(entry.component).filter(function(name) { return /worker|recruit|hire|unlock|active|enable|work|target|guide|finish|complete/i.test(name); }).slice(0, 80)
        };
      });

      recruitTargets.sort(function(a, b) {
        return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999));
      });
      diTieTargets.sort(function(a, b) {
        return ((a.distanceToActor || 999) - (b.distanceToActor || 999));
      });
      var economy = Object.assign({}, completion && completion.economy || {}, customer && customer.keyNumbers || {});
      return {
        ready: true,
        actor: {
          nodePath: actorSummary && actorSummary.path,
          worldPosition: actorSummary && actorSummary.worldPosition,
          state: actorComp ? {
            isLimitMove: actorComp.isLimitMove,
            isMoving: actorComp.isMoving,
            isOnButton: actorComp.isOnButton,
            isGetOnButton: actorComp.isGetOnButton,
            isLeavingButton: actorComp.isLeavingButton
          } : {}
        },
        economy: economy,
        guide: {
          likelyGuideTarget: likely,
          guideDirection: guideVisual && guideVisual.guideDirection,
          activeGuideNodes: (guideVisual && guideVisual.activeGuideNodes || []).slice(0, 30),
          blueIndicators: (guideVisual && guideVisual.blueIndicators || []).slice(0, 10),
          isGuidRecruitActive: Boolean(guidRecruitSummary && guidRecruitSummary.active),
          guidRecruitSummary: guidRecruitSummary
        },
        recruitTargets: recruitTargets.slice(0, 40),
        diTieTargets: diTieTargets.slice(0, 40),
        workers: workers.slice(0, 40),
        customerManager: managerSummary,
        workerManagers: workerManagers.slice(0, 40),
        callCounters: recruitCallCounters(counters),
        completion: completion ? {
          endState: completion.endState,
          activeEndNodes: completion.activeEndNodes,
          managerFlags: completion.managerFlags,
          analyticsEventsTail: completion.analyticsEventsTail
        } : null
      };
    } catch (e) {
      return { ready: false, error: safeString(e && e.message || e) };
    }
  }

  function postRecruitCounters(counters) {
    var calls = counters && counters.calls || {};
    function count(regex) {
      var total = 0;
      Object.keys(calls).forEach(function(key) {
        if (regex.test(key)) total += Number(calls[key] && calls[key].callCount || 0);
      });
      return total;
    }
    return {
      guideUpdate: count(/Guide.*(update|next|target|guide)/i),
      guideComplete: count(/Guide.*(complete|finish)/i),
      buyDiTie: count(/DiTie.*(buy|trigger|updateTempPrice|finish|complete)/i),
      diTieFinish: count(/DiTie.*(finish|complete|checkFinish)/i),
      unlockLaser2: count(/laser2|Laser2|upgradeLaser/i),
      unlockConveyor: count(/conveyor|Conveyor|belt/i),
      conveyorStart: count(/Conveyor.*(start|run|move|active|enable)/i),
      conveyorActive: count(/Conveyor.*(active|enable|start|run)/i),
      endcardShown: count(/endcard|ShowEndCard|ENDCARD/i),
      completed: count(/Completed|COMPLETED|complete|finish/i)
    };
  }

  function getPostRecruitChainSummaryImpl() {
    try {
      var resolved = resolveFastCache(false);
      var cache = resolved.cache;
      if (!cache) return { ready: false };
      ensureLightCallCounters(cache);
      var actorEntry = cache.actorComponent;
      var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), true);
      var actorComp = actorEntry && actorEntry.component;
      var completion = getCompletionSummaryImpl();
      var customer = getCustomerBuyChainSummaryImpl();
      var guideVisual = getGuideVisualSummaryImpl();
      var counters = getCallCounters();
      var likely = guideVisual && guideVisual.likelyGuideTarget;
      var laser2GuideRaw = cache.keyRaw.laser2 || fastFindRawByPathOrName(cache.scene, "/game/GameScene/GuideTargetParent/laser2") || fastFindRawByPathOrName(cache.scene, "laser2");
      var conveyorGuideRaw = cache.keyRaw.guidConveyor1 || fastFindRawByPathOrName(cache.scene, "/game/GameScene/GuideTargetParent/guidConveyor1") || fastFindRawByPathOrName(cache.scene, "guidConveyor1");
      var laser2GuideSummary = compactRaw(laser2GuideRaw, true);
      var conveyorGuideSummary = compactRaw(conveyorGuideRaw, true);
      var laser2Targets = [];
      var conveyorTargets = [];
      var diTieTargets = [];
      function addTarget(kind, nodeLike, reason, priority) {
        try {
          if (!nodeLike) return;
          var node = nodeLike.node ? compactRaw(nodeLike, true) : nodeLike;
          if (!node || !node.worldPosition) return;
          var path = node.path || node.nodePath;
          if (!path) return;
          var arr = kind === "conveyor" ? conveyorTargets : laser2Targets;
          if (arr.some(function(item) { return item.path === path; })) return;
          var entry = nodeLike.node ? compOnRaw(nodeLike, /DiTie|Spot|Laser|Conveyor|Guide|Collider|Trigger|Machine|Button/i) : null;
          var comp = entry && entry.component;
          arr.push({
            name: node.name || node.nodeName,
            path: path,
            worldPosition: node.worldPosition,
            active: node.active,
            components: node.components || [],
            primitiveFields: comp ? primitiveFieldsOf(comp, /price|tempPrice|cost|isFinish|finish|isEnough|enough|canBuy|isBuy|isComplete|laser|conveyor|belt|active|enabled|state|guide|speed|work|run|open|unlock/i) : (node.primitiveFields || {}),
            methodNames: comp ? methodNames(comp).filter(function(name) { return /laser|conveyor|belt|unlock|buy|DiTie|upgrade|finish|complete|guide|active|enable|trigger|start|run|move/i.test(name); }).slice(0, 80) : (node.methodNames || []),
            reason: reason,
            priority: priority,
            distanceToActor: distanceToActor(actorSummary, node)
          });
        } catch (e) {}
      }
      function addDiTie(entry, reason) {
        try {
          var compact = compactRaw(entry.item, true);
          var fields = primitiveFieldsOf(entry.component, /price|tempPrice|cost|isFinish|finish|isEnough|enough|canBuy|isBuy|isComplete|laser|conveyor|belt|worker|unlock|active|enabled|state/i);
          var text = entry.className + " " + compact.name + " " + compact.path + " " + compact.components.join(" ");
          var item = {
            nodePath: compact.path,
            worldPosition: compact.worldPosition,
            active: compact.active,
            components: compact.components,
            primitiveFields: fields,
            price: pickNumber(fields, /price|cost/i),
            tempPrice: pickNumber(fields, /tempPrice/i),
            isFinish: fields.isFinish ?? fields.finish ?? fields.isComplete,
            isEnough: fields.isEnough ?? fields.enough,
            canBuy: fields.canBuy ?? fields.isBuy,
            distanceToActor: distanceToActor(actorSummary, compact),
            reason: reason
          };
          if (!diTieTargets.some(function(existing) { return existing.nodePath === item.nodePath; })) diTieTargets.push(item);
          if (/laser2|upgradeLaserDiTie2|Laser2|laser/i.test(text)) addTarget("laser2", compact, reason || "laser2 DiTie", /upgradeLaserDiTie2/i.test(text) ? 1 : 2);
          if (/conveyor|guidConveyor|belt|transport|line|machine|传送|输送/i.test(text)) addTarget("conveyor", compact, reason || "conveyor DiTie", /conveyor1DiTie/i.test(text) ? 1 : 2);
        } catch (e) {}
      }
      if (likely && /laser2|Laser2|upgradeLaser/i.test((likely.name || "") + " " + (likely.path || ""))) addTarget("laser2", likely, "guide likely target", 1);
      if (likely && /conveyor|guidConveyor|belt|transport|line/i.test((likely.name || "") + " " + (likely.path || ""))) addTarget("conveyor", likely, "guide likely target", 1);
      if (laser2GuideSummary) addTarget("laser2", laser2GuideSummary, "laser2 guide marker", 4);
      if (conveyorGuideSummary) addTarget("conveyor", conveyorGuideSummary, "conveyor guide marker", 4);
      [
        ["/game/env/Spot/upgradeLaserDiTie2", "laser2", 1],
        ["upgradeLaserDiTie2", "laser2", 1],
        ["/game/env/Spot/conveyor1DiTie", "conveyor", 1],
        ["conveyor1DiTie", "conveyor", 1],
        ["/game/env/Spot/conveyor2DiTie", "conveyor", 2],
        ["conveyor2DiTie", "conveyor", 2]
      ].forEach(function(spec) {
        var raw = fastFindRawByPathOrName(cache.scene, spec[0]);
        if (raw) addTarget(spec[1], raw, "exact post-recruit spot", spec[2]);
      });
      for (var i = 0; i < cache.rawNodes.length && (laser2Targets.length + conveyorTargets.length) < 100; i++) {
        var compact = compactRaw(cache.rawNodes[i], true);
        var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
        if (/laser2|guidLaser2|Laser2|upgradeLaser|laser/i.test(text)) addTarget("laser2", cache.rawNodes[i], "laser2 node scan", /upgradeLaserDiTie2/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : 5);
        if (/conveyor|guidConveyor|Conveyor|belt|transport|machine|line|传送|输送/i.test(text)) addTarget("conveyor", cache.rawNodes[i], "conveyor node scan", /conveyor1DiTie/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : 5);
      }
      compEntries(cache, /DiTie|upgrade|Spot|Laser|Conveyor|Machine/i, 180).forEach(function(entry) { addDiTie(entry, "DiTie/upgrade target"); });
      var workers = [];
      compEntries(cache, /Worker|WorkerManager|WorkerController|CustomerManager/i, 120).forEach(function(entry) {
        var compact = compactRaw(entry.item, true);
        var fields = primitiveFieldsOf(entry.component, /active|enabled|isWorking|isUnlock|unlock|worker|state|target|canSell|finish|complete/i);
        workers.push({ nodePath: compact.path, worldPosition: compact.worldPosition, active: compact.active, components: compact.components, primitiveFields: fields, workerNodeActive: compact.active, enabled: componentEnabled(entry.component), distanceToActor: distanceToActor(actorSummary, compact) });
      });
      var conveyors = compEntries(cache, /Conveyor|ConveyorManager|ConveyorLine|Belt|Transport/i, 120).map(function(entry) {
        var compact = compactRaw(entry.item, true);
        var fields = primitiveFieldsOf(entry.component, /active|isOpen|isWorking|isRunning|speed|move|transport|output|input|belt|finish|enabled|state|count|item/i);
        return { className: entry.className, nodePath: compact.path, worldPosition: compact.worldPosition, active: compact.active, components: compact.components, primitiveFields: fields, booleanFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "boolean") out[key] = fields[key]; return out; }, {}), numericFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "number") out[key] = fields[key]; return out; }, {}), methodNames: methodNames(entry.component).filter(function(name) { return /conveyor|belt|start|run|move|active|enable|finish|complete|guide/i.test(name); }).slice(0, 80), distanceToActor: distanceToActor(actorSummary, compact) };
      });
      var laserControllers = compEntries(cache, /Laser|LaserController|LaserButton|MassiveLog/i, 120).map(function(entry) {
        var compact = compactRaw(entry.item, true);
        var fields = primitiveFieldsOf(entry.component, /active|enabled|laser|work|working|running|finish|complete|state|target|button|progress/i);
        return { className: entry.className, nodePath: compact.path, worldPosition: compact.worldPosition, active: compact.active, components: compact.components, primitiveFields: fields, booleanFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "boolean") out[key] = fields[key]; return out; }, {}), numericFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "number") out[key] = fields[key]; return out; }, {}), methodNames: methodNames(entry.component).filter(function(name) { return /laser|button|start|run|work|active|enable|finish|complete|guide/i.test(name); }).slice(0, 80), distanceToActor: distanceToActor(actorSummary, compact) };
      });
      var managerEntry = (compEntries(cache, /CustomerManager/i, 5)[0] || {});
      var managerComp = managerEntry.component;
      var managerFields = primitiveFieldsOf(managerComp, /canSell|workerNode|worker|money|coin|playCoin|score|customer|state|active/i);
      laser2Targets.sort(function(a, b) { return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999)); });
      conveyorTargets.sort(function(a, b) { return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999)); });
      diTieTargets.sort(function(a, b) { return ((a.distanceToActor || 999) - (b.distanceToActor || 999)); });
      var economy = Object.assign({}, completion && completion.economy || {}, customer && customer.keyNumbers || {});
      return {
        ready: true,
        actor: { nodePath: actorSummary && actorSummary.path, worldPosition: actorSummary && actorSummary.worldPosition, state: actorComp ? { isLimitMove: actorComp.isLimitMove, isMoving: actorComp.isMoving, isOnButton: actorComp.isOnButton, isGetOnButton: actorComp.isGetOnButton, isLeavingButton: actorComp.isLeavingButton } : {} },
        economy: economy,
        guide: { likelyGuideTarget: likely, guideDirection: guideVisual && guideVisual.guideDirection, activeGuideNodes: (guideVisual && guideVisual.activeGuideNodes || []).slice(0, 30), blueIndicators: (guideVisual && guideVisual.blueIndicators || []).slice(0, 10), isLaser2GuideActive: Boolean(laser2GuideSummary && laser2GuideSummary.active), isConveyorGuideActive: Boolean(conveyorGuideSummary && conveyorGuideSummary.active), laser2GuideSummary: laser2GuideSummary, conveyorGuideSummary: conveyorGuideSummary },
        laser2Targets: laser2Targets.slice(0, 40),
        conveyorTargets: conveyorTargets.slice(0, 40),
        diTieTargets: diTieTargets.slice(0, 80),
        workers: workers.slice(0, 40),
        conveyors: conveyors.slice(0, 40),
        laserControllers: laserControllers.slice(0, 40),
        customerManager: managerComp ? { className: className(managerComp), nodePath: managerEntry.item && managerEntry.item.path, primitiveFields: managerFields, canSell: managerFields.canSell, workerNodeActive: workers.some(function(worker) { return worker.active; }) } : null,
        callCounters: postRecruitCounters(counters),
        completion: completion ? { endState: completion.endState, activeEndNodes: completion.activeEndNodes, managerFlags: completion.managerFlags, analyticsEventsTail: completion.analyticsEventsTail } : null
      };
    } catch (e) {
      return { ready: false, error: safeString(e && e.message || e) };
    }
  }

  function finalKnifeCounters(counters) {
    var calls = counters && counters.calls || {};
    function count(regex) {
      var total = 0;
      var lastCalledAt = 0;
      Object.keys(calls).forEach(function(key) {
        if (!regex.test(key)) return;
        var item = calls[key] || {};
        total += Number(item.callCount || 0);
        if (item.lastCalledAt && item.lastCalledAt > lastCalledAt) lastCalledAt = item.lastCalledAt;
      });
      return { callCount: total, lastCalledAt: lastCalledAt || undefined };
    }
    return {
      actorOnTriggerEnter: count(/Actor.*onTriggerEnter/i),
      upgradeKnifeDiTieEnter: count(/upgradeKnifeDiTie|Knife.*DiTie|DiTie.*Knife/i),
      progress100: count(/Progress100/i),
      showEndCard: count(/ShowEndCard|endcard/i),
      mainGameGameOver: count(/MainGame.*gameOver|gameOver/i),
      playableCompleted: count(/Completed|COMPLETED|CHALLENGE_SOLVED/i),
      endcardShown: count(/ENDCARD_SHOWN|ShowEndCard|endcard/i),
      ctaShown: count(/CTA/i)
    };
  }

  function getPostConveyorChainSummaryImpl() {
    try {
      var resolved = resolveFastCache(false);
      var cache = resolved.cache;
      if (!cache) return { ready: false };
      ensureLightCallCounters(cache);
      var actorEntry = cache.actorComponent;
      var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), true);
      var actorComp = actorEntry && actorEntry.component;
      var completion = getCompletionSummaryImpl();
      var guideVisual = getGuideVisualSummaryImpl();
      var postRecruit = getPostRecruitChainSummaryImpl();
      var finalKnife = getFinalKnifeChainSummaryImpl();
      var likely = guideVisual && guideVisual.likelyGuideTarget;
      var conveyor1GuideRaw = cache.keyRaw.guidConveyor1 || fastFindRawByPathOrName(cache.scene, "/game/GameScene/GuideTargetParent/guidConveyor1") || fastFindRawByPathOrName(cache.scene, "guidConveyor1");
      var conveyor2GuideRaw = cache.keyRaw.guidConveyor2 || fastFindRawByPathOrName(cache.scene, "/game/GameScene/GuideTargetParent/guidConveyor2") || fastFindRawByPathOrName(cache.scene, "guidConveyor2");
      var knifeGuideRaw = cache.keyRaw.guidKnife || cache.keyRaw.knife || fastFindRawByPathOrName(cache.scene, "/game/GameScene/GuideTargetParent/guidUpgradeKnife") || fastFindRawByPathOrName(cache.scene, "guidUpgradeKnife") || fastFindRawByPathOrName(cache.scene, "knife");
      var conveyor1GuideSummary = compactRaw(conveyor1GuideRaw, true);
      var conveyor2GuideSummary = compactRaw(conveyor2GuideRaw, true);
      var knifeGuideSummary = compactRaw(knifeGuideRaw, true);
      var conveyor1Targets = [];
      var conveyor2Targets = [];
      var conveyorTargets = [];
      var diTieTargets = [];
      var conveyors = [];
      var seen = {};
      function addTarget(list, item, reason, priority) {
        var compact = item && item.worldPosition ? item : compactRaw(item, true);
        if (!compact || !compact.worldPosition) return;
        var key = compact.path || compact.nodePath || compact.name;
        if (!key) return;
        var target = {
          name: compact.name || (compact.nodePath || "").split("/").pop(),
          path: compact.path || compact.nodePath,
          worldPosition: compact.worldPosition,
          active: compact.active,
          components: compact.components || [],
          primitiveFields: compact.primitiveFields || {},
          methodNames: compact.methodNames || [],
          reason: reason,
          priority: priority,
          distanceToActor: distanceToActor(actorSummary, compact)
        };
        var bucketKey = list === conveyor1Targets ? "c1:" + key : list === conveyor2Targets ? "c2:" + key : "c:" + key;
        if (seen[bucketKey]) return;
        seen[bucketKey] = true;
        list.push(target);
        if (list !== conveyorTargets && !seen["c:" + key]) {
          seen["c:" + key] = true;
          conveyorTargets.push(target);
        }
      }
      function addDiTie(rawOrItem, reason) {
        var compact = rawOrItem && rawOrItem.worldPosition ? rawOrItem : compactRaw(rawOrItem, true);
        if (!compact || !compact.worldPosition) return;
        var comp = rawOrItem && rawOrItem.component;
        if (!comp && rawOrItem && rawOrItem.raw && rawOrItem.raw.components) comp = rawOrItem.raw.components[0];
        var fields = rawOrItem.primitiveFields || primitiveFieldsOf(comp, /price|tempPrice|cost|isFinish|finish|isEnough|enough|canBuy|isBuy|isComplete|active|conveyor|knife|laser/i);
        var item = {
          nodePath: compact.path || compact.nodePath,
          worldPosition: compact.worldPosition,
          active: compact.active,
          components: compact.components || [],
          primitiveFields: fields || {},
          price: fields && (fields.price ?? fields.cost),
          tempPrice: fields && fields.tempPrice,
          isFinish: fields && (fields.isFinish ?? fields.finish ?? fields.isComplete),
          isEnough: fields && (fields.isEnough ?? fields.enough),
          canBuy: fields && (fields.canBuy ?? fields.isBuy),
          distanceToActor: distanceToActor(actorSummary, compact),
          reason: reason
        };
        if (!diTieTargets.some(function(existing) { return existing.nodePath === item.nodePath; })) diTieTargets.push(item);
        var text = (item.nodePath || "") + " " + (compact.name || "");
        if (/conveyor1DiTie|guidConveyor1|conveyor1/i.test(text)) addTarget(conveyor1Targets, compact, reason || "conveyor1 DiTie", /conveyor1DiTie/i.test(text) ? 1 : 3);
        if (/conveyor2DiTie|guidConveyor2|conveyor2|chuansongdai/i.test(text)) addTarget(conveyor2Targets, compact, reason || "conveyor2 DiTie", /conveyor2DiTie/i.test(text) ? 1 : 3);
      }
      (postRecruit.diTieTargets || []).forEach(function(item) { addDiTie(item, item.reason || "post-recruit DiTie"); });
      (finalKnife.diTieTargets || []).forEach(function(item) { addDiTie(item, item.reason || "final DiTie"); });
      [
        ["/game/env/Spot/conveyor1DiTie", conveyor1Targets, "exact conveyor1DiTie", 1],
        ["/game/env/Spot/conveyor2DiTie", conveyor2Targets, "exact conveyor2DiTie", 1],
        ["/game/env/conveyor2/colliders/Node-002", conveyor2Targets, "exact conveyor2 collider", 2],
        ["/game/env/conveyor2/colliders", conveyor2Targets, "exact conveyor2 colliders", 2],
        ["/game/env/conveyor2/chuansongdai_low-002/chuansongdai", conveyor2Targets, "exact conveyor2 belt", 3]
      ].forEach(function(spec) {
        var raw = fastFindRawByPathOrName(cache.scene, spec[0]);
        if (raw) addTarget(spec[1], raw, spec[2], spec[3]);
      });
      if (likely) {
        var guideText = (likely.name || "") + " " + (likely.path || "");
        if (/guidConveyor1|conveyor1/i.test(guideText)) addTarget(conveyor1Targets, likely, "guide likely conveyor1 target", 1);
        if (/guidConveyor2|conveyor2/i.test(guideText)) addTarget(conveyor2Targets, likely, "guide likely conveyor2 target", 1);
      }
      if (conveyor1GuideSummary) addTarget(conveyor1Targets, conveyor1GuideSummary, "conveyor1 guide marker", 4);
      if (conveyor2GuideSummary) addTarget(conveyor2Targets, conveyor2GuideSummary, "conveyor2 guide marker", 4);
      for (var i = 0; i < cache.rawNodes.length && conveyorTargets.length < 120; i++) {
        var compact = compactRaw(cache.rawNodes[i], true);
        if (!compact) continue;
        var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
        if (/conveyor1|guidConveyor1/i.test(text)) addTarget(conveyor1Targets, compact, "conveyor1 node scan", /conveyor1DiTie/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : 5);
        if (/conveyor2|guidConveyor2|Conveyor2|chuansongdai|belt|传送|输送/i.test(text)) addTarget(conveyor2Targets, compact, "conveyor2 node scan", /conveyor2DiTie/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : 5);
        if (/conveyor|guidConveyor|Conveyor|belt|chuansongdai|transport|line|传送|输送/i.test(text)) addTarget(conveyorTargets, compact, "conveyor node scan", /DiTie|Spot|button|collider/i.test(text) ? 3 : 6);
      }
      compEntries(cache, /Conveyor|ConveyorManager|ConveyorLine|Belt|Transport/i, 160).forEach(function(entry) {
        var compact = compactRaw(entry.item, true);
        if (!compact) return;
        var fields = primitiveFieldsOf(entry.component, /active|isOpen|isWorking|isRunning|speed|move|transport|output|input|belt|finish|complete|count|enabled/i);
        conveyors.push({
          className: entry.className,
          nodePath: compact.path,
          worldPosition: compact.worldPosition,
          active: compact.active,
          components: compact.components,
          primitiveFields: fields,
          booleanFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "boolean") out[key] = fields[key]; return out; }, {}),
          numericFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "number") out[key] = fields[key]; return out; }, {}),
          methodNames: methodNames(entry.component).filter(function(name) { return /conveyor|belt|start|run|move|active|enable|finish|complete|guide/i.test(name); }).slice(0, 80),
          distanceToActor: distanceToActor(actorSummary, compact),
          reason: /conveyor2/i.test(compact.path || compact.name || "") ? "conveyor2 component" : /conveyor1/i.test(compact.path || compact.name || "") ? "conveyor1 component" : "conveyor component"
        });
      });
      function byPriority(a, b) { return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999)); }
      conveyor1Targets.sort(byPriority);
      conveyor2Targets.sort(byPriority);
      conveyorTargets.sort(byPriority);
      diTieTargets.sort(function(a, b) { return (/conveyor2DiTie/i.test(a.nodePath || "") ? -2 : /conveyor2DiTie/i.test(b.nodePath || "") ? 2 : /conveyor1DiTie/i.test(a.nodePath || "") ? -1 : /conveyor1DiTie/i.test(b.nodePath || "") ? 1 : 0) || ((a.distanceToActor || 999) - (b.distanceToActor || 999)); });
      var knifeDiTie = (finalKnife.diTieTargets || []).find(function(item) { return /upgradeKnifeDiTie/i.test(item.nodePath || ""); }) || null;
      var economy = Object.assign({}, postRecruit.economy || {}, finalKnife.economy || {}, completion && completion.economy || {});
      var conveyor2DiTie = diTieTargets.find(function(item) { return /\/game\/env\/Spot\/conveyor2DiTie$|conveyor2DiTie/i.test(item.nodePath || ""); }) || null;
      var conveyor2Collider = conveyor2Targets.find(function(item) { return /\/game\/env\/conveyor2\/colliders(\/Node-002)?$/i.test(item.path || ""); }) || null;
      var guideText = (likely && ((likely.name || "") + " " + (likely.path || ""))) || "";
      var rawGuideTargets = (guideVisual && guideVisual.activeGuideNodes || []).slice(0, 20).map(function(item) { return { name: item.name, path: item.path, active: item.active, worldPosition: item.worldPosition }; });
      var conveyor2Price = Number((conveyor2DiTie && (conveyor2DiTie.tempPrice ?? conveyor2DiTie.price)) || 0);
      var knifePrice = Number((knifeDiTie && (knifeDiTie.tempPrice ?? knifeDiTie.price)) || 0);
      var coin = Number(economy.coin || 0);
      var playCoin = Number(economy.playCoin || 0);
      var currencyMax = Math.max(coin, playCoin);
      return {
        ready: true,
        actor: { nodePath: actorSummary && actorSummary.path, worldPosition: actorSummary && actorSummary.worldPosition, state: actorComp ? { isLimitMove: actorComp.isLimitMove, isMoving: actorComp.isMoving, isOnButton: actorComp.isOnButton, isGetOnButton: actorComp.isGetOnButton, isLeavingButton: actorComp.isLeavingButton } : {} },
        economy: economy,
        guide: { likelyGuideTarget: likely, guideDirection: guideVisual && guideVisual.guideDirection, activeGuideNodes: (guideVisual && guideVisual.activeGuideNodes || []).slice(0, 30), blueIndicators: (guideVisual && guideVisual.blueIndicators || []).slice(0, 12), isConveyor1GuideActive: Boolean(conveyor1GuideSummary && conveyor1GuideSummary.active), isConveyor2GuideActive: Boolean(conveyor2GuideSummary && conveyor2GuideSummary.active), isKnifeGuideActive: Boolean(knifeGuideSummary && knifeGuideSummary.active), conveyor1GuideSummary: conveyor1GuideSummary, conveyor2GuideSummary: conveyor2GuideSummary, knifeGuideSummary: knifeGuideSummary },
        guideState: { likelyGuideTarget: likely, isGuidConveyor2: /guidConveyor2|conveyor2/i.test(guideText), isGuidUpgradeKnife: /guidUpgradeKnife|upgradeKnife|knife/i.test(guideText), isFinalGuide: /final|end|win|complete|knife|upgradeKnife/i.test(guideText), rawGuideTargets: rawGuideTargets },
        targetDistances: {
          conveyor2DiTie: conveyor2DiTie && conveyor2DiTie.distanceToActor,
          conveyor2Collider: conveyor2Collider && conveyor2Collider.distanceToActor,
          upgradeKnifeDiTie: knifeDiTie && knifeDiTie.distanceToActor,
          guidConveyor2: conveyor2GuideSummary && distanceToActor(actorSummary, conveyor2GuideSummary),
          guidUpgradeKnife: knifeGuideSummary && distanceToActor(actorSummary, knifeGuideSummary)
        },
        currency: { coin: coin, playCoin: playCoin, canAffordConveyor2: !conveyor2Price || currencyMax >= conveyor2Price, canAffordKnife: !knifePrice || currencyMax >= knifePrice },
        conveyor2: conveyor2DiTie ? { diTiePath: conveyor2DiTie.nodePath, distanceToActor: conveyor2DiTie.distanceToActor, price: conveyor2DiTie.price, tempPrice: conveyor2DiTie.tempPrice, isFinish: conveyor2DiTie.isFinish, canBuy: conveyor2DiTie.canBuy, active: conveyor2DiTie.active, selectedTargetReason: conveyor2DiTie.reason } : null,
        conveyor1Targets: conveyor1Targets.slice(0, 40),
        conveyor2Targets: conveyor2Targets.slice(0, 40),
        conveyorTargets: conveyorTargets.slice(0, 80),
        diTieTargets: diTieTargets.slice(0, 100),
        conveyors: conveyors.slice(0, 60),
        knifeTargets: (finalKnife.knifeTargets || []).slice(0, 40),
        finalKnife: { upgradeKnifeDiTie: knifeDiTie, knifeTargets: (finalKnife.knifeTargets || []).slice(0, 20), price: knifeDiTie && knifeDiTie.price, tempPrice: knifeDiTie && knifeDiTie.tempPrice, isFinish: knifeDiTie && knifeDiTie.isFinish, distanceToActor: knifeDiTie && knifeDiTie.distanceToActor },
        completion: finalKnife.completion || (completion ? { endState: completion.endState, activeEndNodes: completion.activeEndNodes, managerFlags: completion.managerFlags, analyticsEventsTail: completion.analyticsEventsTail } : null)
      };
    } catch (e) {
      return { ready: false, error: safeString(e && e.message || e) };
    }
  }

  function getFinalKnifeChainSummaryImpl() {
    try {
      var resolved = resolveFastCache(false);
      var cache = resolved.cache;
      if (!cache) return { ready: false };
      ensureLightCallCounters(cache);
      var actorEntry = cache.actorComponent;
      var actorSummary = compactRaw(cache.keyRaw.Actor || (actorEntry && actorEntry.item), true);
      var actorComp = actorEntry && actorEntry.component;
      var completion = getCompletionSummaryImpl();
      var guideVisual = getGuideVisualSummaryImpl();
      var customer = getCustomerBuyChainSummaryImpl();
      var counters = getCallCounters();
      var likely = guideVisual && guideVisual.likelyGuideTarget;
      var knifeGuideRaw = cache.keyRaw.guidKnife || cache.keyRaw.knife || fastFindRawByPathOrName(cache.scene, "/game/GameScene/GuideTargetParent/guidKnife") || fastFindRawByPathOrName(cache.scene, "guidKnife") || fastFindRawByPathOrName(cache.scene, "knife");
      var knifeGuideSummary = compactRaw(knifeGuideRaw, true);
      var knifeTargets = [];
      var diTieTargets = [];
      var finalTriggers = [];

      function addKnifeTarget(nodeLike, reason, priority) {
        try {
          if (!nodeLike) return;
          var node = nodeLike.node ? compactRaw(nodeLike, true) : nodeLike;
          if (!node || !node.worldPosition) return;
          var path = node.path || node.nodePath;
          if (!path || knifeTargets.some(function(item) { return item.path === path; })) return;
          var entry = nodeLike.node ? compOnRaw(nodeLike, /DiTie|Spot|Knife|Cutter|Guide|Collider|Trigger|Button|Upgrade/i) : null;
          var comp = entry && entry.component;
          knifeTargets.push({
            name: node.name || node.nodeName,
            path: path,
            worldPosition: node.worldPosition,
            active: node.active,
            components: node.components || [],
            primitiveFields: comp ? primitiveFieldsOf(comp, /price|tempPrice|cost|isFinish|finish|isEnough|enough|canBuy|isBuy|isComplete|knife|cut|cutter|blade|final|end|active|enabled|state|guide/i) : (node.primitiveFields || {}),
            methodNames: comp ? methodNames(comp).filter(function(name) { return /knife|cut|cutter|blade|unlock|buy|DiTie|upgrade|finish|complete|guide|active|enable|trigger|Progress100|ShowEndCard|gameOver/i.test(name); }).slice(0, 80) : (node.methodNames || []),
            reason: reason,
            priority: priority,
            distanceToActor: distanceToActor(actorSummary, node)
          });
        } catch (e) {}
      }
      function addDiTie(entry, reason) {
        try {
          var compact = compactRaw(entry.item, true);
          var fields = primitiveFieldsOf(entry.component, /price|tempPrice|cost|isFinish|finish|isEnough|enough|canBuy|isBuy|isComplete|knife|cut|cutter|blade|final|end|laser|conveyor|unlock|active|enabled|state/i);
          var text = entry.className + " " + compact.name + " " + compact.path + " " + compact.components.join(" ");
          var item = {
            nodePath: compact.path,
            worldPosition: compact.worldPosition,
            active: compact.active,
            components: compact.components,
            primitiveFields: fields,
            price: pickNumber(fields, /price|cost/i),
            tempPrice: pickNumber(fields, /tempPrice/i),
            isFinish: fields.isFinish ?? fields.finish ?? fields.isComplete,
            isEnough: fields.isEnough ?? fields.enough,
            canBuy: fields.canBuy ?? fields.isBuy,
            distanceToActor: distanceToActor(actorSummary, compact),
            reason: reason
          };
          if (!diTieTargets.some(function(existing) { return existing.nodePath === item.nodePath; })) diTieTargets.push(item);
          if (/upgradeKnifeDiTie|knife|Knife|刀|cut|cutter|blade|final|end/i.test(text)) addKnifeTarget(compact, reason || "knife/final DiTie", /upgradeKnifeDiTie/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : 5);
        } catch (e) {}
      }
      if (likely && /knife|Knife|刀|cut|cutter|blade|final|end|upgrade/i.test((likely.name || "") + " " + (likely.path || ""))) addKnifeTarget(likely, "guide likely target", 1);
      if (knifeGuideSummary) addKnifeTarget(knifeGuideSummary, "knife guide marker", 4);
      ["/game/env/Spot/upgradeKnifeDiTie", "upgradeKnifeDiTie", "knife", "Knife"].forEach(function(pathOrName) {
        var raw = fastFindRawByPathOrName(cache.scene, pathOrName);
        if (raw) addKnifeTarget(raw, "exact knife/final target", /upgradeKnifeDiTie/i.test(pathOrName) ? 1 : 3);
      });
      for (var i = 0; i < cache.rawNodes.length && knifeTargets.length < 80; i++) {
        var compact = compactRaw(cache.rawNodes[i], true);
        var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
        if (/knife|Knife|刀|cut|cutter|blade|final|end|upgradeKnife/i.test(text)) addKnifeTarget(cache.rawNodes[i], "knife/final node scan", /upgradeKnifeDiTie/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : 5);
      }
      compEntries(cache, /DiTie|upgrade|Spot|Knife|Cutter|MainGame|GameManager|EndCard|CTA|UI/i, 220).forEach(function(entry) { addDiTie(entry, "DiTie/upgrade/final target"); });

      compEntries(cache, /Actor|MainGame|GameManager|EndCard|CTA|Guide|DiTie|Knife|Cutter|UI/i, 180).forEach(function(entry) {
        try {
          var compact = compactRaw(entry.item, true);
          var methods = methodNames(entry.component);
          var text = entry.className + " " + compact.name + " " + compact.path + " " + methods.join(" ");
          if (!/Progress100|ShowEndCard|gameOver|Completed|endcard|CTA|win|complete|finish|final|knife|upgradeKnife/i.test(text)) return;
          var fields = primitiveFieldsOf(entry.component, /isGameOver|isWin|win|complete|finish|success|progress|score|end|cta|active|enabled|price|tempPrice|isFinish/i);
          finalTriggers.push({
            className: entry.className,
            nodePath: compact.path,
            worldPosition: compact.worldPosition,
            active: compact.active,
            components: compact.components,
            primitiveFields: fields,
            booleanFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "boolean") out[key] = fields[key]; return out; }, {}),
            numericFields: Object.keys(fields).reduce(function(out, key) { if (typeof fields[key] === "number") out[key] = fields[key]; return out; }, {}),
            methodNames: methods.filter(function(name) { return /Progress100|ShowEndCard|gameOver|Completed|endcard|CTA|win|complete|finish|final|knife|trigger/i.test(name); }).slice(0, 80),
            distanceToActor: distanceToActor(actorSummary, compact),
            reason: "method/source-name final completion hint"
          });
        } catch (e) {}
      });
      var mainGameEntry = findComponentByClassPriority(cache, /MainGame|GameManager/i, {
        exactClass: "MainGame",
        preferMethods: ["gameOver", "ShowEndCard", "Progress100"],
        requireMethod: false,
        classOnly: true,
        limit: 120
      }).selected || compEntries(cache, /MainGame|GameManager/i, 20)[0];
      var mainGameComp = mainGameEntry && mainGameEntry.component;
      var mainGameFields = primitiveFieldsOf(mainGameComp, /isGameOver|isWin|win|complete|finish|progress|score|game|over|end|success|coin|money/i);
      knifeTargets.sort(function(a, b) { return (a.priority - b.priority) || ((a.distanceToActor || 999) - (b.distanceToActor || 999)); });
      diTieTargets.sort(function(a, b) { return (/upgradeKnifeDiTie/i.test(a.nodePath || "") ? -1 : /upgradeKnifeDiTie/i.test(b.nodePath || "") ? 1 : 0) || ((a.distanceToActor || 999) - (b.distanceToActor || 999)); });
      finalTriggers.sort(function(a, b) { return ((a.distanceToActor || 999) - (b.distanceToActor || 999)); });
      var economy = Object.assign({}, completion && completion.economy || {}, customer && customer.keyNumbers || {});
      var conveyor2Raw = fastFindRawByPathOrName(cache.scene, "/game/env/Spot/conveyor2DiTie") || fastFindRawByPathOrName(cache.scene, "conveyor2DiTie");
      var conveyor2Compact = compactRaw(conveyor2Raw, true);
      var conveyor2Entry = conveyor2Raw && compOnRaw(conveyor2Raw, /DiTie|Spot|Conveyor|Upgrade/i);
      var conveyor2Fields = conveyor2Entry ? primitiveFieldsOf(conveyor2Entry.component, /price|tempPrice|cost|isFinish|finish|isEnough|enough|canBuy|isBuy|isComplete|active|enabled|state/i) : {};
      var conveyor2Price = Number((conveyor2Fields && (conveyor2Fields.tempPrice ?? conveyor2Fields.price ?? conveyor2Fields.cost)) || 0);
      var knifeDiTie = diTieTargets.find(function(item) { return /upgradeKnifeDiTie/i.test(item.nodePath || ""); }) || null;
      var knifePrice = Number((knifeDiTie && (knifeDiTie.tempPrice ?? knifeDiTie.price)) || 0);
      var coin = Number(economy.coin || 0);
      var playCoin = Number(economy.playCoin || 0);
      var currencyMax = Math.max(coin, playCoin);
      var guideText = (likely && ((likely.name || "") + " " + (likely.path || ""))) || "";
      var rawGuideTargets = (guideVisual && guideVisual.activeGuideNodes || []).slice(0, 20).map(function(item) { return { name: item.name, path: item.path, active: item.active, worldPosition: item.worldPosition }; });
      var guidConveyor2 = compactRaw(fastFindRawByPathOrName(cache.scene, "/game/GameScene/GuideTargetParent/guidConveyor2") || fastFindRawByPathOrName(cache.scene, "guidConveyor2"), true);
      var guidUpgradeKnife = knifeGuideSummary;
      var conveyor2Collider = compactRaw(fastFindRawByPathOrName(cache.scene, "/game/env/conveyor2/colliders/Node-002") || fastFindRawByPathOrName(cache.scene, "/game/env/conveyor2/colliders"), true);
      return {
        ready: true,
        actor: { nodePath: actorSummary && actorSummary.path, worldPosition: actorSummary && actorSummary.worldPosition, state: actorComp ? { isLimitMove: actorComp.isLimitMove, isMoving: actorComp.isMoving, isOnButton: actorComp.isOnButton, isGetOnButton: actorComp.isGetOnButton, isLeavingButton: actorComp.isLeavingButton } : {} },
        economy: economy,
        guide: { likelyGuideTarget: likely, guideDirection: guideVisual && guideVisual.guideDirection, activeGuideNodes: (guideVisual && guideVisual.activeGuideNodes || []).slice(0, 30), blueIndicators: (guideVisual && guideVisual.blueIndicators || []).slice(0, 10), isKnifeGuideActive: Boolean(knifeGuideSummary && knifeGuideSummary.active), knifeGuideSummary: knifeGuideSummary },
        guideState: { likelyGuideTarget: likely, isGuidConveyor2: /guidConveyor2|conveyor2/i.test(guideText), isGuidUpgradeKnife: /guidUpgradeKnife|upgradeKnife|knife/i.test(guideText), isFinalGuide: /final|end|win|complete|knife|upgradeKnife/i.test(guideText), rawGuideTargets: rawGuideTargets },
        targetDistances: {
          conveyor2DiTie: conveyor2Compact && distanceToActor(actorSummary, conveyor2Compact),
          conveyor2Collider: conveyor2Collider && distanceToActor(actorSummary, conveyor2Collider),
          upgradeKnifeDiTie: knifeDiTie && knifeDiTie.distanceToActor,
          guidConveyor2: guidConveyor2 && distanceToActor(actorSummary, guidConveyor2),
          guidUpgradeKnife: guidUpgradeKnife && distanceToActor(actorSummary, guidUpgradeKnife)
        },
        currency: { coin: coin, playCoin: playCoin, canAffordConveyor2: !conveyor2Price || currencyMax >= conveyor2Price, canAffordKnife: !knifePrice || currencyMax >= knifePrice },
        conveyor2: conveyor2Compact ? { diTiePath: conveyor2Compact.path, distanceToActor: distanceToActor(actorSummary, conveyor2Compact), price: conveyor2Fields && (conveyor2Fields.price ?? conveyor2Fields.cost), tempPrice: conveyor2Fields && conveyor2Fields.tempPrice, isFinish: conveyor2Fields && (conveyor2Fields.isFinish ?? conveyor2Fields.finish ?? conveyor2Fields.isComplete), canBuy: conveyor2Fields && (conveyor2Fields.canBuy ?? conveyor2Fields.isBuy), active: conveyor2Compact.active, selectedTargetReason: "exact conveyor2DiTie" } : null,
        knifeTargets: knifeTargets.slice(0, 40),
        diTieTargets: diTieTargets.slice(0, 100),
        finalTriggers: finalTriggers.slice(0, 80),
        mainGame: mainGameEntry ? { className: mainGameEntry.className, nodePath: mainGameEntry.item && mainGameEntry.item.path, primitiveFields: mainGameFields, booleanFields: Object.keys(mainGameFields).reduce(function(out, key) { if (typeof mainGameFields[key] === "boolean") out[key] = mainGameFields[key]; return out; }, {}), numericFields: Object.keys(mainGameFields).reduce(function(out, key) { if (typeof mainGameFields[key] === "number") out[key] = mainGameFields[key]; return out; }, {}), methodNames: methodNames(mainGameComp).filter(function(name) { return /Progress100|ShowEndCard|gameOver|Completed|endcard|CTA|win|complete|finish|progress|score/i.test(name); }).slice(0, 100), isGameOver: mainGameFields.isGameOver, isWin: mainGameFields.isWin ?? mainGameFields.win, progress: pickNumber(mainGameFields, /progress/i), score: pickNumber(mainGameFields, /score/i) } : null,
        callCounters: finalKnifeCounters(counters),
        completion: completion ? { endState: completion.endState, activeEndNodes: completion.activeEndNodes, managerFlags: completion.managerFlags, analyticsEventsTail: completion.analyticsEventsTail } : null
      };
    } catch (e) {
      return { ready: false, error: safeString(e && e.message || e) };
    }
  }

  function guideNodeSummary(raw) {
    try {
      var compact = compactRaw(raw, true);
      if (!compact) return null;
      compact.rotation = rotationOf(raw.node);
      compact.scale = scaleOf(raw.node);
      compact.color = colorOf(raw.node);
      compact.opacity = undefined;
      try {
        var compsRaw = componentList(raw.node);
        for (var i = 0; i < compsRaw.length; i++) {
          if (typeof compsRaw[i].opacity === "number") compact.opacity = compsRaw[i].opacity;
          if (typeof compsRaw[i]._opacity === "number") compact.opacity = compsRaw[i]._opacity;
        }
      } catch (e) {}
      var keyText = compact.name + " " + compact.path + " " + compact.components.join(" ");
      compact.tags = classifyTags(keyText);
      compact.children = children(raw.node).slice(0, 8).map(function(child) {
        return { name: nodeName(child), active: nodeActive(child), worldPosition: worldPosition(child), components: components(child) };
      });
      return compact;
    } catch (e) {
      return null;
    }
  }

  function getGuideVisualSummaryImpl() {
    var resolved = resolveFastCache(false);
    var cache = resolved.cache;
    if (!cache) return { ready: false };
    var actor = compactRaw(cache.keyRaw.Actor || (cache.actorComponent && cache.actorComponent.item), true);
    var guideSummary = getGuideSummaryImpl();
    var activeGuideNodes = [];
    var blueIndicators = [];
    var nodeRegex = /guide|guid|arrow|line|dotted|dot|target|indicator|hand|finger|path|laser1|smallLog|sell|spot|table/i;
    for (var i = 0; i < cache.rawNodes.length && activeGuideNodes.length < 80; i++) {
      var raw = cache.rawNodes[i];
      var compact = compactRaw(raw, false);
      var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
      if (!compact.active || !nodeRegex.test(text)) continue;
      var summary = guideNodeSummary(raw);
      if (!summary) continue;
      activeGuideNodes.push(summary);
      if (isBlueNodeSummary(summary)) {
        blueIndicators.push({
          name: summary.name,
          path: summary.path,
          worldPosition: summary.worldPosition,
          screenPosition: summary.screenPosition,
          reason: /blue/i.test(text) ? "blue name" : /guide|guid|arrow|line|indicator/i.test(text) ? "guide visual" : "blue color",
          confidence: summary.color ? 0.85 : 0.65
        });
      }
    }
    var likely = null;
    if (guideSummary && guideSummary.likelyCurrentTarget && guideSummary.likelyCurrentTarget.worldPosition) {
      likely = {
        name: guideSummary.likelyCurrentTarget.name,
        path: guideSummary.likelyCurrentTarget.path,
        worldPosition: guideSummary.likelyCurrentTarget.worldPosition,
        screenPosition: guideSummary.likelyCurrentTarget.screenPosition,
        reason: "GuideManager likelyCurrentTarget",
        confidence: 0.9
      };
    } else if (blueIndicators.length) {
      likely = Object.assign({}, blueIndicators[0], { reason: "first blue guide indicator", confidence: blueIndicators[0].confidence || 0.6 });
    }
    var guideDirection = {};
    try {
      if (actor && actor.worldPosition && likely && likely.worldPosition) {
        guideDirection.fromPlayerToTargetWorld = {
          x: likely.worldPosition.x - actor.worldPosition.x,
          z: likely.worldPosition.z - actor.worldPosition.z
        };
        guideDirection.distanceToPlayer = distanceXZBetween(actor.worldPosition, likely.worldPosition);
      }
      if (actor && actor.screenPosition && likely && likely.screenPosition) {
        guideDirection.fromPlayerToTargetScreen = {
          x: likely.screenPosition.x - actor.screenPosition.x,
          y: likely.screenPosition.y - actor.screenPosition.y
        };
      }
    } catch (e) {}
    return {
      ready: true,
      guideManagers: (guideSummary && guideSummary.managers) || [],
      guideControllers: (guideSummary && guideSummary.controllers) || [],
      activeGuideNodes: activeGuideNodes,
      likelyGuideTarget: likely,
      blueIndicators: blueIndicators.slice(0, 20),
      guideDirection: guideDirection
    };
  }

  function resolveActionableNearGuideImpl(guideTarget, mode) {
    var resolved = resolveFastCache(false);
    var cache = resolved.cache;
    if (!cache) return { guideTarget: guideTarget, nearbyActionables: [], selected: null };
    var actor = compactRaw(cache.keyRaw.Actor || (cache.actorComponent && cache.actorComponent.item), false);
    var gt = guideTarget;
    if (!gt || !gt.worldPosition) {
      var visual = getGuideVisualSummaryImpl();
      gt = visual && visual.likelyGuideTarget;
    }
    var guidePos = gt && gt.worldPosition;
    var nearby = [];
    function add(node, reason, priority) {
      try {
        if (!node || !node.worldPosition) return;
        var distanceToGuide = distanceXZBetween(guidePos, node.worldPosition);
        if (guidePos && distanceToGuide != null && distanceToGuide > 5) return;
        var path = node.path || node.nodePath;
        if (!path || nearby.some(function(item) { return item.path === path; })) return;
        nearby.push({
          name: node.name || node.nodeName,
          path: path,
          worldPosition: node.worldPosition,
          distanceToGuide: distanceToGuide,
          distanceToActor: distanceToActor(actor, node),
          components: node.components || [],
          reason: reason,
          priority: priority
        });
      } catch (e) {}
    }
    if (mode === "harvest") {
      var harvest = getHarvestChainSummaryImpl();
      (harvest.collectTargets || []).forEach(function(item) {
        var text = (item.name || "") + " " + (item.path || "") + " " + (item.reason || "");
        var priority = /smallLog|logSegments/i.test(text) ? 1 : /collect/i.test(text) ? 2 : /massiveLog/i.test(text) ? 3 : 5;
        add(item, item.reason || "harvest guide nearby", priority);
      });
    } else if (mode === "sell" || mode === "table") {
      if (mode === "table") {
        var table = getTableCustomerChainSummaryImpl();
        (table.tableTargets || []).forEach(function(item) {
          var text = (item.name || "") + " " + (item.path || "") + " " + (item.reason || "");
          var priority = /woodOnTable|woodBagOnTable|woodBag/i.test(text) ? 1 : /Spot|table|desk/i.test(text) ? 2 : /Customer/i.test(text) ? 3 : /getWoodSpot/i.test(text) ? 9 : 5;
          add(item, item.reason || "table guide nearby", priority);
        });
      }
      var sell = getSellChainSummaryImpl();
      (sell.sellTargets || []).forEach(function(item) {
        var text = (item.name || "") + " " + (item.path || "") + " " + (item.reason || "");
        var priority = /woodBagOnTable|woodOnTable/i.test(text) ? 1 : /Spot|sell|table/i.test(text) ? 2 : /SellNPC|Customer/i.test(text) ? 3 : /sellSpotRoot/i.test(text) ? 4 : 5;
        add(item, item.reason || "sell guide nearby", priority);
      });
    } else if (mode === "recruit" || mode === "worker") {
      var recruit = getRecruitChainSummaryImpl();
      (recruit.recruitTargets || []).forEach(function(item) {
        var text = (item.name || "") + " " + (item.path || "") + " " + (item.reason || "") + " " + (item.components || []).join(" ");
        var priority = /recruitWorkerDiTie|worker.*DiTie|DiTie.*worker|upgrade.*worker/i.test(text) ? 1 : /workerNode|Worker/i.test(text) ? 2 : /guidRecruit/i.test(text) ? 4 : /DiTie|upgrade|Spot|button|collider/i.test(text) ? 3 : 6;
        add(item, item.reason || "recruit guide nearby", priority);
      });
      (recruit.diTieTargets || []).forEach(function(item) {
        add({
          name: item.nodePath && item.nodePath.split("/").pop(),
          path: item.nodePath,
          worldPosition: item.worldPosition,
          active: item.active,
          components: item.components || []
        }, "recruit DiTie target", 1);
      });
    } else if (mode === "laser2" || mode === "upgrade" || mode === "conveyor" || mode === "conveyor2") {
      var postRecruit = getPostRecruitChainSummaryImpl();
      var postConveyor = mode === "conveyor2" ? getPostConveyorChainSummaryImpl() : null;
      if (mode === "laser2" || mode === "upgrade") {
        (postRecruit.laser2Targets || []).forEach(function(item) {
          var text = (item.name || "") + " " + (item.path || "") + " " + (item.reason || "") + " " + (item.components || []).join(" ");
          var priority = /upgradeLaserDiTie2/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : /laser2/i.test(text) ? 3 : 6;
          add(item, item.reason || "laser2 guide nearby", priority);
        });
      }
      if (mode === "conveyor" || mode === "conveyor2" || mode === "upgrade") {
        ((mode === "conveyor2" ? postConveyor.conveyor2Targets : postRecruit.conveyorTargets) || []).forEach(function(item) {
          var text = (item.name || "") + " " + (item.path || "") + " " + (item.reason || "") + " " + (item.components || []).join(" ");
          var priority = /conveyor2DiTie/i.test(text) ? 1 : /conveyor1DiTie/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : /guidConveyor/i.test(text) ? 4 : 6;
          add(item, item.reason || "conveyor guide nearby", priority);
        });
      }
      (postRecruit.diTieTargets || []).forEach(function(item) {
        var text = item.nodePath || "";
        if ((mode === "laser2" && !/laser|upgradeLaser/i.test(text)) || (mode === "conveyor" && !/conveyor|belt/i.test(text)) || (mode === "conveyor2" && !/conveyor2|belt/i.test(text))) return;
        add({
          name: item.nodePath && item.nodePath.split("/").pop(),
          path: item.nodePath,
          worldPosition: item.worldPosition,
          active: item.active,
          components: item.components || []
        }, "post-recruit DiTie target", 1);
      });
    } else if (mode === "knife" || mode === "final") {
      var finalKnife = getFinalKnifeChainSummaryImpl();
      (finalKnife.knifeTargets || []).forEach(function(item) {
        var text = (item.name || "") + " " + (item.path || "") + " " + (item.reason || "") + " " + (item.components || []).join(" ");
        var priority = /upgradeKnifeDiTie/i.test(text) ? 1 : /DiTie|Spot|button|collider/i.test(text) ? 2 : /knife|Knife|final/i.test(text) ? 3 : 6;
        add(item, item.reason || "knife/final guide nearby", priority);
      });
      (finalKnife.diTieTargets || []).forEach(function(item) {
        var text = item.nodePath || "";
        if (!/knife|Knife|upgradeKnife|final|end/i.test(text)) return;
        add({
          name: item.nodePath && item.nodePath.split("/").pop(),
          path: item.nodePath,
          worldPosition: item.worldPosition,
          active: item.active,
          components: item.components || []
        }, "knife/final DiTie target", /upgradeKnifeDiTie/i.test(text) ? 1 : 2);
      });
    } else {
      for (var i = 0; i < cache.rawNodes.length; i++) add(compactRaw(cache.rawNodes[i], false), "generic nearby", 5);
    }
    var guidePath = gt && (gt.path || gt.nodePath);
    nearby = nearby.filter(function(item) {
      if (!guidePath || item.path !== guidePath) return true;
      return /collider|trigger/i.test((item.components || []).join(" "));
    });
    nearby.sort(function(a, b) {
      return (a.priority - b.priority) || ((a.distanceToGuide || 999) - (b.distanceToGuide || 999)) || ((a.distanceToActor || 999) - (b.distanceToActor || 999));
    });
    return { guideTarget: gt, nearbyActionables: nearby.slice(0, 40), selected: nearby[0] || null };
  }

  function moveByCocosInputImpl(dx, dz, durationMs, options) {
    var started = Date.now();
    return new Promise(function(resolve) {
      try {
        var cache = resolveFastCache(false).cache;
        var actorEntry = cache && cache.actorComponent;
        var actor = actorEntry && actorEntry.component;
        if (!actor) return resolve({ ok: false, reason: "Actor component not found" });
        if (actor.isLimitMove === true) return resolve({ ok: false, reason: "blockedByLimitMove", blockedByLimitMove: true });
        var fields = ["moveDir", "_moveDir", "inputDir", "_inputDir", "dir", "_dir", "velocity", "_velocity", "moveDirection", "_moveDirection", "inputMoveDir", "_inputMoveDir", "joyDir", "_joyDir", "joystickDir", "_joystickDir", "moveVector", "_moveVector"];
        var touched = [];
        var previous = {};
        var x = Number(dx) || 0;
        var z = Number(dz) || 0;
        var length = Math.sqrt(x * x + z * z) || 1;
        var joyVec = { x: x / length, y: z / length, z: 0 };
        if (typeof actor.move === "function" && typeof actor.stopMove === "function") {
          try {
            actor.move(joyVec);
            var interval = setInterval(function() {
              try { actor.move(joyVec); } catch (e) {}
            }, 120);
            return setTimeout(function() {
              try { clearInterval(interval); } catch (e) {}
              try { actor.stopMove(); } catch (e2) {}
              resolve({ ok: true, backend: "cocos-actor-move", method: "Actor.move", elapsedMs: Date.now() - started, options: options || {} });
            }, Math.max(0, Math.min(Number(durationMs) || 250, 2000)));
          } catch (moveError) {
            // Fall through to field-based movement below.
          }
        }
        function cloneValue(value) {
          if (!value || typeof value !== "object") return value;
          var out = {};
          Object.keys(value).slice(0, 12).forEach(function(key) { try { out[key] = value[key]; } catch (e) {} });
          return out;
        }
        function applyVec(target) {
          if (!target || typeof target !== "object") return false;
          try {
            if (typeof target.set === "function") {
              if (target.w !== undefined) target.set(x, 0, z, target.w);
              else target.set(x, 0, z);
              return true;
            }
          } catch (e) {}
          try {
            target.x = x;
            if ("y" in target) target.y = 0;
            if ("z" in target) target.z = z;
            return true;
          } catch (e2) {}
          return false;
        }
        function makeVec(oldValue) {
          if (oldValue && typeof oldValue === "object") {
            var next = cloneValue(oldValue) || {};
            next.x = x;
            next.y = 0;
            next.z = z;
            return next;
          }
          return { x: x, y: 0, z: z };
        }
        fields.forEach(function(field) {
          try {
            if (!(field in actor)) return;
            previous[field] = cloneValue(actor[field]);
            if (!applyVec(actor[field])) actor[field] = makeVec(actor[field]);
            touched.push(field);
          } catch (e) {}
        });
        ["isMoving", "_isMoving", "moving", "_moving", "isMove", "_isMove", "isRun", "_isRun"].forEach(function(field) {
          try {
            if (!(field in actor)) return;
            previous[field] = actor[field];
            actor[field] = true;
            touched.push(field);
          } catch (e) {}
        });
        if (!touched.length) return resolve({ ok: false, reason: "backendUnavailable", backendUnavailable: true, elapsedMs: Date.now() - started });
        setTimeout(function() {
          touched.forEach(function(field) {
            try {
              if (/moving/i.test(field)) actor[field] = previous[field] === undefined ? false : previous[field];
              else if (actor[field] && typeof actor[field] === "object" && previous[field] && typeof previous[field] === "object" && applyVec(actor[field])) {
                try {
                  actor[field].x = previous[field].x || 0;
                  if ("y" in actor[field]) actor[field].y = previous[field].y || 0;
                  if ("z" in actor[field]) actor[field].z = previous[field].z || 0;
                } catch (e) { actor[field] = previous[field]; }
              } else actor[field] = previous[field];
            } catch (e) {}
          });
          resolve({ ok: true, backend: "cocos", fields: touched, elapsedMs: Date.now() - started, options: options || {} });
        }, Math.max(0, Math.min(Number(durationMs) || 250, 1000)));
      } catch (e) {
        resolve({ ok: false, reason: safeString(e && e.message || e), elapsedMs: Date.now() - started });
      }
    });
  }

  function deltaXZ(before, after) {
    if (!before || !after) return null;
    return {
      x: Number(after.x || 0) - Number(before.x || 0),
      z: Number(after.z || 0) - Number(before.z || 0)
    };
  }

  function normalizeXZ(delta) {
    if (!delta) return null;
    var distance = Math.sqrt(delta.x * delta.x + delta.z * delta.z);
    if (!distance) return null;
    return { x: delta.x / distance, z: delta.z / distance };
  }

  async function calibrateActorMoveBackendImpl(options) {
    var opts = options || {};
    var durationMs = Number(opts.durationMs) || 650;
    var returnMs = Number(opts.returnMs) || 320;
    var settleMs = Number(opts.settleMs) || 180;
    var directions = [
      { name: "up", inputDir: [0, -1] },
      { name: "down", inputDir: [0, 1] },
      { name: "right", inputDir: [1, 0] },
      { name: "left", inputDir: [-1, 0] },
      { name: "upRight", inputDir: [1, -1] },
      { name: "upLeft", inputDir: [-1, -1] },
      { name: "downRight", inputDir: [1, 1] },
      { name: "downLeft", inputDir: [-1, 1] }
    ];
    var started = Date.now();
    var initialObs = observeFastImpl();
    var initialActor = initialObs && initialObs.raw && initialObs.raw.actorState || {};
    var initialPos = initialObs && initialObs.player && initialObs.player.worldPosition;
    if (initialActor.isLimitMove === true) {
      __playableAgentActorMoveCalibration = {
        ready: false,
        reason: "blockedByLimitMove",
        actorState: initialActor,
        samples: [],
        createdAt: new Date().toISOString()
      };
      return __playableAgentActorMoveCalibration;
    }
    var samples = [];
    for (var i = 0; i < directions.length; i++) {
      var item = directions[i];
      var beforeObs = observeFastImpl();
      var before = beforeObs && beforeObs.player && beforeObs.player.worldPosition;
      var result = await moveByCocosInputImpl(item.inputDir[0], item.inputDir[1], durationMs, { calibration: true, name: item.name });
      await new Promise(function(resolve) { setTimeout(resolve, settleMs); });
      var afterObs = observeFastImpl();
      var after = afterObs && afterObs.player && afterObs.player.worldPosition;
      var d = deltaXZ(before, after);
      var distance = d ? Math.sqrt(d.x * d.x + d.z * d.z) : 0;
      var normalized = normalizeXZ(d);
      samples.push({
        name: item.name,
        inputDir: item.inputDir,
        before: before,
        after: after,
        worldDelta: d,
        distance: distance,
        speed: durationMs > 0 ? distance / (durationMs / 1000) : 0,
        normalizedWorldDir: normalized,
        moved: distance > 0.03,
        actorState: afterObs && afterObs.raw && afterObs.raw.actorState || {},
        moveResult: result
      });
      if (returnMs > 0) {
        await moveByCocosInputImpl(-item.inputDir[0], -item.inputDir[1], returnMs, { calibrationReturn: true, name: item.name });
        await new Promise(function(resolve) { setTimeout(resolve, Math.min(settleMs, 120)); });
      }
    }
    var finalObs = observeFastImpl();
    var finalPos = finalObs && finalObs.player && finalObs.player.worldPosition;
    var drift = deltaXZ(initialPos, finalPos);
    __playableAgentActorMoveCalibration = {
      ready: true,
      backend: "cocos-actor-move",
      method: "Actor.move",
      durationMs: durationMs,
      returnMs: returnMs,
      settleMs: settleMs,
      samples: samples,
      initialPlayer: initialPos,
      finalPlayer: finalPos,
      cumulativeDrift: drift,
      createdAt: new Date().toISOString(),
      elapsedMs: Date.now() - started
    };
    return __playableAgentActorMoveCalibration;
  }

  function findFirstComponentSummary(pattern) {
    var list = getComponentSummariesImpl(pattern, {});
    return list.length ? list[0] : null;
  }

  // Nodes whose final path segment names a guide/target/upgrade spot and that
  // are currently active.  Consumed by the Python rule engine as
  // ``guide_or_target_candidates`` (ported from the Node.js probe).
  var GUIDE_TARGET_NODE_RE = /unlockitem|ditie|target|spot|guideline|arr3d/i;
  var GUIDE_TARGET_DONE_STATE_RE = /unlocked|completed|complete|done|purchased|bought/i;

  function buildGuideOrTargetCandidates(cache) {
    var out = [];
    try {
      for (var i = 0; i < cache.rawNodes.length && out.length < 40; i++) {
        var item = cache.rawNodes[i];
        var seg = safeString(item.path).split("/").pop();
        if (!GUIDE_TARGET_NODE_RE.test(seg)) continue;
        var compact = compactRaw(item, true);
        if (!compact || !compact.active || !compact.worldPosition) continue;
        // Skip spots that are already consumed (e.g. an unlocked upgrade pad
        // whose component reports _unlockState "Unlocked" / isUnlocked true).
        var consumed = false;
        var compsRaw = componentList(item.node);
        for (var c = 0; c < compsRaw.length && !consumed; c++) {
          var fields = primitiveFieldsOf(compsRaw[c], /unlockState|isUnlocked|isCompleted|isComplete|isDone|isPurchased|isBought|state/i);
          var keys = Object.keys(fields);
          for (var k = 0; k < keys.length; k++) {
            var key = keys[k];
            var value = fields[key];
            if (typeof value === "boolean" && value && /^(is)?(unlocked|completed|complete|done|purchased|bought)$/i.test(key)) { consumed = true; break; }
            if (typeof value === "string" && /unlockstate/i.test(key) && GUIDE_TARGET_DONE_STATE_RE.test(value) && !/^(noactive|active)$/i.test(value)) { consumed = true; break; }
          }
        }
        if (consumed) continue;
        out.push({
          name: compact.name,
          path: compact.path,
          active: compact.active,
          worldPosition: compact.worldPosition,
          screenPosition: compact.screenPosition
        });
      }
    } catch (e) {}
    return out;
  }

  function observeFastImpl() {
    try {
      var started = Date.now();
      var scene = getScene();
      if (!scene) return { ready: false, done: false, win: false };
      var resolved = resolveFastCache(false);
      var cache = resolved.cache;
      var timings = { resolveCacheMs: resolved.resolveCacheMs };
      if (!cache) return { ready: false, done: false, win: false };
      var keyNames = ["laserButtonModel", "upgradeLaserDiTie", "laser1", "guidSmallLogs", "guidWoodSpot", "collectArea", "collectCollider", "massiveLog", "Machine", "MachineInputSpot", "getWoodSpot", "sellSpotRoot", "woodOnTable", "woodBagOnTable"];
      var keyNodes = {};
      for (var i = 0; i < keyNames.length; i++) {
        var name = keyNames[i];
        var node = compactRaw(cache.keyRaw[name] || fastFindRawByPathOrName(scene, name), false);
        keyNodes[name] = node;
      }
      var actorStarted = Date.now();
      var actor = compactRaw(cache.keyRaw.Actor || (cache.actorComponent && cache.actorComponent.item), false);
      if (!actor) {
        // Generic fallback for games whose player node is not named "Actor"
        // (e.g. "Hero"): reuse the same playerScore heuristic as observe().
        var bestActorScore = 0;
        for (var pi = 0; pi < cache.rawNodes.length; pi++) {
          var actorCand = compactRaw(cache.rawNodes[pi], false);
          if (!actorCand || !actorCand.active) continue;
          var actorScore = playerScore(actorCand);
          if (actorScore > bestActorScore) {
            bestActorScore = actorScore;
            actor = actorCand;
          }
        }
      }
      var actorComponent = null;
      var managers = [];
      if (cache.actorComponent) {
        actorComponent = componentSummary(cache.actorComponent.component, { node: cache.actorComponent.item.node, compact: compactRaw(cache.actorComponent.item, false) }, {});
      }
      timings.readActorMs = Date.now() - actorStarted;
      var harvestStarted = Date.now();
      var harvestChain = getHarvestChainSummaryImpl();
      timings.readHarvestMs = Date.now() - harvestStarted;
      var managerStarted = Date.now();
      for (var r = 0; r < cache.rawNodes.length; r++) {
        var compsRaw = componentList(cache.rawNodes[r].node);
        for (var c = 0; c < compsRaw.length; c++) {
          var cls = className(compsRaw[c]);
          var text = cls + " " + cache.rawNodes[r].path;
          if (/MainGame|MassiveLogController|GuideLine|GuideManager|Game|Manager/i.test(text)) {
            var interesting = readInteresting(compsRaw[c]);
            if (interesting.interestingKeys.length) {
              managers.push({
                className: cls,
                nodeName: nodeName(cache.rawNodes[r].node),
                nodePath: cache.rawNodes[r].path,
                flags: interesting.flags,
                numbers: interesting.numbers,
                interestingKeys: interesting.interestingKeys
              });
            }
          }
        }
      }
      timings.readManagersMs = Date.now() - managerStarted;
      var guideStarted = Date.now();
      var guideSummary = harvestChain && harvestChain.guideSummary ? harvestChain.guideSummary : getGuideSummaryImpl();
      var guideVisualSummary = getGuideVisualSummaryImpl();
      timings.readGuideMs = Date.now() - guideStarted;
      var completionStarted = Date.now();
      var completionSummary = getCompletionSummaryImpl();
      timings.readCompletionMs = Date.now() - completionStarted;
      var merged = mergeFlags(managers);
      var activeUiNodes = [];
      var end = readEndState(activeUiNodes, merged.flags);
      if (completionSummary && completionSummary.endState && completionSummary.endState.done) {
        end = {
          done: completionSummary.endState.done,
          win: completionSummary.endState.win,
          doneReason: completionSummary.endState.reason
        };
      }
      var keyNumbers = {};
      Object.keys(merged.numbers || {}).forEach(function(key) {
        if (/fallCount|smallLogCount|log|wood|bag|capacity|money|coin|score|progress|count/i.test(key)) keyNumbers[key] = merged.numbers[key];
      });
      Object.keys((harvestChain && harvestChain.keyNumbers) || {}).forEach(function(key) {
        if (typeof harvestChain.keyNumbers[key] === "number") keyNumbers[key] = harvestChain.keyNumbers[key];
      });
      var sellChain = getSellChainSummaryImpl();
      Object.keys((sellChain && sellChain.keyNumbers) || {}).forEach(function(key) {
        if (typeof sellChain.keyNumbers[key] === "number") keyNumbers[key] = sellChain.keyNumbers[key];
      });
      var machineChain = getMachineChainSummaryImpl();
      Object.keys((machineChain && machineChain.keyNumbers) || {}).forEach(function(key) {
        if (typeof machineChain.keyNumbers[key] === "number") keyNumbers[key] = machineChain.keyNumbers[key];
      });
      var keyFlags = {};
      Object.keys(merged.flags || {}).forEach(function(key) {
        if (/win|finish|complete|lose|fail|gameOver|isGameOver/i.test(key)) keyFlags[key] = merged.flags[key];
      });
      timings.totalMs = Date.now() - started;
      return {
        ready: true,
        done: end.done,
        win: end.win,
        doneReason: end.doneReason,
        player: actor,
        guideSummary: guideSummary,
        guideVisualSummary: {
          likelyGuideTarget: guideVisualSummary && guideVisualSummary.likelyGuideTarget,
          blueIndicators: (guideVisualSummary && guideVisualSummary.blueIndicators || []).slice(0, 5),
          guideDirection: guideVisualSummary && guideVisualSummary.guideDirection
        },
        keyNumbers: keyNumbers,
        keyFlags: keyFlags,
        keyNodes: keyNodes,
        harvestChain: harvestChain,
        sellChain: sellChain,
        machineChain: machineChain,
        completionSummary: {
          endState: completionSummary && completionSummary.endState,
          activeEndNodes: (completionSummary && completionSummary.activeEndNodes || []).slice(0, 10),
          analyticsEventsTail: (completionSummary && completionSummary.analyticsEventsTail || []).slice(-10),
          economy: completionSummary && completionSummary.economy,
          completionCandidates: (completionSummary && completionSummary.completionCandidates || []).slice(0, 10),
          guide: completionSummary && completionSummary.guide
        },
        endStateSignals: completionSummary && completionSummary.endState && completionSummary.endState.signals,
        callCounters: getCallCounters(),
        actorState: {
          isLimitMove: actorComponent && actorComponent.booleanFields && actorComponent.booleanFields.isLimitMove,
          isOnButton: actorComponent && actorComponent.booleanFields && actorComponent.booleanFields.isOnButton,
          isGetOnButton: actorComponent && actorComponent.booleanFields && actorComponent.booleanFields.isGetOnButton,
          isLeavingButton: actorComponent && actorComponent.booleanFields && actorComponent.booleanFields.isLeavingButton
        },
        strategyRelevantManagers: managers.filter(function(manager) { return /Guide|MassiveLog|Laser|Bag|Game|Manager/i.test(manager.className); }).slice(0, 20),
        guide_or_target_candidates: buildGuideOrTargetCandidates(cache),
        timings: timings
      };
    } catch (e) {
      return { ready: false, done: false, win: false, error: safeString(e && e.message || e) };
    }
  }

  function snapshotComponentsImpl(pattern) {
    return getComponentSummariesImpl(pattern, {}).map(function(summary) {
      return {
        id: summary.className + "@" + summary.nodePath,
        className: summary.className,
        nodeName: summary.nodeName,
        nodePath: summary.nodePath,
        active: summary.nodeActive,
        enabled: summary.enabled,
        nodeWorldPosition: summary.nodeWorldPosition,
        primitiveFields: summary.primitiveFields,
        numericFields: summary.numericFields,
        booleanFields: summary.booleanFields,
        stringFields: summary.stringFields,
        tags: summary.tags
      };
    });
  }

  function diffMaps(before, after) {
    var changes = {};
    var keys = {};
    Object.keys(before || {}).forEach(function(key) { keys[key] = true; });
    Object.keys(after || {}).forEach(function(key) { keys[key] = true; });
    Object.keys(keys).forEach(function(key) {
      var a = before ? before[key] : undefined;
      var b = after ? after[key] : undefined;
      if (JSON.stringify(a) !== JSON.stringify(b)) changes[key] = { before: a, after: b };
    });
    return changes;
  }

  function diffComponentSnapshotsImpl(before, after) {
    var beforeMap = {};
    var afterMap = {};
    (before || []).forEach(function(item) { if (item && item.id) beforeMap[item.id] = item; });
    (after || []).forEach(function(item) { if (item && item.id) afterMap[item.id] = item; });
    var numericFields = [];
    var booleanFields = [];
    var stringFields = [];
    var activatedNodes = [];
    var deactivatedNodes = [];
    var addedComponents = [];
    var removedComponents = [];
    Object.keys(afterMap).forEach(function(id) { if (!beforeMap[id]) addedComponents.push(id); });
    Object.keys(beforeMap).forEach(function(id) { if (!afterMap[id]) removedComponents.push(id); });
    Object.keys(beforeMap).forEach(function(id) {
      var b = beforeMap[id], a = afterMap[id];
      if (!a) return;
      var nums = diffMaps(b.numericFields, a.numericFields);
      Object.keys(nums).forEach(function(key) { numericFields.push({ id: id, field: key, before: nums[key].before, after: nums[key].after, delta: Number(nums[key].after) - Number(nums[key].before) }); });
      var bools = diffMaps(b.booleanFields, a.booleanFields);
      Object.keys(bools).forEach(function(key) { booleanFields.push({ id: id, field: key, before: bools[key].before, after: bools[key].after }); });
      var strings = diffMaps(b.stringFields, a.stringFields);
      Object.keys(strings).forEach(function(key) { stringFields.push({ id: id, field: key, before: strings[key].before, after: strings[key].after }); });
      if (b.active !== a.active) (a.active ? activatedNodes : deactivatedNodes).push({ id: id, nodePath: a.nodePath });
    });
    return {
      numericFields: numericFields,
      booleanFields: booleanFields,
      stringFields: stringFields,
      activatedNodes: activatedNodes,
      deactivatedNodes: deactivatedNodes,
      addedComponents: addedComponents,
      removedComponents: removedComponents
    };
  }

  function summarizeArgs(args) {
    try {
      return Array.prototype.slice.call(args || [], 0, 5).map(summarizeAny);
    } catch (e) {
      return [];
    }
  }

  function pushTraceCall(record) {
    try {
      var trace = window.__playableAgentMethodTrace;
      trace.calls.push(record);
      if (trace.calls.length > 800) trace.calls.shift();
    } catch (e) {}
  }

  function briefStack(stack) {
    return safeString(String(stack || "").split("\n").slice(1, 6).join(" | "));
  }

  function unlockFieldSnapshot() {
    var out = {};
    try {
      var scene = getScene();
      if (!scene) return out;
      var nodes = traverse(scene);
      for (var i = 0; i < nodes.length; i++) {
        var comps = componentList(nodes[i].node);
        for (var j = 0; j < comps.length; j++) {
          var comp = comps[j];
          var cls = className(comp);
          var text = cls + " " + nodes[i].compact.path;
          if (!DEFAULT_FIELD_TRACE_COMPONENT_RE.test(text)) continue;
          var id = cls + "@" + nodes[i].compact.path;
          var fields = {};
          ownKeys(comp).forEach(function(key) {
            try {
              if (!DEFAULT_FIELD_TRACE_FIELD_RE.test(key)) return;
              var value = comp[key];
              if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") fields[key] = value;
            } catch (e) {}
          });
          if (Object.keys(fields).length) out[id] = fields;
        }
      }
    } catch (e) {}
    return out;
  }

  function diffUnlockSnapshots(before, after) {
    var changes = [];
    try {
      var ids = {};
      Object.keys(before || {}).forEach(function(id) { ids[id] = true; });
      Object.keys(after || {}).forEach(function(id) { ids[id] = true; });
      Object.keys(ids).forEach(function(id) {
        var keys = {};
        Object.keys((before || {})[id] || {}).forEach(function(key) { keys[key] = true; });
        Object.keys((after || {})[id] || {}).forEach(function(key) { keys[key] = true; });
        Object.keys(keys).forEach(function(key) {
          var oldValue = before && before[id] ? before[id][key] : undefined;
          var newValue = after && after[id] ? after[id][key] : undefined;
          if (JSON.stringify(oldValue) !== JSON.stringify(newValue)) changes.push({ id: id, field: key, oldValue: oldValue, newValue: newValue });
        });
      });
    } catch (e) {}
    return changes.slice(0, 80);
  }

  function pushFieldChange(record) {
    try {
      var trace = window.__playableAgentFieldTrace;
      trace.changes.push(record);
      if (trace.changes.length > 1000) trace.changes.shift();
    } catch (e) {}
  }

  function stopFieldTraceImpl() {
    try {
      var trace = window.__playableAgentFieldTrace;
      if (!trace || !trace.wrappers) return { restored: 0 };
      var restored = 0;
      for (var i = trace.wrappers.length - 1; i >= 0; i--) {
        var item = trace.wrappers[i];
        try {
          if (item.descriptor) {
            Object.defineProperty(item.target, item.field, item.descriptor);
          } else {
            delete item.target[item.field];
          }
          restored += 1;
        } catch (e) {}
      }
      trace.active = false;
      trace.wrappers = [];
      return { restored: restored };
    } catch (e) {
      return { restored: 0, error: safeString(e && e.message || e) };
    }
  }

  function startFieldTraceImpl(componentPattern, fieldPattern) {
    stopFieldTraceImpl();
    var trace = window.__playableAgentFieldTrace = { active: true, changes: [], failedFields: [], wrappers: [] };
    var compRegex = regexFromString(componentPattern, DEFAULT_FIELD_TRACE_COMPONENT_RE);
    var fieldRegex = regexFromString(fieldPattern, DEFAULT_FIELD_TRACE_FIELD_RE);
    var scene = getScene();
    if (!scene) return { wrapped: 0, failed: 0 };
    var nodes = traverse(scene);
    var wrapped = 0;
    function fail(meta, field, reason) {
      try {
        trace.failedFields.push({ className: meta.className, nodePath: meta.nodePath, field: field, reason: safeString(reason) });
      } catch (e) {}
    }
    function wrapField(comp, field, meta) {
      try {
        if (!fieldRegex.test(field)) return;
        var descriptor = Object.getOwnPropertyDescriptor(comp, field);
        if (descriptor && descriptor.configurable === false) {
          fail(meta, field, "not configurable");
          return;
        }
        var currentValue = comp[field];
        if (!(currentValue === null || typeof currentValue === "string" || typeof currentValue === "number" || typeof currentValue === "boolean")) return;
        var enumerable = descriptor ? descriptor.enumerable : true;
        Object.defineProperty(comp, field, {
          configurable: true,
          enumerable: enumerable,
          get: function() {
            try {
              return descriptor && descriptor.get ? descriptor.get.call(this) : currentValue;
            } catch (e) {
              return currentValue;
            }
          },
          set: function(value) {
            var oldValue;
            try { oldValue = descriptor && descriptor.get ? descriptor.get.call(this) : currentValue; } catch (e) { oldValue = currentValue; }
            try {
              if (descriptor && descriptor.set) descriptor.set.call(this, value);
              else currentValue = value;
            } catch (e) {
              currentValue = value;
            }
            var newValue;
            try { newValue = descriptor && descriptor.get ? descriptor.get.call(this) : currentValue; } catch (e2) { newValue = currentValue; }
            if (JSON.stringify(oldValue) !== JSON.stringify(newValue)) {
              var stack = "";
              try { stack = String(new Error().stack || "").slice(0, 1800); } catch (e3) {}
              pushFieldChange({
                timestamp: Date.now(),
                className: meta.className,
                nodePath: meta.nodePath,
                field: field,
                oldValue: summarizeAny(oldValue),
                newValue: summarizeAny(newValue),
                stack: stack,
                briefStack: briefStack(stack)
              });
            }
          }
        });
        trace.wrappers.push({ target: comp, field: field, descriptor: descriptor || null });
        wrapped += 1;
      } catch (e) {
        fail(meta, field, e && e.message || e);
      }
    }
    for (var i = 0; i < nodes.length; i++) {
      var comps = componentList(nodes[i].node);
      for (var j = 0; j < comps.length; j++) {
        var comp = comps[j];
        var cls = className(comp);
        var text = cls + " " + nodes[i].compact.name + " " + nodes[i].compact.path;
        try { if (!compRegex.test(text)) continue; } catch (e) { continue; }
        var meta = { className: cls, nodePath: nodes[i].compact.path };
        ownKeys(comp).forEach(function(key) { wrapField(comp, key, meta); });
      }
    }
    return { wrapped: wrapped, failed: trace.failedFields.length };
  }

  function getFieldTraceImpl() {
    try {
      var trace = window.__playableAgentFieldTrace || {};
      return {
        active: Boolean(trace.active),
        changes: (trace.changes || []).slice(-1000),
        failedFields: (trace.failedFields || []).slice(0, 300)
      };
    } catch (e) {
      return { active: false, changes: [], failedFields: [], error: safeString(e && e.message || e) };
    }
  }

  function callComponentMethodImpl(componentPattern, methodName) {
    var dangerous = /install|cta|open|store|destroy|remove|clear|reset|fail|lose/i;
    if (!methodName || dangerous.test(methodName)) return { called: false, reason: "dangerous or missing method" };
    try {
      var compRegex = regexFromString(componentPattern, DEFAULT_FIELD_TRACE_COMPONENT_RE);
      var scene = getScene();
      if (!scene) return { called: false, reason: "scene not ready" };
      var nodes = traverse(scene);
      for (var i = 0; i < nodes.length; i++) {
        var comps = componentList(nodes[i].node);
        for (var j = 0; j < comps.length; j++) {
          var comp = comps[j];
          var cls = className(comp);
          var text = cls + " " + nodes[i].compact.name + " " + nodes[i].compact.path;
          if (!compRegex.test(text)) continue;
          var fn = comp[methodName];
          if (typeof fn !== "function") continue;
          if (fn.length > 0) return { called: false, reason: "method requires args", className: cls, nodePath: nodes[i].compact.path, methodName: methodName, length: fn.length };
          var before = unlockFieldSnapshot();
          var result = fn.call(comp);
          var after = unlockFieldSnapshot();
          return {
            called: true,
            className: cls,
            nodePath: nodes[i].compact.path,
            methodName: methodName,
            returnValue: summarizeAny(result),
            fieldDiff: diffUnlockSnapshots(before, after)
          };
        }
      }
      return { called: false, reason: "method not found" };
    } catch (e) {
      return { called: false, reason: safeString(e && e.message || e) };
    }
  }

  function stopMethodTraceImpl() {
    try {
      var trace = window.__playableAgentMethodTrace;
      if (!trace || !trace.wrappers) return { restored: 0 };
      var restored = 0;
      for (var i = trace.wrappers.length - 1; i >= 0; i--) {
        var item = trace.wrappers[i];
        try {
          item.target[item.name] = item.original;
          restored += 1;
        } catch (e) {}
      }
      trace.active = false;
      trace.wrappers = [];
      return { restored: restored };
    } catch (e) {
      return { restored: 0, error: safeString(e && e.message || e) };
    }
  }

  function startMethodTraceImpl(pattern, methodPattern) {
    stopMethodTraceImpl();
    var trace = window.__playableAgentMethodTrace = { active: true, calls: [], updateCounts: {}, wrappers: [] };
    var methodRegex = regexFromString(methodPattern, DEFAULT_METHOD_RE);
    var summaries = getComponentSummariesImpl(pattern, {});
    var scene = getScene();
    if (!scene) return { wrapped: 0, components: 0 };
    var nodes = traverse(scene);
    var wanted = {};
    summaries.forEach(function(summary) { wanted[summary.className + "@" + summary.nodePath] = true; });
    var wrapped = 0;
    function wrap(target, name, meta) {
      try {
        if (!target || typeof target[name] !== "function") return;
        if (target[name].__playableAgentTraceWrapped) return;
        if (!methodRegex.test(name)) return;
        var original = target[name];
        var isUpdate = /^(update|lateUpdate)$/i.test(name);
        target[name] = function() {
          var now = Date.now();
          if (isUpdate) {
            var keyName = meta.className + "@" + meta.nodePath + "." + name;
            var info = trace.updateCounts[keyName] || { callCount: 0, lastTimestamp: 0 };
            info.callCount += 1;
            info.lastTimestamp = now;
            trace.updateCounts[keyName] = info;
          } else {
            var before = unlockFieldSnapshot();
            var result;
            var thrown;
            try {
              result = original.apply(this, arguments);
            } catch (error) {
              thrown = error;
            }
            var after = unlockFieldSnapshot();
            pushTraceCall({
              timestamp: now,
              className: meta.className,
              nodePath: meta.nodePath,
              methodName: name,
              args: summarizeArgs(arguments),
              returnValue: summarizeAny(result),
              fieldDiff: diffUnlockSnapshots(before, after),
              error: thrown ? safeString(thrown && thrown.message || thrown) : undefined
            });
            if (thrown) throw thrown;
            return result;
          }
          return original.apply(this, arguments);
        };
        target[name].__playableAgentTraceWrapped = true;
        trace.wrappers.push({ target: target, name: name, original: original });
        wrapped += 1;
      } catch (e) {}
    }
    for (var i = 0; i < nodes.length; i++) {
      var compsRaw = componentList(nodes[i].node);
      for (var j = 0; j < compsRaw.length; j++) {
        var comp = compsRaw[j];
        var cls = className(comp);
        var id = cls + "@" + nodes[i].compact.path;
        if (!wanted[id]) continue;
        var meta = { className: cls, nodePath: nodes[i].compact.path };
        methodNames(comp).forEach(function(name) {
          wrap(comp, name, meta);
          try { wrap(Object.getPrototypeOf(comp), name, meta); } catch (e) {}
        });
      }
    }
    return { wrapped: wrapped, components: summaries.length };
  }

  function getMethodTraceImpl() {
    try {
      var trace = window.__playableAgentMethodTrace || {};
      return { active: Boolean(trace.active), calls: (trace.calls || []).slice(-800), updateCounts: trace.updateCounts || {} };
    } catch (e) {
      return { active: false, calls: [], updateCounts: {}, error: safeString(e && e.message || e) };
    }
  }

  function ownKeys(obj) {
    try {
      var keys = Object.keys(obj || {});
      return keys.slice(0, 120);
    } catch (e) {
      return [];
    }
  }

  function readInteresting(component) {
    var flags = {};
    var numbers = {};
    var keys = [];
    var rawKeys = ownKeys(component);
    for (var i = 0; i < rawKeys.length; i++) {
      var key = rawKeys[i];
      if (!FLAG_KEYS.test(key) && !NUM_KEYS.test(key)) continue;
      try {
        var value = component[key];
        if (typeof value === "boolean") {
          flags[key] = value;
          keys.push(key);
        } else if (typeof value === "number" && isFinite(value)) {
          numbers[key] = value;
          keys.push(key);
        }
      } catch (e) {}
    }
    return { flags: flags, numbers: numbers, interestingKeys: keys.slice(0, 60) };
  }

  function collectManagers(nodes) {
    var managers = [];
    for (var i = 0; i < nodes.length && managers.length < MAX_ITEMS; i++) {
      var node = nodes[i].node;
      var comps;
      try { comps = node && (node._components || node.components) || []; } catch (e) { comps = []; }
      for (var j = 0; j < comps.length && managers.length < MAX_ITEMS; j++) {
        var cls = className(comps[j]);
        if (!MANAGER_RE.test(cls)) continue;
        var interesting = readInteresting(comps[j]);
        managers.push({
          className: cls,
          nodeName: nodes[i].compact.name,
          nodePath: nodes[i].compact.path,
          flags: interesting.flags,
          numbers: interesting.numbers,
          interestingKeys: interesting.interestingKeys
        });
      }
    }
    return managers;
  }

  function playerScore(compact) {
    var text = compact.name + " " + compact.path + " " + compact.components.join(" ");
    if (!PLAYER_RE.test(text)) return 0;
    var score = 1;
    if (/^(player|actor|hero|character|car|truck|vehicle)$/i.test(compact.name)) score += 8;
    if (/(^|\/)(Player|Actor|Hero|Character|Car|Truck|Vehicle)$/i.test(compact.path)) score += 5;
    if (compact.components.some(function(name) { return /^(Player|Actor|Hero|Character|Car|Truck|Vehicle)$/i.test(name); })) score += 10;
    if (compact.components.some(function(name) { return /CharacterController|RigidBody|Collider/i.test(name); })) score += 2;
    if (/manager|controller|director|root/i.test(compact.name)) score -= 8;
    if (/manager|controller|director/i.test(compact.components.join(" "))) score -= 4;
    if (!compact.active) score -= 3;
    return score;
  }

  function mergeFlags(managers) {
    var flags = {};
    var numbers = {};
    for (var i = 0; i < managers.length; i++) {
      Object.keys(managers[i].flags || {}).forEach(function(key) {
        flags[managers[i].className + "." + key] = managers[i].flags[key];
        flags[key] = managers[i].flags[key];
      });
      Object.keys(managers[i].numbers || {}).forEach(function(key) {
        numbers[managers[i].className + "." + key] = managers[i].numbers[key];
        if (numbers[key] === undefined) numbers[key] = managers[i].numbers[key];
      });
    }
    return { flags: flags, numbers: numbers };
  }

  // Paths of end-like UI nodes that were already active when the probe first
  // observed them (e.g. a persistent Download/CTA button shown during
  // gameplay).  Such nodes are baseline UI, not an end card, so they must not
  // count as completion; only nodes that become active later do.
  var __endStateBaselinePaths = null;

  function readEndState(activeUiNodes, flags) {
    var done = false;
    var win = false;
    var reason = "";
    var presentPaths = [];
    for (var p = 0; p < activeUiNodes.length; p++) {
      presentPaths.push(safeString(activeUiNodes[p] && activeUiNodes[p].path));
    }
    if (__endStateBaselinePaths === null) __endStateBaselinePaths = presentPaths;
    Object.keys(flags || {}).forEach(function(key) {
      if (!flags[key]) return;
      if (WIN_KEYS.test(key)) {
        done = true;
        win = true;
        reason = reason || "manager flag " + key;
      } else if (LOSE_KEYS.test(key)) {
        done = true;
        win = false;
        reason = reason || "manager lose flag " + key;
      } else if (GAMEOVER_KEYS.test(key)) {
        done = true;
        win = true;
        reason = reason || "manager gameOver flag " + key;
      }
    });
    var managerSignalled = done;
    var ctaOnlyPath = null;
    for (var i = 0; i < activeUiNodes.length; i++) {
      var keyText = (activeUiNodes[i].name + " " + activeUiNodes[i].path).toLowerCase();
      // Ignore end-like UI nodes that were already active at first
      // observation (baseline UI such as a persistent CTA/Download button).
      if (__endStateBaselinePaths.indexOf(safeString(activeUiNodes[i].path)) !== -1) continue;
      if (/endcard|win|victory|success|finish|complete|gamewin|ui_win/.test(keyText)) {
        done = true;
        if (!/lose|fail|retry/.test(keyText)) win = true;
        reason = reason || "active UI " + activeUiNodes[i].path;
        break;
      }
      if (/lose|fail|retry/.test(keyText)) {
        done = true;
        win = false;
        reason = reason || "active UI " + activeUiNodes[i].path;
        break;
      }
      // A bare CTA/install/download button is not terminal by itself: many
      // playables keep one on screen during gameplay.  Defer it — it only
      // counts when another completion signal fired (checked below).
      if (!ctaOnlyPath && /cta|install|download/.test(keyText)) {
        ctaOnlyPath = activeUiNodes[i].path;
      }
    }
    var events = (window.__playableAgentEvents || []).slice(-20);
    for (var j = 0; j < events.length; j++) {
      var eventName = safeString(events[j] && events[j].name);
      if (/ENDCARD_SHOWN|COMPLETED|CHALLENGE_SOLVED|CTA_CLICKED/i.test(eventName)) {
        done = true;
        win = !/CTA_CLICKED/i.test(eventName);
        reason = reason || "analytics " + eventName;
      } else if (/CHALLENGE_FAILED/i.test(eventName)) {
        done = true;
        win = false;
        reason = reason || "analytics " + eventName;
      }
    }
    if (!done && ctaOnlyPath && managerSignalled) {
      done = true;
      win = true;
      reason = reason || "active UI " + ctaOnlyPath;
    }
    return { done: done, win: win, doneReason: reason || undefined };
  }

  function patchAnalytics() {
    try {
      function wrap(obj, key) {
        if (!obj || typeof obj[key] !== "function" || obj[key].__playableAgentWrapped) return;
        var original = obj[key];
        obj[key] = function() {
          try {
            var name = arguments.length ? safeString(arguments[0]) : key;
            window.__playableAgentEvents.push({ name: name, args: Array.prototype.slice.call(arguments, 0, 3).map(safeString), timestamp: Date.now() });
            if (window.__playableAgentEvents.length > 100) window.__playableAgentEvents.shift();
          } catch (e) {}
          return original.apply(this, arguments);
        };
        obj[key].__playableAgentWrapped = true;
      }
      wrap(window.ALPlayableAnalytics, "trackEvent");
      wrap(window.ALPlayableAnalytics, "track");
      wrap(window.PlayableAnalytics, "trackEvent");
      wrap(window.PlayableAnalytics, "track");
      wrap(window, "trackEvent");
      wrap(window, "track");
    } catch (e) {}
  }

  patchAnalytics();
  var probe = {
    __version: "0.1.0",
    observe: function() {
      patchAnalytics();
      var scene = getScene();
      if (!window.cc || !scene) {
        return { ready: false, done: false, win: false, managers: [], activeUiNodes: [], interestingNodes: [], numbers: {}, flags: {}, raw: { events: window.__playableAgentEvents || [] } };
      }
      var nodes = traverse(scene);
      var compact = nodes.map(function(item) { return item.compact; });
      var player;
      var bestPlayerScore = 0;
      var activeUiNodes = [];
      var interestingNodes = [];
      for (var i = 0; i < compact.length; i++) {
        var keyText = compact[i].name + " " + compact[i].path + " " + compact[i].components.join(" ");
        var score = playerScore(compact[i]);
        if (score > bestPlayerScore) {
          bestPlayerScore = score;
          player = compact[i];
        }
        if (compact[i].active && UI_RE.test(keyText)) activeUiNodes.push(compact[i]);
        if (INTERESTING_RE.test(keyText) || DEFAULT_NODE_INTEREST_RE.test(keyText)) interestingNodes.push(compact[i]);
      }
      var managers = collectManagers(nodes);
      var merged = mergeFlags(managers);
      var end = readEndState(activeUiNodes, merged.flags);
      return {
        ready: true,
        done: end.done,
        win: end.win,
        doneReason: end.doneReason,
        player: player,
        managers: managers,
        activeUiNodes: activeUiNodes.slice(0, MAX_ITEMS),
        interestingNodes: interestingNodes.slice(0, MAX_ITEMS),
        numbers: merged.numbers,
        flags: merged.flags,
        raw: { nodeCount: compact.length, events: (window.__playableAgentEvents || []).slice(-20) }
      };
    },
    observeFast: function() {
      return observeFastImpl();
    },
    dumpScene: function() {
      var scene = getScene();
      if (!scene) return { ready: false, nodes: [] };
      var nodes = traverse(scene).map(function(item) { return item.compact; });
      return { ready: true, nodeCount: nodes.length, nodes: nodes };
    },
    findNodeByName: function(name) {
      var scene = getScene();
      if (!scene) return null;
      var nodes = traverse(scene);
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i].compact.name === name) return nodes[i].compact;
      }
      return null;
    },
    getNodeByPath: function(pathText) {
      var entry = findNodeEntryByPath(pathText);
      return entry ? entry.compact : null;
    },
    getNodeSummaryByPath: function(pathText) {
      var entry = findNodeEntryByPath(pathText);
      return entry ? entry.compact : null;
    },
    findNodeSummariesByName: function(nameOrRegex) {
      return findNodeSummariesByNameImpl(nameOrRegex);
    },
    findInterestingNodeSummaries: function(pattern) {
      return findInterestingNodeSummariesImpl(pattern);
    },
    getComponentSummaries: function(pattern, options) {
      return getComponentSummariesImpl(pattern, options || {});
    },
    getNodeDeepSummary: function(pathOrName) {
      return getNodeDeepSummaryImpl(pathOrName);
    },
    getGuideSummary: function() {
      return getGuideSummaryImpl();
    },
    getGuideVisualSummary: function() {
      return getGuideVisualSummaryImpl();
    },
    resolveActionableNearGuide: function(guideTarget, mode) {
      return resolveActionableNearGuideImpl(guideTarget, mode || "generic");
    },
    getMethodSources: function(componentPattern, methodPattern, options) {
      return getMethodSourcesImpl(componentPattern, methodPattern, options || {});
    },
    searchMethodSources: function(query, componentPattern) {
      return searchMethodSourcesImpl(query, componentPattern);
    },
    getComponentDependencyGraph: function(pattern) {
      return getComponentDependencyGraphImpl(pattern);
    },
    getHarvestChainSummary: function() {
      return getHarvestChainSummaryImpl();
    },
    getSellChainSummary: function() {
      return getSellChainSummaryImpl();
    },
    getDepositChainSummary: function() {
      return getDepositChainSummaryImpl();
    },
    getMachineChainSummary: function() {
      return getMachineChainSummaryImpl();
    },
    getTableCustomerChainSummary: function() {
      return getTableCustomerChainSummaryImpl();
    },
    getCustomerBuyChainSummary: function() {
      return getCustomerBuyChainSummaryImpl();
    },
    getCompletionSummary: function() {
      return getCompletionSummaryImpl();
    },
    getRecruitChainSummary: function() {
      return getRecruitChainSummaryImpl();
    },
    getPostRecruitChainSummary: function() {
      return getPostRecruitChainSummaryImpl();
    },
    getPostConveyorChainSummary: function() {
      return getPostConveyorChainSummaryImpl();
    },
    getFinalKnifeChainSummary: function() {
      return getFinalKnifeChainSummaryImpl();
    },
    moveByCocosInput: function(dx, dz, durationMs, options) {
      return moveByCocosInputImpl(dx, dz, durationMs, options || {});
    },
    calibrateActorMoveBackend: function(options) {
      return calibrateActorMoveBackendImpl(options || {});
    },
    getActorMoveCalibration: function() {
      return __playableAgentActorMoveCalibration;
    },
    clearActorMoveCalibration: function() {
      __playableAgentActorMoveCalibration = null;
      return true;
    },
    snapshotComponents: function(pattern) {
      return snapshotComponentsImpl(pattern);
    },
    diffComponentSnapshots: function(before, after) {
      return diffComponentSnapshotsImpl(before, after);
    },
    startMethodTrace: function(pattern, methodPattern) {
      return startMethodTraceImpl(pattern, methodPattern);
    },
    stopMethodTrace: function() {
      return stopMethodTraceImpl();
    },
    getMethodTrace: function() {
      return getMethodTraceImpl();
    },
    startFieldTrace: function(componentPattern, fieldPattern) {
      return startFieldTraceImpl(componentPattern, fieldPattern);
    },
    stopFieldTrace: function() {
      return stopFieldTraceImpl();
    },
    getFieldTrace: function() {
      return getFieldTraceImpl();
    },
    callComponentMethod: function(componentPattern, methodName) {
      return callComponentMethodImpl(componentPattern, methodName);
    },
    findComponentsByClass: function(classNameText) {
      var scene = getScene();
      if (!scene) return [];
      var needle = String(classNameText || "").toLowerCase();
      var nodes = traverse(scene);
      var out = [];
      for (var i = 0; i < nodes.length && out.length < MAX_ITEMS; i++) {
        var comps = components(nodes[i].node);
        for (var j = 0; j < comps.length; j++) {
          if (comps[j].toLowerCase().indexOf(needle) !== -1) {
            out.push({ className: comps[j], node: nodes[i].compact });
          }
        }
      }
      return out;
    },
    findPanelButtons: function(panelPattern) {
      // Find tap targets inside *active* panels whose name matches
      // panelPattern (e.g. "LosePanel|FailPanel"). Production builds minify
      // component class names, so a node counts as a button when EITHER a
      // component name ends in "Button" OR the node name matches /btn|button/i.
      // NOTE: uses scene.walk (uncapped, native) — traverse() caps at
      // MAX_NODES and can miss the Canvas/UI branch in big scenes.
      var scene = getScene();
      if (!scene || typeof scene.walk !== "function") return [];
      var re;
      try {
        re = new RegExp(String(panelPattern || "LosePanel|FailPanel|RetryPanel"), "i");
      } catch (e) { return []; }
      var designW = 0, designH = 0;
      try {
        var ds = window.cc && window.cc.view && window.cc.view.getDesignResolutionSize
          && window.cc.view.getDesignResolutionSize();
        if (ds) { designW = ds.width; designH = ds.height; }
      } catch (e) {}
      var panels = [];
      scene.walk(function (n) {
        if (n && n.active && re.test(n.name || "")) panels.push(n);
      });
      var out = [];
      for (var pi = 0; pi < panels.length && out.length < 32; pi++) {
        panels[pi].walk(function (node) {
          if (out.length >= 32 || !node || !node.active) return;
          var nm = nodeName(node);
          var comps = components(node);
          var hasButton = /btn|button/i.test(nm);
          for (var j = 0; j < comps.length && !hasButton; j++) {
            if (/Button$/.test(comps[j])) { hasButton = true; }
          }
          if (!hasButton) return;
          var wp = worldPosition(node);
          if (!wp || !isFinite(wp.x) || !isFinite(wp.y)) return;
          var path = nm;
          try {
            if (typeof node.getPathInHierarchy === "function") path = node.getPathInHierarchy();
          } catch (e) {}
          out.push({
            name: nm,
            path: path,
            designPosition: { x: wp.x, y: wp.y },
            designSize: { width: designW, height: designH },
            dpr: (typeof window.devicePixelRatio === "number" ? window.devicePixelRatio : 1)
          });
        });
      }
      return out;
    },
    setTimeScale: function(scale) {
      try {
        if (window.cc && window.cc.director && window.cc.director.getScheduler) {
          window.cc.director.getScheduler().setTimeScale(Number(scale));
          return true;
        }
      } catch (e) {}
      return false;
    },
    readEndState: function() {
      var obs = this.observe();
      return { done: obs.done, win: obs.win, doneReason: obs.doneReason };
    },
    getDebugSummary: function() {
      var obs = this.observe();
      return {
        ready: obs.ready,
        player: obs.player,
        managers: obs.managers.length,
        activeUiNodes: obs.activeUiNodes.length,
        interestingNodes: obs.interestingNodes.length,
        numbers: obs.numbers,
        flags: obs.flags,
        done: obs.done,
        win: obs.win,
        doneReason: obs.doneReason
      };
    }
  };
  window.__playableAgentProbe = probe;
})();`;
