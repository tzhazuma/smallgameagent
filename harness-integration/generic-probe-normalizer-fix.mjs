import { createHash } from "node:crypto";

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function text(item) {
  return [item?.path, item?.nodePath, item?.name, item?.nodeName, item?.className, ...(item?.components || [])].filter(Boolean).join(" ");
}

function ownText(item) {
  return [item?.name, item?.nodeName, item?.className, ...(item?.components || [])].filter(Boolean).join(" ");
}

function pathOf(item) {
  return item?.path || item?.nodePath || null;
}

function active(item) {
  return item?.active !== false && item?.nodeActive !== false && item?.activeInHierarchy !== false;
}

function worldPoint(item) {
  const value = item?.worldPosition || item?.nodeWorldPosition || item?.world_position || item?.position;
  const x = finite(value?.x);
  const z = finite(value?.z ?? value?.y);
  return x === null || z === null ? null : { x, z };
}

function worldHeight(item) {
  const value = item?.worldPosition || item?.nodeWorldPosition || item?.world_position || item?.position;
  return finite(value?.y);
}

function screenPoint(item) {
  const value = item?.screenPosition || item?.screen_position;
  const x = finite(value?.x);
  const y = finite(value?.y);
  return x === null || y === null ? null : { x, y };
}

function screenVisible(item, viewport) {
  const point = screenPoint(item);
  if (!point || !viewport) return false;
  return point.x >= 0 && point.y >= 0 && point.x <= viewport.width && point.y <= viewport.height;
}

function instancePositionIdentity(item) {
  const point = worldPoint(item);
  return point ? `${point.x.toFixed(3)}:${point.z.toFixed(3)}` : null;
}

function stableId(prefix, item, { includePosition = false } = {}) {
  const pathIdentity = pathOf(item);
  const positionIdentity = includePosition ? instancePositionIdentity(item) : null;
  const identity = pathIdentity
    ? `${pathIdentity}${positionIdentity ? `@${positionIdentity}` : ""}`
    : text(item) || JSON.stringify(item);
  return `${prefix}-${createHash("sha256").update(identity).digest("hex").slice(0, 14)}`;
}

function numericLabel(item) {
  const fields = {
    ...(item?.primitiveFields || {}),
    ...(item?.numericFields || {}),
    ...(item?.numberFields || {}),
    ...(item?.numbers || {}),
    ...(item?.stringFields || {})
  };
  for (const key of [
    "remainingCost",
    "requiredCoins",
    "requiredCount",
    "needNum",
    "cost",
    "price",
    "money",
    "count",
    "value",
    "playNum",
    "num",
    "_string",
    "string",
    "text",
    "label"
  ]) {
    const raw = item?.[key] ?? fields[key];
    if (typeof raw === "number" && Number.isFinite(raw)) return raw;
    if (typeof raw === "string") {
      const number = Number(raw.replace(/[,\s$]/g, ""));
      if (Number.isFinite(number)) return number;
    }
  }
  return null;
}

function dedupe(items) {
  const seen = new Set();
  return items.filter((item) => {
    // Some Unity playables instantiate several active objects from one pooled
    // authored path (doors, enemies, pads). Path-only deduplication erased all
    // but the first physical instance. Keep spatially distinct clones while
    // still coalescing the same node repeated across observation channels.
    const nodePath = pathOf(item);
    const positionIdentity = instancePositionIdentity(item);
    const key = nodePath
      ? `${nodePath}@${positionIdentity || "positionless"}`
      : `${text(item)}:${positionIdentity || "positionless"}`;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function matchesAlias(value, aliases = []) {
  const lower = String(value || "").toLowerCase();
  return aliases.some((alias) => typeof alias === "string" && alias.trim() && lower.includes(alias.trim().toLowerCase()));
}

function matchesPlayerAlias(item, aliases = []) {
  const nodePath = String(pathOf(item) || "").trim().toLowerCase();
  const pathSegments = nodePath.split("/").filter(Boolean);
  const identityTokens = [
    item?.name,
    item?.nodeName,
    item?.className,
    ...(item?.components || [])
  ].filter(Boolean).flatMap((value) => {
    const normalized = String(value).trim().toLowerCase();
    return [normalized, ...normalized.split(/[.:/\\]/).filter(Boolean)];
  });
  return aliases.some((alias) => {
    if (typeof alias !== "string" || !alias.trim()) return false;
    const normalized = alias.trim().toLowerCase();
    if (normalized.startsWith("/")) return nodePath === normalized;
    return pathSegments.includes(normalized) || identityTokens.includes(normalized);
  });
}

function matchesTargetExclusion(item, aliases = []) {
  const nodePath = String(pathOf(item) || "").trim().toLowerCase();
  const identities = [
    item?.name,
    item?.nodeName,
    item?.className,
    ...(item?.components || [])
  ].filter(Boolean).map((value) => String(value).trim().toLowerCase());
  return aliases.some((alias) => {
    if (typeof alias !== "string" || !alias.trim()) return false;
    const normalized = alias.trim().toLowerCase();
    return normalized.startsWith("/")
      ? nodePath === normalized
      : identities.includes(normalized);
  });
}

function isDescendantPath(candidate, ancestor) {
  return Boolean(candidate && ancestor && candidate !== ancestor && candidate.startsWith(`${ancestor}/`));
}

function isUiNode(item) {
  const nodePath = pathOf(item) || "";
  const components = item?.components || [];
  return /\/(?:Canvas_2d|Canvas|UI)(?:\/|$)/i.test(nodePath)
    || components.some((name) => /(?:^|\.)Canvas$/i.test(String(name)));
}

function isCollectibleResourceNode(item) {
  const identity = [
    item?.className,
    ...(item?.components || []),
    item?.primitiveFields?.rssName,
    item?.stringFields?.rssName,
    item?.observationSource
  ].filter(Boolean).join(" ");
  return /(?:RssItem|MoneyPickup|ResourcePickup|Collectible)/i.test(identity);
}

function isResourceCarrierNode(item) {
  if (isCollectibleResourceNode(item)) return true;
  const identity = [
    item?.className,
    ...(item?.components || []),
    item?.primitiveFields?.pointName,
    item?.primitiveFields?.rssName,
    item?.stringFields?.pointName,
    item?.stringFields?.rssName
  ].filter(Boolean).join(" ");
  // RssPoint is the stack/container that owns spawned RssItem instances. Its
  // visual children can have animated rigid-body colliders (cash, ingredients,
  // etc.), but those children are pickups rather than static navigation walls.
  return /(?:^|\s)RssPoint(?:\s|$)/i.test(identity);
}

function isGameProbeSemanticNode(item) {
  return item?.observationSource === "game_probe_semantic"
    || item?.observation_source === "game_probe_semantic"
    || (item?.tags || []).includes("semantic_target");
}

function isFreshOnlyGameProbeSemanticNode(item) {
  if (!isGameProbeSemanticNode(item)) return false;
  const tags = new Set((item?.tags || []).map((value) => String(value).toLowerCase()));
  const nodePath = String(pathOf(item) || "").toLowerCase();
  return item?.primitiveFields?.guided === true
    || item?.primitiveFields?.dynamic_target === true
    || tags.has("guided_resource_target")
    || tags.has("guided_traversal_target")
    || tags.has("dynamic_resource_target")
    || nodePath.includes("/current-guided-source");
}

function hasInfrastructureAncestor(item) {
  const segments = String(pathOf(item) || "").split("/").filter(Boolean).slice(0, -1);
  return segments.some((segment) => /(?:Manager|Controller|Pool|Preload|Mgrs?|RootPoints?|.*Triggers)$/i.test(segment));
}

function isInfrastructureRoot(item) {
  const leaf = String(pathOf(item) || "").split("/").filter(Boolean).at(-1) || "";
  const components = (item?.components || []).map(String);
  if (/^(?:MapTriggers|.*Triggers|RootPoints?)$/i.test(leaf)) return true;
  if (/^(?:Mgrs?|Managers?|GuideMgr|Follow)$/i.test(leaf)
    && components.some((name) => /(?:Manager|Mgr|FollowTarget|CameraController|System)$/i.test(name))) return true;
  const managerCount = components.filter((name) => /(?:Manager|Mgr|Controller|System)$/i.test(name)).length;
  return components.length >= 3 && managerCount / components.length >= 0.6
    && !components.some((name) => /(?:Interaction|Trigger|Collider|DiTie|Pad|Zone)$/i.test(name));
}

function semanticKind(item, config = {}) {
  const configuredValue = text(item);
  const value = ownText(item);
  const leaf = String(pathOf(item) || "").split("/").filter(Boolean).at(-1) || "";
  const componentIdentity = [item?.className, ...(item?.components || [])].filter(Boolean).join(" ");
  const methodIdentity = (item?.methodNames || []).join(" ");
  if (matchesAlias(configuredValue, config.target_aliases?.upgrade)) return { kind: "upgrade", source: "configured_alias" };
  if (matchesAlias(configuredValue, config.target_aliases?.money)) return { kind: "money", source: "configured_alias" };
  if (matchesAlias(configuredValue, config.target_aliases?.interaction)) return { kind: "interaction", source: "configured_alias" };
  // Identify build/payment pads from their interaction contract, not from a
  // localized or misleading node name. A DiTie-like component only becomes an
  // upgrade candidate when it also exposes both trigger handling and a
  // requirement/progression API. Resource identity remains deliberately
  // unresolved until a controlled before/after transaction grounds it.
  if (/(?:^|\s|\.)DiTie(?:\s|$)/i.test(componentIdentity)
    && /onTrigger(?:Enter|Stay|Exit)/i.test(methodIdentity)
    && /(?:getRequired|update.*(?:Num|Score)|checkCanLvUp|changeState|kuozhang|open)/i.test(methodIdentity)) {
    return { kind: "upgrade", source: "behavioral_component" };
  }
  if (isCollectibleResourceNode(item)) {
    const resourceIdentity = [
      item?.primitiveFields?.rssName,
      item?.stringFields?.rssName,
      item?.name,
      item?.nodeName,
      ...(item?.components || [])
    ].filter(Boolean).join(" ");
    return {
      kind: /Money|Coin|Cash|Gold|Currency|钞票|金币/i.test(resourceIdentity) ? "money" : "interaction",
      source: "observed_resource_pickup"
    };
  }
  // A resource manager can expose counters and descendant references, but its
  // own transform (commonly the scene origin) is not a pickup location. Only
  // a configured alias or an observed RssItem/cluster may become actionable.
  if (/(?:^|[_-])(?:mgr|manager)$/i.test(leaf)) return null;
  if (/(?:Manager|Controller|System|Pool|Preload)$/i.test(value.trim())) return null;
  if (/Unlock|Upgrade|Cost|LevelUp|Purchase|BuyPad|Sticker|解锁|升级|购买/i.test(value)) return { kind: "upgrade", source: "semantic_name" };
  if (/Money|Coin|Cash|Gold|Reward|Currency|DropItem/i.test(value)) return { kind: "money", source: "semantic_name" };
  if (/Interaction|Interact|Trigger|Target|Feed|Sell|Storage|Deposit|Door|Gate|Button|Catch|Collect|Harvest|Pickup|Cook(?:ed)?|Process|Factory|Output|Delivery|Deliver|Counter|Checkout/i.test(value)) {
    return { kind: "interaction", source: "semantic_name" };
  }
  return null;
}

function roleHint(item, kind) {
  const value = ownText(item);
  if (kind === "upgrade") return "upgrade";
  if (/Door|Gate|Entrance|Portal|Teleport|Lift|Elevator/i.test(value)) return "traversal_transition";
  // Resource identity is stronger than a generic Pickup/Collect verb. A
  // MoneyPickup is a prerequisite resource source, not a processed-output
  // station, and causal ordering depends on preserving that distinction.
  if (isCollectibleResourceNode(item) || kind === "money" || /Money|Coin|Cash|Gold|Reward|Currency/i.test(value)) return "collect_resource";
  if (/Sell|Deposit|Delivery|Deliver|Counter|Checkout/i.test(value)) return "sell_or_deposit";
  if (/Get.*(?:Factory|Machine)|Output|Pickup|Collect/i.test(value)) return "collect_output";
  if (/Catch|Harvest/i.test(value)) return "collect_source";
  if (/Cook|Process|Factory/i.test(value)) return "process";
  return "interact";
}

function semanticStrength(item, source) {
  if (source === "configured_alias") return 1;
  if (source === "observed_resource_pickup") return 0.98;
  if (source === "behavioral_component") return 0.97;
  const componentText = (item?.components || []).join(" ");
  if (/Unlock|Upgrade|Cost|Money|Coin|Cash|Gold|Reward|Sell|Deposit|Catch|Collect|Harvest|Pickup|Cook|Process|Factory|Output|Interaction|Trigger/i.test(componentText)) return 0.95;
  return 0.75;
}

function interactionAnchorScore(item) {
  const value = ownText(item);
  const nodePath = pathOf(item) || "";
  const leaf = nodePath.split("/").filter(Boolean).at(-1) || "";
  let score = 0;
  if ((item?.components || []).some((name) => /(?:^|\.)DiTie$/i.test(String(name)))) score += 100;
  if ((item?.components || []).some((name) => /Collider|Trigger|Interaction|Interact|Area|Zone|Pad/i.test(String(name)))) score += 70;
  if (/^(?:diTie|interaction|interact|trigger|collider|entry|entrance|pad|zone|area)$/i.test(leaf)) score += 55;
  if (/Interaction|Interact|Trigger|Collider|Entry|Entrance|Pad|Zone|Area/i.test(value)) score += 30;
  if (/Icon|Label|Text|Mesh|Model|Effect|Particle|Money|Price|Cost/i.test(value)) score -= 50;
  return score;
}

function semanticTargetAnchorPriority(candidate) {
  const item = candidate?.candidate || {};
  let score = 0;
  // A game-local probe can resolve a transient actor or guide child to its
  // current world position while the broad scan exposes only an inert pooling
  // parent at the origin. Prefer that fresher observation-only grounding; it
  // changes the anchor, not the decision or action policy.
  if (isGameProbeSemanticNode(item)) score += 100;
  const components = [item?.className, ...(item?.components || [])].filter(Boolean).join(" ");
  if (/(?:Enemy|Mover|Move|Actor|Controller|RewardItemBag)/i.test(components)) score += 40;
  if (item?.observationSource === "live_resource_instance") score += 30;
  if (screenPoint(item)) score += 5;
  return score;
}

function pairedTransitionMidpointAnchor(root, mechanicComponents, viewport) {
  if (root?.role_hint !== "traversal_transition") return null;
  const rootPoint = worldPoint(root);
  if (!rootPoint) return null;
  const candidates = [];
  for (const component of mechanicComponents || []) {
    const componentPath = pathOf(component);
    const componentPoint = worldPoint(component);
    const componentSemantic = [
      component?.className,
      ...(component?.components || []),
      ...(component?.methodNames || [])
    ].filter(Boolean).join(" ");
    if (componentPath !== root.path
      || !componentPoint
      || Math.hypot(componentPoint.x - rootPoint.x, componentPoint.z - rootPoint.z) > 0.75
      || !/(?:door|gate|openDoor|closeDoor)/i.test(componentSemantic)) continue;
    const endpoints = Object.entries(component?.objectRefs || {})
      .filter(([field, reference]) =>
        /^(?:door|gate|leaf|panel|side)[_-]?\d*$/i.test(String(field))
        && reference
        && typeof reference === "object"
        && active(reference)
        && worldPoint(reference)
      )
      .map(([field, reference]) => ({
        field,
        path: pathOf(reference),
        position: worldPoint(reference),
        screen_position: screenPoint(reference)
      }));
    for (let leftIndex = 0; leftIndex < endpoints.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < endpoints.length; rightIndex += 1) {
        const left = endpoints[leftIndex];
        const right = endpoints[rightIndex];
        const separation = Math.hypot(
          left.position.x - right.position.x,
          left.position.z - right.position.z
        );
        // A pair of independently observed door leaves/hinges is stronger
        // traversal geometry than the parent node origin. The latter is often
        // one hinge, so steering at it sends the actor into the adjacent post.
        // Reject tiny decorative pairs and implausibly broad manager refs.
        if (separation < 0.5 || separation > 20) continue;
        const screenPosition = left.screen_position && right.screen_position
          ? {
              x: (left.screen_position.x + right.screen_position.x) / 2,
              y: (left.screen_position.y + right.screen_position.y) / 2
            }
          : null;
        candidates.push({
          path: root.path,
          position: {
            x: (left.position.x + right.position.x) / 2,
            z: (left.position.z + right.position.z) / 2
          },
          screen_position: screenPosition,
          screen_visible: screenPosition
            ? screenVisible({ screenPosition }, viewport)
            : root.screen_visible,
          source: "paired_transition_midpoint",
          score: 200,
          endpoint_fields: [left.field, right.field],
          endpoint_paths: [left.path, right.path],
          endpoint_separation: Number(separation.toFixed(6))
        });
      }
    }
  }
  return candidates.sort((left, right) =>
    right.endpoint_separation - left.endpoint_separation
      || String(left.endpoint_fields).localeCompare(String(right.endpoint_fields))
  )[0] || null;
}

/**
 * Some games expose a door's actual traversal corridor through a manager as
 * two authored navigation references (one on each side of the opening).  The
 * references are more useful than the animated door-leaf transforms: an open
 * leaf may still extend across the first inferred clearance point.
 *
 * Do not depend on field names or a particular language.  Accept only an
 * exact two-node array whose segment passes close to the independently
 * observed semantic opening.  This keeps arbitrary spawn/resource arrays out
 * of navigation semantics.
 */
function pairedTransitionCorridor(root, mechanicComponents, portalAnchor) {
  if (root?.role_hint !== "traversal_transition") return null;
  const anchor = worldPoint({ position: portalAnchor }) || worldPoint(root);
  if (!anchor) return null;
  const candidates = [];
  for (const component of mechanicComponents || []) {
    for (const [field, collection] of Object.entries(component?.arrayFields || {})) {
      const sample = Array.isArray(collection)
        ? collection
        : (Array.isArray(collection?.sample) ? collection.sample : []);
      const declaredLength = Number(collection?.length ?? sample.length);
      if (declaredLength !== 2 || sample.length !== 2) continue;
      const endpoints = sample.map((reference) => ({
        path: pathOf(reference),
        position: worldPoint(reference),
        active: active(reference)
      }));
      if (endpoints.some((endpoint) => !endpoint.active || !endpoint.position)) continue;
      const separation = Math.hypot(
        endpoints[0].position.x - endpoints[1].position.x,
        endpoints[0].position.z - endpoints[1].position.z
      );
      if (separation < 1 || separation > 8) continue;
      const midpoint = {
        x: (endpoints[0].position.x + endpoints[1].position.x) / 2,
        z: (endpoints[0].position.z + endpoints[1].position.z) / 2
      };
      const midpointDistance = Math.hypot(
        midpoint.x - anchor.x,
        midpoint.z - anchor.z
      );
      if (midpointDistance > 1.25) continue;
      candidates.push({
        source: "paired_traversal_reference_points",
        confidence: 0.99,
        mechanic_path: pathOf(component),
        reference_field: field,
        endpoints: endpoints.map(({ path, position }) => ({ path, position })),
        midpoint,
        endpoint_separation: Number(separation.toFixed(6)),
        midpoint_anchor_distance: Number(midpointDistance.toFixed(6))
      });
    }
  }
  return candidates.sort((left, right) =>
    left.midpoint_anchor_distance - right.midpoint_anchor_distance
      || left.endpoint_separation - right.endpoint_separation
      || String(left.reference_field).localeCompare(String(right.reference_field))
  )[0] || null;
}

function interactionAnchors(root, nodes, viewport, mechanicComponents = []) {
  const rootPath = root.path;
  const pairedTransitionAnchor = pairedTransitionMidpointAnchor(
    root,
    mechanicComponents,
    viewport
  );
  const values = [...(pairedTransitionAnchor ? [pairedTransitionAnchor] : []), {
    path: rootPath,
    position: root.position,
    screen_position: root.screen_position,
    screen_visible: root.screen_visible,
    source: "semantic_root",
    score: 1
  }];
  for (const item of nodes) {
    const nodePath = pathOf(item);
    if (!active(item) || !worldPoint(item) || !isDescendantPath(nodePath, rootPath) || isUiNode(item)) continue;
    const score = interactionAnchorScore(item);
    if (score <= 0) continue;
    values.push({
      path: nodePath,
      position: worldPoint(item),
      screen_position: screenPoint(item),
      screen_visible: screenVisible(item, viewport),
      source: score >= 100 ? "ground_interaction_marker" : "interaction_descendant",
      score
    });
  }
  values.sort((a, b) => b.score - a.score
    || String(a.path).split("/").length - String(b.path).split("/").length
    || String(a.path).localeCompare(String(b.path)));
  return values;
}

function colliderBounds(item) {
  const explicit = {
    minX: finite(item?.minX ?? item?.bounds?.minX),
    maxX: finite(item?.maxX ?? item?.bounds?.maxX),
    minZ: finite(item?.minZ ?? item?.bounds?.minZ),
    maxZ: finite(item?.maxZ ?? item?.bounds?.maxZ)
  };
  if (Object.values(explicit).every((value) => value !== null)) return explicit;
  const position = item?.nodeWorldPosition || item?.worldPosition || item?.position;
  const scale = item?.nodeScale || item?.scale || { x: 1, z: 1 };
  const size = item?.colliderFields?.size;
  const center = item?.colliderFields?.center || { x: 0, z: 0 };
  if (![position?.x, position?.z, scale?.x, scale?.z, center?.x ?? 0, center?.z ?? 0]
    .every((value) => finite(value) !== null)) return explicit;
  const angle = Number(item?.nodeRotation?.y || item?.rotation?.y || 0) * Math.PI / 180;
  const localX = finite(center.x ?? 0) * finite(scale.x);
  const localZ = finite(center.z ?? 0) * finite(scale.z);
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const centerX = finite(position.x) + localX * cos + localZ * sin;
  const centerZ = finite(position.z) - localX * sin + localZ * cos;
  const radius = finite(item?.colliderFields?.radius
    ?? item?.primitiveFields?._radius
    ?? item?.primitiveFields?.radius);
  if (radius !== null) {
    const extentX = Math.abs(radius * finite(scale.x));
    const extentZ = Math.abs(radius * finite(scale.z));
    return {
      minX: centerX - extentX,
      maxX: centerX + extentX,
      minZ: centerZ - extentZ,
      maxZ: centerZ + extentZ
    };
  }
  if (![size?.x, size?.z].every((value) => finite(value) !== null)) return explicit;
  const halfX = Math.abs(finite(size.x) * finite(scale.x)) / 2;
  const halfZ = Math.abs(finite(size.z) * finite(scale.z)) / 2;
  const extentX = Math.abs(halfX * cos) + Math.abs(halfZ * sin);
  const extentZ = Math.abs(halfX * sin) + Math.abs(halfZ * cos);
  return { minX: centerX - extentX, maxX: centerX + extentX, minZ: centerZ - extentZ, maxZ: centerZ + extentZ };
}

function colliderOrientedBounds(item) {
  const explicit = item?.oriented_bounds || item?.orientedBounds || null;
  const explicitCenter = explicit?.center || null;
  const explicitHalf = explicit?.half_extents || explicit?.halfExtents || null;
  const explicitRotation = finite(explicit?.rotation_y_degrees ?? explicit?.rotationYDegrees);
  if ([explicitCenter?.x, explicitCenter?.z, explicitHalf?.x, explicitHalf?.z, explicitRotation]
    .every((value) => finite(value) !== null)) {
    return {
      center: { x: finite(explicitCenter.x), z: finite(explicitCenter.z) },
      half_extents: {
        x: Math.abs(finite(explicitHalf.x)),
        z: Math.abs(finite(explicitHalf.z))
      },
      rotation_y_degrees: explicitRotation
    };
  }

  const className = String(item?.className || item?.type || "");
  if (!/BoxCollider/i.test(className)) return null;
  const position = item?.nodeWorldPosition || item?.worldPosition || item?.position;
  const scale = item?.nodeScale || item?.scale || { x: 1, z: 1 };
  const size = item?.colliderFields?.size;
  const center = item?.colliderFields?.center || { x: 0, z: 0 };
  const rotation = finite(item?.nodeRotation?.y ?? item?.rotation?.y ?? 0);
  if (![position?.x, position?.z, scale?.x, scale?.z, size?.x, size?.z, center?.x ?? 0, center?.z ?? 0, rotation]
    .every((value) => finite(value) !== null)) return null;
  // Multiples of 90 degrees are represented exactly by the existing AABB
  // contract. Preserve that representation so existing scoped traversal
  // evidence remains compatible; OBB metadata is needed only when rotation
  // makes the AABB a materially larger over-approximation.
  const quarterTurns = Math.round(rotation / 90);
  if (Math.abs(rotation - quarterTurns * 90) <= 1e-6) return null;

  const angle = rotation * Math.PI / 180;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const localX = finite(center.x ?? 0) * finite(scale.x);
  const localZ = finite(center.z ?? 0) * finite(scale.z);
  return {
    center: {
      x: finite(position.x) + localX * cos + localZ * sin,
      z: finite(position.z) - localX * sin + localZ * cos
    },
    half_extents: {
      x: Math.abs(finite(size.x) * finite(scale.x)) / 2,
      z: Math.abs(finite(size.z) * finite(scale.z)) / 2
    },
    rotation_y_degrees: rotation
  };
}

function isPlanarNavigationObstacle(item) {
  const className = String(item?.className || item?.type || "");
  // Several playable-game SDKs expose custom XZ-only avoidance components.
  // Their nodes can live on an arbitrary render Y layer (for example -15),
  // but they are not necessarily player colliders: some describe an AI
  // avoidance/building footprint and may even contain the authoritative
  // walk-on target. Preserve their geometry as advisory evidence. Only
  // ordinary physics colliders are hard blockers before measured player
  // movement proves that one of these advisory boundaries is relevant.
  return /^(?:SphereObstacle|BoxObstacle)$/i.test(className)
    || (/(?:Sphere|Box)Obstacle$/i.test(className)
      && finite(item?.primitiveFields?.obstacleGroup) !== null
      && finite(item?.primitiveFields?.collisionType) !== null);
}

function isStaticPlanarWorldStructure(item) {
  const nodePath = String(pathOf(item) || "");
  // Some playables expose the permanent house/castle footprint and outer
  // world boundary through custom XZ avoidance components rather than a
  // physics Collider. These are not pooled future-build footprints: their
  // authored path identifies an already-present static world structure.
  //
  // Keep staged buildings under building_mgr/build advisory until their
  // authoritative progression receipt completes. Treating every
  // SphereObstacle as hard previously made dormant future rooms impassable.
  return /\/layer_game\/castle(?:\/|$)/i.test(nodePath)
    || /\/layer_env\/air_box(?:\/|$)/i.test(nodePath);
}

function structuralLifecycleSignals(raw, config = {}) {
  const threshold = finite(config?.structural_stowed_y_threshold) ?? -5;
  const items = [
    ...(raw?.obstacleSummary || []),
    ...(raw?.components || []),
    ...(raw?.causalComponents || []),
    ...(raw?.interestingNodes || []),
    ...(raw?.observe?.interestingNodes || [])
  ];
  const signals = new Map();
  for (const item of items) {
    const nodePath = String(pathOf(item) || "");
    const className = String(item?.className || "");
    if (!nodePath || !className) continue;
    if (!/(?:building_mgr|structure|fort|castle|fence|wall|barrier|bridge|gate|door)/i.test(nodePath)) continue;
    if (!/^(?:Fence|Wall|Barrier|Door|Gate|Building|Bridge)$/i.test(className)) continue;
    const position = item?.nodeWorldPosition || item?.worldPosition || item?.position || null;
    const x = finite(position?.x);
    const y = finite(position?.y);
    const z = finite(position?.z);
    const instance = x !== null && z !== null
      ? `${x.toFixed(3)},${z.toFixed(3)}`
      : "positionless";
    const id = `${nodePath}|${className}|${instance}`;
    const nodeActive = active(item);
    const deploymentState = !nodeActive
      ? "inactive"
      : (y !== null && y <= threshold ? "stowed_below_world" : "deployed_world");
    signals.set(id, {
      id,
      path: nodePath,
      class_name: className,
      active: nodeActive,
      deployed: deploymentState === "deployed_world",
      deployment_state: deploymentState,
      position: x !== null && z !== null ? { x, y, z } : null,
      source: "backend_structural_lifecycle"
    });
  }
  return [...signals.values()].sort((left, right) => left.id.localeCompare(right.id));
}

function colliderVerticalBounds(item) {
  const explicitMin = finite(item?.minY ?? item?.bounds?.minY ?? item?.bounds?.min_y);
  const explicitMax = finite(item?.maxY ?? item?.bounds?.maxY ?? item?.bounds?.max_y);
  if (explicitMin !== null && explicitMax !== null) return { minY: explicitMin, maxY: explicitMax };
  const position = item?.nodeWorldPosition || item?.worldPosition || item?.position;
  const scale = item?.nodeScale || item?.scale || { y: 1 };
  const center = item?.colliderFields?.center || { y: 0 };
  const positionY = finite(position?.y);
  const scaleY = finite(scale?.y ?? 1);
  const centerY = finite(center?.y ?? 0);
  if (positionY === null || scaleY === null || centerY === null) return null;
  const sizeY = finite(item?.colliderFields?.size?.y);
  if (sizeY !== null) {
    const middle = positionY + centerY * scaleY;
    const half = Math.abs(sizeY * scaleY) / 2;
    return { minY: middle - half, maxY: middle + half };
  }
  const radius = finite(item?.colliderFields?.radius ?? item?.primitiveFields?._radius ?? item?.primitiveFields?.radius);
  const height = finite(item?.colliderFields?.height ?? item?.primitiveFields?._height ?? item?.primitiveFields?.height);
  if (radius === null && height === null) return null;
  const middle = positionY + centerY * scaleY;
  const half = Math.abs(((height ?? 0) / 2 + (radius ?? 0)) * scaleY);
  return { minY: middle - half, maxY: middle + half };
}

function playerVerticalBounds(raw, playerItem) {
  const playerPath = pathOf(playerItem);
  const playerY = worldHeight(playerItem);
  if (!playerPath || playerY === null) return null;
  const summaries = [
    ...(raw?.obstacleSummary || []),
    ...(raw?.components || []),
    ...(raw?.causalComponents || [])
  ];
  const controller = summaries.find((item) => pathOf(item) === playerPath
    && /CharacterController/i.test(String(item?.className || "")));
  const controllerBounds = controller ? colliderVerticalBounds(controller) : null;
  if (controllerBounds) return controllerBounds;
  // Cocos character nodes conventionally sit at the actor's feet. Keep a
  // small band below the node for skin/radius tolerance and a generous band
  // above it. Unknown-height obstacles remain conservative blockers.
  return { minY: playerY - 0.4, maxY: playerY + 1.8 };
}

function verticallyRelevantToPlayer(item, playerBounds) {
  if (!playerBounds) return true;
  const obstacleBounds = colliderVerticalBounds(item);
  if (!obstacleBounds) return true;
  const epsilon = 0.05;
  return obstacleBounds.maxY >= playerBounds.minY - epsilon
    && obstacleBounds.minY <= playerBounds.maxY + epsilon;
}

function parentPath(value) {
  const segments = String(value || "").split("/").filter(Boolean);
  if (segments.length <= 1) return null;
  return `/${segments.slice(0, -1).join("/")}`;
}

function pointInsideCollider(point, bounds, epsilon = 0.06) {
  return Boolean(point)
    && [bounds?.minX, bounds?.maxX, bounds?.minZ, bounds?.maxZ].every((value) => finite(value) !== null)
    && point.x >= bounds.minX - epsilon
    && point.x <= bounds.maxX + epsilon
    && point.z >= bounds.minZ - epsilon
    && point.z <= bounds.maxZ + epsilon;
}

function walkOnInteractionPoints(raw) {
  const nodes = dedupe([
    ...(raw?.components || []),
    ...(raw?.causalComponents || []),
    ...(raw?.interestingNodes || []),
    ...(raw?.observe?.interestingNodes || [])
  ]);
  return nodes.filter((item) => active(item)
    && worldPoint(item)
    && /(?:Interaction|Interact|Trigger|DiTie|Button|Pad|Zone|Area|操作|交互|触发)/i.test(ownText(item)))
    .map((item) => ({ path: pathOf(item), position: worldPoint(item) }))
    .filter((item) => item.path && item.position);
}

function isWalkOnInteractionSurface(item, bounds, interactionPoints) {
  const nodePath = pathOf(item);
  if (!nodePath || item?.colliderFields?.isTrigger === true || item?.isTrigger === true) return false;
  // Walk-on controls are often rendered with a solid BoxCollider below the
  // actual trigger. Treat that decorative/support surface as an affordance,
  // not a wall, only when its identity is surface-like and it contains an
  // active interaction anchor from the same trigger cluster.
  if (!/(?:Button|Pad|Plate|Sticker|Ground|Floor|Zone|Area|DiTie|按钮|地块|地贴|踏板|区域|操作台)/i.test(nodePath)) return false;
  return interactionPoints.some((interaction) => {
    const cluster = parentPath(interaction.path);
    return cluster
      && (nodePath === cluster || isDescendantPath(nodePath, cluster))
      && pointInsideCollider(interaction.position, bounds);
  });
}

function normalizeObstacles(raw, playerItem = null) {
  const playerPath = pathOf(playerItem);
  const playerY = worldHeight(playerItem);
  const playerBounds = playerVerticalBounds(raw, playerItem);
  const resourceCarrierPaths = new Set([
    ...(raw?.components || []),
    ...(raw?.causalComponents || []),
    ...(raw?.interestingNodes || []),
    ...(raw?.observe?.interestingNodes || []),
    ...(raw?.obstacleSummary || []),
    ...(raw?.moneyResources?.resources || [])
  ].filter(isResourceCarrierNode).map(pathOf).filter(Boolean));
  const interactionPoints = walkOnInteractionPoints(raw);
  const gameSemanticEphemeralObstaclePrefixes = (
    raw?.gameProbe?.game_state?.ephemeral_obstacle_path_prefixes || []
  ).filter((value) => typeof value === "string" && value.trim())
    .map((value) => value.trim());
  return (raw?.obstacleSummary || raw?.observe?.obstacles || []).map((item) => {
    const bounds = colliderBounds(item);
    const orientedBounds = colliderOrientedBounds(item);
    const verticalBounds = colliderVerticalBounds(item);
    const nodePath = pathOf(item);
    const className = String(item?.className || "");
    const playerOwned = Boolean(playerPath) && (nodePath === playerPath || isDescendantPath(nodePath, playerPath));
    const gameSemanticCollectible = gameSemanticEphemeralObstaclePrefixes
      .some((prefix) => nodePath === prefix || isDescendantPath(nodePath, prefix));
    const collectibleResource = gameSemanticCollectible
      || resourceCarrierPaths.has(nodePath)
      || [...resourceCarrierPaths].some((resourcePath) => isDescendantPath(nodePath, resourcePath));
    const renderOnly = /(?:Sprite|Mesh|SkinnedMesh)Renderer/i.test(className)
      && !/(?:Collider|CharacterController)/i.test(className);
    const horizontalWidth = finite(bounds.maxX) !== null && finite(bounds.minX) !== null
      ? Math.abs(bounds.maxX - bounds.minX)
      : 0;
    const horizontalDepth = finite(bounds.maxZ) !== null && finite(bounds.minZ) !== null
      ? Math.abs(bounds.maxZ - bounds.minZ)
      : 0;
    const supportingFloor = /BoxCollider/i.test(className)
      && item?.colliderFields?.isTrigger !== true
      && playerY !== null
      && verticalBounds !== null
      && verticalBounds.maxY <= playerY + 0.1
      && verticalBounds.maxY >= playerY - 0.5
      && horizontalWidth >= 8
      && horizontalDepth >= 8;
    const interactionSurface = isWalkOnInteractionSurface(item, bounds, interactionPoints);
    const planarNavigationObstacle = isPlanarNavigationObstacle(item);
    const staticPlanarWorldStructure = planarNavigationObstacle
      && isStaticPlanarWorldStructure(item);
    const structuralFenceCandidate = planarNavigationObstacle
      && /\/building_mgr\/fence\/fence_area\d+_/i.test(nodePath);
    const obstacleInstanceId = `obstacle-${createHash("sha256").update(JSON.stringify({
      path: nodePath || null,
      type: item?.type || item?.className || null,
      bounds: {
        min_x: finite(bounds.minX),
        max_x: finite(bounds.maxX),
        min_z: finite(bounds.minZ),
        max_z: finite(bounds.maxZ)
      },
      oriented_bounds: orientedBounds || null
    })).digest("hex").slice(0, 14)}`;
    return {
      // Cocos permits several spatially distinct collider components to share
      // the same authored node path.  Path-only identities collapse separate
      // wall segments into one graph node and make a visibility route appear
      // to pass through their shared corner.  Keep the semantic path for
      // structural association, but give every positioned collider instance
      // a stable geometry-scoped identity.
      id: obstacleInstanceId,
      path: nodePath || stableId("obstacle", item),
      type: item?.type || item?.className || null,
      active: active(item),
      navigation_blocking: !playerOwned
        && !collectibleResource
        && !renderOnly
        && !supportingFloor
        && !interactionSurface
        && (!planarNavigationObstacle || staticPlanarWorldStructure)
        && verticallyRelevantToPlayer(item, playerBounds),
      navigation_advisory: !playerOwned
        && !collectibleResource
        && !interactionSurface
        && planarNavigationObstacle
        && !staticPlanarWorldStructure,
      navigation_scope: staticPlanarWorldStructure
        ? "static_world_structure"
        : (gameSemanticCollectible
            ? "game_semantic_resource_telemetry"
        : (structuralFenceCandidate
            ? "structural_fence_candidate"
            : (planarNavigationObstacle ? "custom_avoidance_component" : "player_collision"))),
      interaction_surface: interactionSurface,
      planar_navigation_obstacle: planarNavigationObstacle,
      vertical_bounds: verticalBounds ? { min_y: verticalBounds.minY, max_y: verticalBounds.maxY } : null,
      oriented_bounds: orientedBounds,
      is_trigger: item?.isTrigger === true || item?.colliderFields?.isTrigger === true,
      minX: bounds.minX,
      maxX: bounds.maxX,
      minZ: bounds.minZ,
      maxZ: bounds.maxZ
    };
  });
}

function interactionBoundsForAnchor(anchor, obstacles) {
  const candidates = obstacles.filter((item) => item.active !== false
    && item.is_trigger === true
    && (item.path === anchor.path || isDescendantPath(item.path, anchor.path))
    && [item.minX, item.maxX, item.minZ, item.maxZ].every((value) => finite(value) !== null));
  candidates.sort((a, b) => ((a.maxX - a.minX) * (a.maxZ - a.minZ)) - ((b.maxX - b.minX) * (b.maxZ - b.minZ)));
  const selected = candidates[0];
  return selected ? {
    min_x: selected.minX,
    max_x: selected.maxX,
    min_z: selected.minZ,
    max_z: selected.maxZ,
    source_path: selected.path,
    source: "descendant_trigger_collider"
  } : null;
}

function activeResourcePositionNodes(raw) {
  const nodes = [];
  const resources = raw?.moneyResources?.resources || [];
  const explicitlyUnreadyResourcePaths = new Set(resources
    .filter((item) => item?.primitiveFields?.isReady === false
      || item?.primitiveFields?.ready === false)
    .map(pathOf)
    .filter(Boolean));
  const explicitlyUnreadyResources = resources
    .filter((item) => item?.primitiveFields?.isReady === false
      || item?.primitiveFields?.ready === false)
    .map((item) => ({
      path: pathOf(item),
      name: String(item?.name || item?.nodeName || "").toLowerCase(),
      point: worldPoint(item)
    }));
  const isObservedMoneyPickup = (item) => {
    const identity = [
      item?.path,
      item?.name,
      item?.nodeName,
      item?.className,
      ...(item?.components || [])
    ].filter(Boolean).join(" ");
    // CoinPool descendant sweeps can include the sale counter, payment-pad
    // icons and the player's bags.  They share the manager's "money" context
    // but are not physical cash.  Require pickup-like money identity and
    // explicitly reject facilities, UI and inventory containers.
    if (/(?:counter|checkout|deposit|sell|ditie|pad|sticker|bag|canvas|label|icon|manager|pool|layer)/i.test(identity)) {
      return false;
    }
    // An authored Coin node can remain active below the floor while its
    // gameplay component explicitly reports isReady=false.  Parent-manager
    // descendant sweeps omit that field, so cross-reference the exact resource
    // path instead of promoting the visual placeholder to a pickup cluster.
    if (explicitlyUnreadyResourcePaths.has(pathOf(item))) return false;
    // A broad manager sweep can return a shallow copy without its authored
    // path or primitive fields. Match that copy back to an explicitly unready
    // resource by semantic name and world position so the same underground
    // placeholder cannot re-enter through the descendant channel.
    const itemPoint = worldPoint(item);
    const itemName = String(item?.name || item?.nodeName || "").toLowerCase();
    if (itemPoint && explicitlyUnreadyResources.some((resource) =>
      resource.point
      && resource.name
      && resource.name === itemName
      && Math.hypot(resource.point.x - itemPoint.x, resource.point.z - itemPoint.z) <= 0.05
    )) return false;
    return /(?:money|coin|cash|gold|currency|chao\s*piao|chaopiao|钞票|现金|金币)/i.test(identity);
  };
  // A map RssPoint can expose getRssWorldPosByIndex values in its own local
  // stack frame (often 0,0) even when its spawned RssItem rigid bodies have
  // already scattered across the playable floor.  The live item is the
  // actionable fact; the stack slot is only a fallback when no physical item
  // instance is observable.  Keep one representative per observed resource
  // path.  After it is collected the next same-path instance becomes the
  // representative on the following probe.
  const livePickups = resources.filter((resource) => (
    resource?.active !== false
    && !explicitlyUnreadyResourcePaths.has(pathOf(resource))
    && isCollectibleResourceNode(resource)
    && worldPoint(resource)
  ));
  for (const resource of livePickups) {
    const point = worldPoint(resource);
    const resourcePath = pathOf(resource) || "/observed/resources/pickup";
    const moneyIdentity = [
      resource?.name,
      resource?.nodeName,
      resource?.className,
      ...(resource?.components || [])
    ].filter(Boolean).join(" ");
    const money = /Money|Coin|Cash|Gold|Currency|钞票|金币/i.test(moneyIdentity);
    nodes.push({
      path: `${resourcePath}/__observed_live_pickup`,
      name: resource?.name || resource?.nodeName || (money ? "MoneyPickup" : "ResourcePickup"),
      className: "RssItem",
      components: [...new Set([...(resource?.components || []), "RssItem", money ? "MoneyPickup" : "ResourcePickup"])],
      active: true,
      worldPosition: { x: point.x, y: worldHeight(resource) ?? 0, z: point.z },
      observationSource: "live_resource_instance"
    });
  }
  for (const resource of resources) {
    if (resource?.active === false) continue;
    const resourcePath = pathOf(resource) || "/observed/resources/money";
    const liveMoneyDescendants = (resource?.activeMoneyDescendants || [])
      .filter((item) =>
        item?.active !== false
        && worldPoint(item)
        && isObservedMoneyPickup(item)
      )
      .map((item) => ({ item, point: worldPoint(item) }))
      .sort((left, right) => left.point.x - right.point.x || left.point.z - right.point.z);
    // Coin managers often expose every scattered pickup but no actionable
    // position on the manager itself. Collapse spatially connected pickups
    // into stable cluster targets so the strategy routes to the pile instead
    // of the manager origin or issuing one micro-route per coin.
    const unassigned = new Set(liveMoneyDescendants.map((_, index) => index));
    const clusters = [];
    while (unassigned.size > 0) {
      const seed = Math.min(...unassigned);
      unassigned.delete(seed);
      const members = [liveMoneyDescendants[seed]];
      const queue = [liveMoneyDescendants[seed]];
      while (queue.length > 0) {
        const current = queue.shift();
        for (const index of [...unassigned]) {
          const candidate = liveMoneyDescendants[index];
          if (Math.hypot(
            candidate.point.x - current.point.x,
            candidate.point.z - current.point.z
          ) > 6) continue;
          unassigned.delete(index);
          members.push(candidate);
          queue.push(candidate);
        }
      }
      clusters.push(members);
    }
    clusters.sort((left, right) => {
      const leftX = left.reduce((sum, entry) => sum + entry.point.x, 0) / left.length;
      const rightX = right.reduce((sum, entry) => sum + entry.point.x, 0) / right.length;
      const leftZ = left.reduce((sum, entry) => sum + entry.point.z, 0) / left.length;
      const rightZ = right.reduce((sum, entry) => sum + entry.point.z, 0) / right.length;
      return leftX - rightX || leftZ - rightZ;
    });
    clusters.forEach((cluster, index) => {
      const center = {
        x: cluster.reduce((sum, entry) => sum + entry.point.x, 0) / cluster.length,
        z: cluster.reduce((sum, entry) => sum + entry.point.z, 0) / cluster.length
      };
      const representative = [...cluster].sort((left, right) =>
        Math.hypot(left.point.x - center.x, left.point.z - center.z)
          - Math.hypot(right.point.x - center.x, right.point.z - center.z)
      )[0];
      nodes.push({
        path: `${resourcePath}/__observed_money_cluster_${index}`,
        name: "MoneyPickupCluster",
        className: "RssItem",
        components: ["RssItem", "MoneyPickup", "MoneyPickupCluster"],
        active: true,
        worldPosition: {
          x: representative.point.x,
          y: worldHeight(representative.item) ?? 0,
          z: representative.point.z
        },
        primitiveFields: { pickup_count: cluster.length },
        observationSource: "active_money_descendant_cluster"
      });
    });
    const hasLiveDescendant = livePickups.some((pickup) => isDescendantPath(pathOf(pickup), resourcePath));
    if (hasLiveDescendant || liveMoneyDescendants.length > 0) continue;
    for (const entry of resource?.stackPositions || []) {
      const point = worldPoint(entry);
      if (!point) continue;
      const index = Number.isInteger(Number(entry?.index)) ? Number(entry.index) : nodes.length;
      nodes.push({
        path: `${resourcePath}/__observed_money_pickup_${index}`,
        name: `MoneyPickup${index}`,
        className: "RssItem",
        components: ["RssItem", "MoneyPickup"],
        active: true,
        worldPosition: { x: point.x, y: worldHeight(entry) ?? 0, z: point.z },
        observationSource: entry?.source || "resource_stack_position"
      });
    }
  }
  return nodes;
}

function allNodes(raw) {
  const currentGameProbeNodes = raw?.gameProbe?.nodes || [];
  const withoutCachedGameProbeSemantics = (items = []) => items.filter((item) => !isGameProbeSemanticNode(item));
  const withoutFreshOnlyGameProbeSemantics = (items = []) =>
    items.filter((item) => !isFreshOnlyGameProbeSemanticNode(item));
  const resolvedPlayer = raw?.player || raw?.fast?.player || null;
  return dedupe([
    // A game-local normalizer may attach richer, observation-only semantics to
    // the same backend path. Put those nodes first so deduplication keeps the
    // semantic role/cost while the raw component still remains available in
    // causalComponents for verification.
    ...currentGameProbeNodes,
    // The browser probe has already resolved this object as the active player
    // root. Keep it ahead of cached descendants such as backpack, skeleton, or
    // transient VFX nodes; otherwise equal-scoring children can become the
    // canonical player and corrupt movement calibration and route progress.
    ...(resolvedPlayer ? [resolvedPlayer] : []),
    // Baseline semantic nodes may retain static pads/facilities when a compact
    // subscription omits their transform. A live guide binding, moving enemy,
    // or guided traversal target is different: it is fresh-only evidence and
    // must disappear when the current game probe no longer observes it.
    ...withoutFreshOnlyGameProbeSemantics(raw?.baselineSemanticNodes),
    ...(raw?.observe?.activeUiNodes || []),
    ...withoutCachedGameProbeSemantics(raw?.observe?.interestingNodes),
    ...(raw?.observe?.managers || []),
    ...withoutCachedGameProbeSemantics(raw?.interestingNodes),
    // Compact/full refreshes keep exact progression components in a
    // dedicated channel. Omitting it here can turn a live payment pad into
    // an opaque guide interaction and erase its remaining requirement.
    ...withoutCachedGameProbeSemantics(raw?.progress_components),
    ...withoutCachedGameProbeSemantics(raw?.components),
    ...withoutCachedGameProbeSemantics(raw?.causalComponents),
    ...activeResourcePositionNodes(raw)
  ]);
}

const MECHANIC_FIELD_RE = /num|count|amount|stock|inventory|capacity|max|min|current|value|price|cost|level|input|output|queue|order|item|fish|coin|money|ready|full|empty|lock|work|process|complete|finish|enable|available|active|state|type|unit|init|put|add|time|timer|progress|show|hide|move|change/i;
const SEMANTIC_STATE_FIELD_RE = /num|count|amount|stock|inventory|capacity|max|min|current|value|price|cost|level|input|output|queue|order|item|fish|coin|money|ready|full|empty|lock|unlock|work|process|complete|finish|available|state|sell|cook|catch/i;
const TRANSIENT_STATE_FIELD_RE = /time|timer|cooldown|animation|anim|show|hide|move|change|elapsed|tick|frame/i;
const MECHANIC_METHOD_RE = /trigger|interact|collect|catch|harvest|pickup|sell|deposit|deliver|cook|process|factory|machine|input|output|inventory|bag|update.*(?:num|count|coin|money|state)|changeState|moveTo/i;

function causalComponentItems(raw) {
  const seen = new Set();
  const items = [];
  for (const item of [...(raw?.causalComponents || []), ...(raw?.components || [])]) {
    const key = `${item?.className || "unknown"}:${pathOf(item) || "unknown"}`;
    if (seen.has(key)) continue;
    seen.add(key);
    items.push(item);
  }
  return items;
}

/**
 * Normalize backend-authored annular navigation walls.
 *
 * Several Cocos playables implement fort/camp boundaries with gameplay
 * components instead of physics Collider components. Those components expose
 * the authoritative ring center, inner/outer diameter, arc coverage,
 * orientation, and lock state. Treating them as ordinary causal telemetry
 * makes the geometry layer report DIRECT_CLEAR even while the actor is
 * visibly walking into a wall.
 *
 * This observer remains game-agnostic: a component qualifies only by its
 * geometry fields, not by a game id or authored node name.
 */
export function inferredRingNavigationBarriers(raw) {
  const sectors = causalComponentItems(raw).flatMap((item) => {
    const fields = {
      ...(item?.numericFields || {}),
      ...(item?.booleanFields || {}),
      ...(item?.primitiveFields || {})
    };
    const innerDiameter = finite(fields._ringInnerDiameter ?? fields.ringInnerDiameter);
    const outerDiameter = finite(fields._ringOuterDiameter ?? fields.ringOuterDiameter);
    const arcAngle = finite(fields._ringArcAngle ?? fields.ringArcAngle);
    const center = worldPoint(item);
    const orientation = finite(item?.nodeRotation?.y ?? item?.rotation?.y ?? 0);
    const className = String(item?.className || "");
    const nodePath = String(pathOf(item) || "");
    const structuralPathHint = /(?:air.?wall|ring|annular|boundary|fort|camp|gate|door)/i.test(nodePath);
    if (!center
      || !/AirWall|Ring.*Wall|Annular.*Wall/i.test(className)
      || innerDiameter === null
      || outerDiameter === null
      || arcAngle === null
      || orientation === null
      || innerDiameter <= 0
      || outerDiameter <= innerDiameter
      || arcAngle <= 0) return [];
    // Many playables attach a small PlayerDynAirWall to every harvestable
    // tree/enemy. Those are local interaction colliders, not a global annular
    // boundary requiring a portal. Treating one duplicate authored tree path
    // as a ring also makes an exact-reference refresh expand to hundreds of
    // components and crowd the player/counters out of the lightweight probe.
    // Large rings remain structural even with opaque authored names; smaller
    // rings require an explicit boundary-like path.
    if (outerDiameter / 2 < 8 && !structuralPathHint) return [];
    const nodeActive = active(item) && fields.active !== false;
    const locked = fields.isLock !== false && fields.locked !== false;
    return [{
      path: pathOf(item) || stableId("ring-sector", item, { includePosition: true }),
      class_name: className,
      center,
      inner_radius: innerDiameter / 2,
      outer_radius: outerDiameter / 2,
      center_angle_degrees: orientation,
      arc_angle_degrees: Math.min(400, arcAngle),
      active: nodeActive,
      locked,
      blocking: nodeActive && locked
    }];
  });

  const groups = [];
  for (const sector of sectors) {
    let group = groups.find((candidate) =>
      Math.abs(candidate.inner_radius - sector.inner_radius) <= 0.1
      && Math.abs(candidate.outer_radius - sector.outer_radius) <= 0.1
      && Math.hypot(candidate.center.x - sector.center.x, candidate.center.z - sector.center.z) <= 0.75
    );
    if (!group) {
      group = {
        center: { ...sector.center },
        inner_radius: sector.inner_radius,
        outer_radius: sector.outer_radius,
        sectors: []
      };
      groups.push(group);
    }
    group.sectors.push(sector);
    const count = group.sectors.length;
    group.center = {
      x: group.sectors.reduce((sum, item) => sum + item.center.x, 0) / count,
      z: group.sectors.reduce((sum, item) => sum + item.center.z, 0) / count
    };
  }

  return groups.map((group) => {
    const blockingSectors = group.sectors.filter((item) => item.blocking);
    const identity = {
      center_x: Number(group.center.x.toFixed(2)),
      center_z: Number(group.center.z.toFixed(2)),
      inner_radius: Number(group.inner_radius.toFixed(2)),
      outer_radius: Number(group.outer_radius.toFixed(2))
    };
    return {
      id: `ring-barrier-${createHash("sha256").update(JSON.stringify(identity)).digest("hex").slice(0, 14)}`,
      kind: "ring_barrier",
      center: {
        x: Number(group.center.x.toFixed(4)),
        z: Number(group.center.z.toFixed(4))
      },
      inner_radius: Number(group.inner_radius.toFixed(4)),
      outer_radius: Number(group.outer_radius.toFixed(4)),
      active: blockingSectors.length > 0,
      locked: blockingSectors.length > 0,
      active_locked_sector_count: blockingSectors.length,
      observed_sector_count: group.sectors.length,
      blocking_arc_coverage_degrees: Number(blockingSectors
        .reduce((sum, item) => sum + Math.min(360, item.arc_angle_degrees), 0)
        .toFixed(3)),
      full_boundary: blockingSectors.some((item) => item.arc_angle_degrees >= 359.5),
      sectors: group.sectors
        .sort((left, right) => left.center_angle_degrees - right.center_angle_degrees
          || String(left.path).localeCompare(String(right.path)))
        .map((item) => ({
          path: item.path,
          class_name: item.class_name,
          center_angle_degrees: item.center_angle_degrees,
          arc_angle_degrees: item.arc_angle_degrees,
          active: item.active,
          locked: item.locked,
          blocking: item.blocking
        })),
      authority: "current_run_backend_ring_geometry"
    };
  }).sort((left, right) => String(left.id).localeCompare(String(right.id)));
}

function configuredNavigationReferenceRole(fieldName, referencePath, aliases = []) {
  const field = String(fieldName || "");
  const path = String(referencePath || "");
  const match = (Array.isArray(aliases) ? aliases : []).find((item) =>
    item
    && typeof item.field === "string"
    && typeof item.path === "string"
    && item.field === field
    && item.path === path
    && ["entry_transition", "exit_transition", "traversal_transition"].includes(item.role)
  );
  return match?.role || null;
}

function navigationReferenceRole(fieldName, referencePath, aliases = []) {
  const configuredRole = configuredNavigationReferenceRole(fieldName, referencePath, aliases);
  if (configuredRole) return configuredRole;
  const field = String(fieldName || "");
  const leaf = String(referencePath || "").split("/").filter(Boolean).at(-1) || "";
  const fieldToken = field.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const leafToken = leaf.replace(/[^a-z0-9]/gi, "").toLowerCase();
  const exactEntryField = /^(?:enter|entry)Node$/i.test(field)
    || /^(?:tiaoshui|jump|board|dock)(?:Node|Target|Point)?$/i.test(field);
  // Some games prefix an entry reference with the referenced node's own
  // semantic name (for example yuchuanenterNode -> .../yuchuan).  Require the
  // prefix to exactly match the reference leaf; a loose /enter/ test would
  // incorrectly promote unrelated fields such as centerNode.
  const leafQualifiedEntryField = leafToken.length >= 3
    && (fieldToken === `${leafToken}enternode` || fieldToken === `${leafToken}entrynode`);
  const semanticNode = /(?:^|[-_])(?:enter|entry|entrance|jump|tiaoshui|board|dock|portal|teleport|lift|elevator)(?:$|[-_])/i.test(leaf)
    || /^(?:enter|entry|entrance|jump|tiaoshui|board|dock|portal|teleport|lift|elevator)/i.test(leaf);
  if (!exactEntryField && !leafQualifiedEntryField && !semanticNode) return null;
  const token = `${field} ${leaf}`;
  if (/exit|leave|return/i.test(token)) return "exit_transition";
  if (/portal|teleport|lift|elevator/i.test(token)) return "traversal_transition";
  return "entry_transition";
}

function corroboratesNavigationReference(fieldName, referencePath) {
  const fieldToken = String(fieldName || "").replace(/[^a-z0-9]/gi, "").toLowerCase();
  const leafToken = String(referencePath || "").split("/").filter(Boolean).at(-1)?.replace(/[^a-z0-9]/gi, "").toLowerCase() || "";
  // A plain <leaf>Node reference has no navigation meaning by itself.  It may
  // only corroborate an independently seeded semantic reference to the exact
  // same backend path.
  return leafToken.length >= 3 && fieldToken === `${leafToken}node`;
}

function inferredNavigationMechanisms(raw, obstacles = [], config = {}) {
  const obstacleByPath = new Map();
  for (const obstacle of obstacles || []) {
    // Navigation references bind to authored node paths, while obstacle ids
    // are geometry-instance scoped.  Never use the instance id as the
    // semantic lookup key.
    const obstaclePath = obstacle?.path || obstacle?.id;
    if (!obstaclePath) continue;
    const previous = obstacleByPath.get(obstaclePath);
    // Cocos commonly reports both RigidBody and Collider components for one
    // node. Treat the node as an active collider when any component is active
    // and retain the component carrying finite geometry.
    if (!previous
      || (previous.active === false && obstacle.active !== false)
      || ([obstacle.minX, obstacle.maxX, obstacle.minZ, obstacle.maxZ].every((value) => finite(value) !== null)
        && ![previous.minX, previous.maxX, previous.minZ, previous.maxZ].every((value) => finite(value) !== null))) {
      obstacleByPath.set(obstaclePath, obstacle);
    }
  }
  const byPath = new Map();
  const references = [];
  for (const component of causalComponentItems(raw)) {
    if (!active(component)) continue;
    // Probe object references are normally stored in objectRefs. Retain the
    // primitiveFields fallback for third-party probes that inline compact
    // node references there.
    const referenceFields = new Map([
      ...Object.entries(component?.primitiveFields || {}),
      ...Object.entries(component?.objectRefs || {})
    ]);
    for (const [field, reference] of referenceFields) {
      if (!reference || typeof reference !== "object" || Array.isArray(reference)) continue;
      const referencePath = pathOf(reference);
      const referencePosition = worldPoint(reference);
      const configuredRole = configuredNavigationReferenceRole(
        field,
        referencePath,
        config.navigation_reference_aliases
      );
      const role = configuredRole || navigationReferenceRole(
        field,
        referencePath,
        config.navigation_reference_aliases
      );
      if (!referencePath || !referencePosition) continue;
      const matchingObstacle = obstacleByPath.get(referencePath) || null;
      const source = {
        component_path: pathOf(component),
        component_class: component?.className || null,
        field,
        reference_active: active(reference),
        method_names: (component?.methodNames || [])
          .filter((name) => /enter|entry|jump|board|dock|exit|leave|return|portal|teleport|lift|elevator/i.test(String(name)))
          .map(String)
          .slice(0, 8)
      };
      references.push({
        field,
        reference,
        referencePath,
        referencePosition,
        role,
        configuredRole,
        matchingObstacle,
        source
      });
    }
  }
  const mergeReference = ({
    reference,
    referencePath,
    referencePosition,
    role,
    configuredRole = null,
    matchingObstacle,
    source
  }) => {
      const existing = byPath.get(referencePath) || {
        path: referencePath,
        name: reference?.name || reference?.nodeName || referencePath.split("/").at(-1) || null,
        active: matchingObstacle ? matchingObstacle.active !== false : active(reference),
        role,
        position: referencePosition,
        screen_position: screenPoint(reference),
        collider_id: matchingObstacle ? referencePath : null,
        source_fields: []
      };
      if (!existing.source_fields.some((item) => item.component_path === source.component_path
        && item.component_class === source.component_class
        && item.field === source.field)) existing.source_fields.push(source);
      if (existing.role !== role) existing.role = "traversal_transition";
      if (!existing.screen_position && screenPoint(reference)) existing.screen_position = screenPoint(reference);
      if (matchingObstacle) {
        existing.collider_id = referencePath;
        existing.active = matchingObstacle.active !== false;
        if (configuredRole && matchingObstacle.active !== false) {
          // A per-game semantic alias plus the independently observed active
          // collider are two current-run evidence channels. Keep the backend
          // reference count literal while exposing the collider corroboration
          // separately so ordinary one-source Node fields remain insufficient.
          existing.active_collider_confirmed = true;
        }
      }
      byPath.set(referencePath, existing);
  };
  // First establish mechanisms only from independently semantic references.
  for (const item of references) {
    if (item.role) mergeReference(item);
  }
  // Then add exact same-path <leaf>Node references as corroboration.  This
  // raises confidence without granting a generic node reference authority to
  // create a mechanism on its own.
  for (const item of references) {
    if (item.role || !byPath.has(item.referencePath)
      || !corroboratesNavigationReference(item.field, item.referencePath)) continue;
    mergeReference({ ...item, role: byPath.get(item.referencePath).role });
    }
  return [...byPath.values()]
    .map((item) => ({
      ...item,
      source_fields: item.source_fields
        .sort((left, right) => String(left.component_path || "").localeCompare(String(right.component_path || ""))
          || String(left.component_class || "").localeCompare(String(right.component_class || ""))
          || String(left.field).localeCompare(String(right.field)))
        .slice(0, 8),
      reference_count: item.source_fields.length,
      confidence: item.source_fields.length >= 2 || item.active_collider_confirmed === true ? 0.99 : 0.92,
      authority: "current_run_backend_node_reference"
    }))
    .sort((left, right) => String(left.path).localeCompare(String(right.path)));
}

function safeMechanicFields(item) {
  const entries = [];
  for (const [key, value] of Object.entries(item?.primitiveFields || {})) {
    if (!key || /^__/.test(key) || ["_enabled", "_sceneGetter", "_objFlags", "_name", "_id"].includes(key) || !MECHANIC_FIELD_RE.test(key.replace(/^_+/, ""))) continue;
    if (typeof value === "number" && !Number.isFinite(value)) continue;
    if (!["string", "number", "boolean"].includes(typeof value) && value !== null) continue;
    entries.push([key, typeof value === "string" ? value.slice(0, 120) : value]);
  }
  entries.sort(([left], [right]) => {
    const leftScore = (SEMANTIC_STATE_FIELD_RE.test(left) && !TRANSIENT_STATE_FIELD_RE.test(left) ? 4 : 0) + (left.startsWith("_") ? 0 : 1);
    const rightScore = (SEMANTIC_STATE_FIELD_RE.test(right) && !TRANSIENT_STATE_FIELD_RE.test(right) ? 4 : 0) + (right.startsWith("_") ? 0 : 1);
    return rightScore - leftScore || left.localeCompare(right);
  });
  return Object.fromEntries(entries.slice(0, 40));
}

function semanticStateFields(fields) {
  return Object.fromEntries(Object.entries(fields)
    .filter(([key]) => SEMANTIC_STATE_FIELD_RE.test(key) && !TRANSIENT_STATE_FIELD_RE.test(key))
    .sort(([left], [right]) => left.localeCompare(right)));
}

function quantityField(key) {
  if (/^(?:init|unit|add)/i.test(key)) return false;
  return /num|count|amount|stock|inventory|capacity|max|min|current|value|input|output|queue|item|fish|coin|money/i.test(key)
    && !/price|cost|time|timer|rate|speed/i.test(key);
}

function mechanicStateForTarget(targetPath, components, { includeDescendants = true } = {}) {
  const candidates = [];
  for (const item of components) {
    const nodePath = pathOf(item);
    if (!nodePath || !(nodePath === targetPath || (includeDescendants && isDescendantPath(nodePath, targetPath)))) continue;
    const fields = safeMechanicFields(item);
    const methodNames = (item?.methodNames || []).filter((name) => MECHANIC_METHOD_RE.test(String(name))).slice(0, 16);
    const className = item?.className || null;
    const stateFields = semanticStateFields(fields);
    if (Object.keys(stateFields).length === 0 && methodNames.length === 0) continue;
    const score = (nodePath === targetPath ? 50 : 0)
      + (!/^cc\./i.test(String(className || "")) ? 30 : 0)
      + Object.keys(fields).length * 2
      + Object.keys(stateFields).length * 3
      + methodNames.length;
    candidates.push({
      score,
      class_name: className,
      path: nodePath,
      enabled: item?.enabled !== false && item?.nodeActive !== false,
      fields,
      state_fields: stateFields,
      method_names: methodNames
    });
  }
  candidates.sort((left, right) => right.score - left.score || String(left.path).localeCompare(String(right.path)) || String(left.class_name).localeCompare(String(right.class_name)));
  const selected = candidates.slice(0, 8).map(({ score, ...item }) => item);
  if (selected.length === 0) return null;
  const quantityFields = selected.flatMap((component) => Object.entries(component.fields)
    .filter(([key, value]) => quantityField(key) && typeof value === "number" && Number.isFinite(value))
    .map(([key, value]) => ({ field: `${component.class_name || "component"}@${component.path}:${key}`, value })));
  const stateShape = selected.map((component) => ({
    class_name: component.class_name,
    path: component.path,
    enabled: component.enabled,
    state_fields: component.state_fields
  }));
  return {
    state_signature: createHash("sha256").update(JSON.stringify(stateShape)).digest("hex"),
    components: selected,
    signals: {
      component_count: selected.length,
      quantity_field_count: quantityFields.length,
      zero_quantity_fields: quantityFields.filter((item) => item.value === 0).map((item) => item.field),
      positive_quantity_fields: quantityFields.filter((item) => item.value > 0).map((item) => ({ field: item.field, value: item.value })),
      all_observed_quantities_zero: quantityFields.length > 0 && quantityFields.every((item) => item.value === 0)
    }
  };
}

function playerScore(item, config = {}) {
  const value = text(item);
  let score = 0;
  if (/PlayerController|MainPlayer|PlayerState|\bPlayer\b|\bActor\b|Avatar|Pawn|主角/i.test(value)) score += 5;
  if (matchesPlayerAlias(item, config.player_aliases)) score += 6;
  if (/controller|character|role/i.test(value)) score += 2;
  if (/Canvas|UI|Label|Button|Manager|Pool|Preload/i.test(value)) score -= 3;
  if (worldPoint(item)) score += 2;
  if (!active(item)) score -= 5;
  return score;
}

function completionSignals(raw, nodes, config = {}) {
  const completion = raw?.completion || raw?.observe?.completion || {};
  const signals = [];
  const authoritativeGameCompletion = raw?.gameProbe?.game_state?.terminal?.completion_authoritative === true;
  const terminalEvidence = completion?.endState?.signals || completion?.signals || [];
  const uiTransitionFalsePositive = terminalEvidence.length > 0
    && terminalEvidence.every((item) => item?.type === "managerFlag"
      && /^cc\./i.test(String(item?.name || ""))
      && /_transitionFinished$/i.test(String(item?.name || "")))
    && (completion?.activeEndNodes || []).length === 0;
  // Some playables keep an install/download CTA mounted from the first frame.
  // Their generic page observer can therefore report done/win before gameplay
  // starts. A sandboxed game normalizer may explicitly own terminal
  // observation; in that mode only its completion_candidates are accepted.
  // The normalizer still cannot choose an action or declare completion without
  // returning a concrete candidate, and settled-completion remains a separate
  // multi-sample gate.
  if (!authoritativeGameCompletion) {
    // Download/ad CTA false positive: doneReason pointing at a download /
    // install / ad / logo panel is not a real completion.
    const doneReason = String(raw?.observe?.doneReason || "");
    const downloadCtaFalsePositive = /(?:download|install|ad\b|advert|store|market|logo_panel|logo_btn|cta)/i.test(doneReason)
      && !/(?:WinPanel|Victory|SuccessPanel|CompletePanel|CompletedPanel|ENDCARD|endCard)/i.test(doneReason);
    for (const key of ["done", "win", "runCompleted", "completed", "success"]) {
      if (!uiTransitionFalsePositive && !downloadCtaFalsePositive
        && (completion?.[key] === true || raw?.fast?.[key] === true)) signals.push(`probe:${key}`);
    }
    for (const item of nodes) {
      const value = text(item);
      if (active(item) && /(?:^|\/|\b)(WinPanel|Victory|SuccessPanel|CompletePanel|CompletedPanel)(?:$|\/|\b)/i.test(value) && !/Pool|Preload/i.test(value)) {
        signals.push(`node:${pathOf(item) || value.slice(0, 120)}`);
      }
      if (active(item) && matchesAlias(value, config.completion_aliases)) signals.push(`configured-node:${pathOf(item) || value.slice(0, 120)}`);
    }
  }
  for (const candidate of raw?.gameProbe?.completion_candidates || []) {
    if (typeof candidate === "string" && candidate.trim()) signals.push(`game-normalizer:${candidate.trim()}`);
  }
  return [...new Set(signals)];
}

function failureSignals(raw, nodes, config = {}) {
  const signals = [];
  const observeDoneReason = String(raw?.observe?.doneReason || "");
  const observeConfirmedFailure = raw?.observe?.done === true
    && raw?.observe?.win === false
    && /(?:fail|lose|defeat|game.?over|dead|death|revive|retry|复活|失败|重试|死亡)/i.test(observeDoneReason);
  // The full page observer already adjudicates active terminal UI before any
  // game-local semantics run. Preserve an explicit loss here as a generic
  // safety net: a buggy game normalizer must never be able to erase a
  // confirmed failure and let resource or guide deltas authorize more input.
  if (observeConfirmedFailure) {
    const path = observeDoneReason.match(/active UI\s+(.+)$/i)?.[1] || null;
    signals.push({
      type: "observe_terminal_failure",
      path,
      text: observeDoneReason.slice(0, 180)
    });
  }
  for (const item of nodes) {
    const value = text(item);
    if (active(item) && /(?:^|\/|\b)(FailPanel|LosePanel|GameOver|TryAgain)(?:$|\/|\b)/i.test(value) && !/Pool|Preload/i.test(value)) {
      signals.push({ type: "failure_ui", path: pathOf(item), text: value.slice(0, 180) });
    }
    if (active(item) && matchesAlias(value, config.failure_aliases)) signals.push({ type: "configured_failure_ui", path: pathOf(item), text: value.slice(0, 180) });
  }
  if (Array.isArray(raw?.failureEvidence)) signals.push(...raw.failureEvidence);
  // Game-local normalizers can expose authoritative failures that the broad
  // observer cannot infer yet (for example, a combat component reporting
  // hp<=0 before the failure overlay mounts). Preserve that evidence in the
  // canonical failure channel so the orchestration hard-stop sees it.
  if (Array.isArray(raw?.gameProbe?.failure_evidence)) {
    signals.push(...raw.gameProbe.failure_evidence);
  }
  return signals;
}

function targets(nodes, config = {}, {
  playerPath = null,
  viewport = null,
  obstacles = [],
  mechanicComponents = [],
  explicitlyUnreadyResourcePaths = new Set()
} = {}) {
  const candidates = [];
  const positionsByPath = new Map();
  for (const item of nodes) {
    const nodePath = pathOf(item);
    const positionIdentity = instancePositionIdentity(item);
    if (!nodePath || !positionIdentity || !active(item)) continue;
    if (!positionsByPath.has(nodePath)) positionsByPath.set(nodePath, new Set());
    positionsByPath.get(nodePath).add(positionIdentity);
  }
  for (const item of nodes) {
    if (!active(item) || !worldPoint(item)) continue;
    // Some games keep authored resource placeholders active while explicitly
    // marking their gameplay component unready. Treat readiness as the
    // authoritative actionability signal even when the scene node remains
    // mounted and semantically named like a pickup.
    if (item?.primitiveFields?.isReady === false
      || item?.primitiveFields?.ready === false) continue;
    const nodePath = pathOf(item);
    // Readiness may only exist on the authoritative money-resource record
    // while interestingNodes/causalComponents contain a shallower duplicate.
    // Filter every duplicate that resolves to the same backend path.
    if (explicitlyUnreadyResourcePaths.has(nodePath)) continue;
    if (isUiNode(item) || isDescendantPath(nodePath, playerPath)) continue;
    // Game-local probes may know that a semantically named scene root is
    // infrastructure rather than an actionable instance. Exact exclusions
    // prevent roots such as tree_node, AreaStickerMgr, or RewardMgr at (0,0)
    // from becoming false objectives while leaving their real descendants
    // eligible for discovery.
    if (matchesTargetExclusion(item, config.target_exclusions)) continue;
    const semantics = semanticKind(item, config);
    if (!semantics) continue;
    const observedResourcePickup = isCollectibleResourceNode(item);
    const gameProbeSemantic = isGameProbeSemanticNode(item);
    if (isInfrastructureRoot(item) && semantics.source !== "configured_alias" && !observedResourcePickup && !gameProbeSemantic) continue;
    if (hasInfrastructureAncestor(item) && semantics.source !== "configured_alias" && !observedResourcePickup && !gameProbeSemantic) continue;
    const components = [...new Set([item?.className, ...(item?.components || [])].filter(Boolean))];
    candidates.push({
      id: stableId(semantics.kind, item, {
        includePosition: (positionsByPath.get(nodePath)?.size || 0) > 1
      }),
      kind: semantics.kind,
      path: pathOf(item),
      name: item?.name || item?.nodeName || null,
      components,
      role_hint: roleHint(item, semantics.kind),
      required_resource: item?.primitiveFields?.required_resource
        || item?.stringFields?.required_resource
        || (semantics.kind === "money" ? "money" : null)
        || null,
      dynamic_target: item?.primitiveFields?.dynamic_target === true
        || item?.observed_state?.dynamic_target === true
        || (item?.tags || []).some((tag) => String(tag).toLowerCase() === "dynamic_resource_target"),
      observed_resource_pickup: observedResourcePickup,
      active: active(item),
      navigable: true,
      position: worldPoint(item),
      screen_position: screenPoint(item),
      screen_visible: screenVisible(item, viewport),
      value: numericLabel(item),
      discovery_confidence: semanticStrength(item, semantics.source),
      candidate: item
    });
  }
  const canonical = candidates.filter((candidate) => {
    const priority = semanticTargetAnchorPriority(candidate);
    const shadowedByAncestor = candidates.some((other) => {
      if (other === candidate || !isDescendantPath(candidate.path, other.path)) return false;
      // A synthesized RssItem/MoneyPickupCluster is a measured physical
      // resource, not a decorative price icon. Keep it actionable even when
      // it lives below a counter/payment facility with a different semantic
      // role; ordinary ungrounded descendants remain shadowed as before.
      if (candidate.observed_resource_pickup === true) return false;
      const otherPriority = semanticTargetAnchorPriority(other);
      const sameSemanticFamily = other.kind === candidate.kind && other.role_hint === candidate.role_hint;
      // Descendants with a different meaning (for example a price icon below
      // a station root) remain non-canonical. Only a strictly fresher anchor
      // for the same semantic family may replace its broad parent.
      return !sameSemanticFamily || otherPriority >= priority;
    });
    if (shadowedByAncestor) return false;
    const replacedByStrongerDescendant = candidates.some((other) => other !== candidate
      && other.kind === candidate.kind
      && other.role_hint === candidate.role_hint
      && isDescendantPath(other.path, candidate.path)
      && semanticTargetAnchorPriority(other) > priority);
    return !replacedByStrongerDescendant;
  });
  const selected = [];
  for (const candidate of [...canonical].sort((a, b) => b.discovery_confidence - a.discovery_confidence || String(a.path).localeCompare(String(b.path)))) {
    const duplicate = selected.some((current) => current.kind === candidate.kind
      && current.role_hint === candidate.role_hint
      && Math.hypot(current.position.x - candidate.position.x, current.position.z - candidate.position.z) <= 0.75);
    if (!duplicate) selected.push(candidate);
  }
  return selected
    .map((candidate) => {
      const anchors = interactionAnchors(candidate, nodes, viewport, mechanicComponents);
      const selectedAnchor = anchors[0];
      const transitionCorridor = pairedTransitionCorridor(
        candidate,
        mechanicComponents,
        selectedAnchor.position
      );
      return {
        ...candidate,
        semantic_position: candidate.position,
        position: selectedAnchor.position,
        // A synthesized money cluster denotes live physical pickups, not the
        // broad counter/facility trigger that owns them.  Entering a generic
        // 0.8 interaction envelope can therefore declare arrival before the
        // actor touches any coin.  Keep both the route-state predicate and
        // the following dwell intent on the same collision-level contract so
        // automatic pickup cannot be missed between those two FSM states.
        ...(candidate.observed_resource_pickup === true && candidate.kind === "money"
          ? {
              interaction_entry_policy: "deep_entry",
              interaction_entry_tolerance: 0.16
            }
          : {}),
        screen_position: selectedAnchor.screen_position || candidate.screen_position,
        screen_visible: selectedAnchor.screen_visible || candidate.screen_visible,
        interaction_anchor: {
          path: selectedAnchor.path,
          position: selectedAnchor.position,
          source: selectedAnchor.source,
          confidence: selectedAnchor.source === "paired_transition_midpoint"
            ? 0.99
            : (selectedAnchor.source === "ground_interaction_marker"
                ? 0.98
                : (selectedAnchor.source === "interaction_descendant" ? 0.85 : candidate.discovery_confidence))
        },
        ...(transitionCorridor ? { transition_corridor: transitionCorridor } : {}),
        interaction_bounds: interactionBoundsForAnchor(selectedAnchor, obstacles),
        anchor_candidates: anchors.slice(0, 8).map(({ score, ...anchor }) => anchor),
        mechanic_state: mechanicStateForTarget(candidate.path, mechanicComponents)
      };
    })
    .sort((a, b) => String(a.path).localeCompare(String(b.path)))
    .slice(0, 180);
}

function resourceCounters(raw, nodes) {
  const counters = {};
  // Do not rely only on allNodes() here. Its path/position dedupe correctly
  // favors the game-probe semantic node for navigation, but a shallow copy of
  // the same UI path may omit the cc.Label string carried by the raw component
  // channel. Counter extraction is value-oriented, so inspect every fresh UI
  // channel and let the numeric observation for a path win.
  const counterNodes = [
    ...nodes,
    ...(raw?.observe?.activeUiNodes || []),
    ...(raw?.components || []),
    ...(raw?.causalComponents || [])
  ];
  for (const item of counterNodes) {
    const nodePath = pathOf(item);
    if (!nodePath || !active(item) || !isUiNode(item)) continue;
    const componentIdentity = [item?.className, ...(item?.components || [])].filter(Boolean).join(" ");
    const opaqueInventorySlotCounter = /(?:^|\/)Item\/[^/]+\/[^/]+$/i.test(nodePath)
      && /(?:^|\s|\.)Label(?:\s|$)/i.test(componentIdentity);
    if (!opaqueInventorySlotCounter
      && !/Money|Coin|Cash|Gold|Fish|Resource|Currency|Score|Count/i.test(text(item))) continue;
    const value = numericLabel(item);
    if (value !== null) counters[nodePath] = value;
  }
  for (const resource of raw?.moneyResources?.resources || []) {
    for (const descendant of resource?.activeMoneyDescendants || []) {
      const value = numericLabel(descendant);
      if (value === null) continue;
      const key = descendant?.path || `${pathOf(resource) || "resource"}/${descendant?.name || descendant?.nodeName || "counter"}`;
      counters[key] = value;
    }
  }
  for (const [key, value] of Object.entries(raw?.gameProbe?.counters || {})) {
    const number = finite(value);
    if (number !== null) counters[`game_probe:${key}`] = number;
  }
  return Object.fromEntries(Object.entries(counters).sort(([a], [b]) => a.localeCompare(b)));
}

function derivedArrowTargetPath(target, targetPosition) {
  if (!targetPosition) return null;
  const identity = JSON.stringify({
    // The name is only an opaque object-reference discriminator. It is never
    // mapped to a resource or action semantic.
    name: target?.name || target?.nodeName || null,
    x: Number(targetPosition.x.toFixed(3)),
    z: Number(targetPosition.z.toFixed(3))
  });
  const digest = createHash("sha256").update(identity).digest("hex").slice(0, 14);
  return `/game-probe/derived/backend-arrow-target/${digest}`;
}

function inferredBackendArrowGuide(raw) {
  const candidates = causalComponentItems(raw).flatMap((item) => {
    if (!active(item)) return [];
    const refs = item?.objectRefs || {};
    const target = refs.targetNode || refs.guideTarget || refs.currentTarget || null;
    const targetPosition = worldPoint(target);
    const exactTargetPath = pathOf(target);
    const targetPath = exactTargetPath || derivedArrowTargetPath(target, targetPosition);
    if (!targetPath || !targetPosition || !active(target)) return [];

    const marker = refs.jianTouNode || refs.arrowNode || refs.guideNode || null;
    const arrowController = refs.arrCon || refs.arrowController || null;
    const fields = {
      ...(item?.primitiveFields || {}),
      ...(item?.booleanFields || {})
    };
    const methods = (item?.methodNames || []).join(" ");
    const markerActive = marker?.active === true || marker?.nodeActive === true;
    const controllerActive = arrowController?.active === true || arrowController?.nodeActive === true;
    const guideStateActive = fields.isJianTouMoving === true
      || fields.isGuideMoving === true
      || fields.guideActive === true;
    const guideApiPresent = /(?:checkJianTouTarget|setTargetNode|clearJianTouTargetNode|setGuide)/i.test(methods);
    if (!guideApiPresent || !(markerActive || (controllerActive && guideStateActive))) return [];

    const score = Number(markerActive) * 4
      + Number(controllerActive) * 2
      + Number(guideStateActive) * 2
      + Number(guideApiPresent);
    return [{
      score,
      guide: {
        status: "backend_world_target",
        target_path: targetPath,
        target_name: target?.name || target?.nodeName || null,
        // These are intentionally opaque interaction semantics. Neither the
        // asset name nor its containing folder is accepted as a resource label.
        target_kind: "interaction",
        target_role: "interact",
        target_position: targetPosition,
        marker_path: pathOf(marker),
        actionable: true,
        activation_authoritative: true,
        confidence: Math.min(0.99, 0.88 + (score * 0.01)),
        source: exactTargetPath
          ? "active_backend_actor_arrow_binding"
          : "active_backend_actor_arrow_position_binding",
        semantic_identity_status: exactTargetPath
          ? "unresolved"
          : "position_grounded_unresolved",
        semantic_authority: "controlled_before_after_required",
        // Some games expose automatic combat/harvest only on the actor that
        // owns the exact backend arrow binding. Treat this as a transaction
        // lease, not as semantic proof about the target or its output.
        interaction_in_progress: fields.isAttacking === true,
        interaction_progress_reason: fields.isAttacking === true
          ? "actor_attacking_bound_target"
          : null
      }
    }];
  });
  return candidates.sort((left, right) => right.score - left.score)[0]?.guide || null;
}

function normalizedGameState(raw, discoveredTargets, obstacles = [], config = {}) {
  const source = raw?.gameProbe?.game_state;
  const state = source && typeof source === "object" && !Array.isArray(source)
    ? structuredClone(source)
    : {};
  const inferredArrowGuide = inferredBackendArrowGuide(raw);
  const explicitGuideStatus = String(state?.current_guide?.status || "");
  const explicitTerminalGuide = ["terminal_completion", "terminal_failure"].includes(explicitGuideStatus);
  if (!state?.current_guide?.target_path && inferredArrowGuide && !explicitTerminalGuide) {
    state.current_guide = {
      ...(state.current_guide || {}),
      ...inferredArrowGuide
    };
  }
  const inferredRingBarriers = inferredRingNavigationBarriers(raw);
  if (inferredRingBarriers.length > 0) {
    const barrierMap = new Map(
      (Array.isArray(state.navigation_barriers) ? state.navigation_barriers : [])
        .filter((item) => item?.id)
        .map((item) => [item.id, item])
    );
    for (const barrier of inferredRingBarriers) {
      barrierMap.set(barrier.id, {
        ...(barrierMap.get(barrier.id) || {}),
        ...barrier
      });
    }
    state.navigation_barriers = [...barrierMap.values()]
      .sort((left, right) => String(left.id).localeCompare(String(right.id)));
  }
  const activationObservation = (item) => {
    const observed = item?.observed_state || {};
    const authoritative = item?.activation_authoritative === true
      || observed.activation_authoritative === true;
    const explicitlyInactive = item?.active === false
      || observed.visible === false
      || observed.unlocked === false;
    return { authoritative, explicitlyInactive };
  };
  for (const upgrade of state.upgrades || []) {
    const target = discoveredTargets.find((item) => item.path === upgrade?.path);
    if (!target) continue;
    const activation = activationObservation(upgrade);
    if (activation.authoritative) {
      target.activation_authoritative = true;
      target.active = !activation.explicitlyInactive;
      if (activation.explicitlyInactive) {
        target.navigable = false;
        target.interaction_ready = false;
        target.readiness_reason = upgrade.observed_state?.readiness_reason || "backend_node_inactive";
      }
    }
    if (upgrade.observed_state?.interaction_ready === false) {
      target.navigable = false;
      target.interaction_ready = false;
      target.readiness_reason = upgrade.observed_state?.readiness_reason || "interaction_not_ready";
    } else if (upgrade.observed_state?.interaction_ready === true) {
      target.navigable = true;
      target.interaction_ready = true;
      target.readiness_reason = null;
    }
    if (finite(upgrade.remaining_cost) !== null) target.value = finite(upgrade.remaining_cost);
    target.role_hint = upgrade.terminal_trigger === true ? "terminal_trigger" : "upgrade";
    target.required_resource = upgrade.required_resource || target.required_resource || null;
    if (upgrade.terminal_trigger === true) {
      target.terminal_trigger = true;
      target.collider_group = finite(upgrade.collider_group);
      target.value = null;
    }
    target.discovery_confidence = Math.max(Number(target.discovery_confidence || 0), Number(upgrade.confidence || 0));
  }
  for (const station of state.stations || []) {
    const target = discoveredTargets.find((item) => item.path === station?.path);
    if (!target) continue;
    const activation = activationObservation(station);
    if (activation.authoritative) {
      target.activation_authoritative = true;
      target.active = !activation.explicitlyInactive;
      if (activation.explicitlyInactive) target.navigable = false;
    }
    if (station.role) target.role_hint = station.role;
    if (station.observed_state?.visible === false || station.observed_state?.unlocked === false) target.active = false;
    if (station.observed_state?.interaction_ready === false) {
      target.navigable = false;
      target.interaction_ready = false;
      target.readiness_reason = station.observed_state?.readiness_reason || "interaction_not_ready";
    } else if (station.observed_state?.interaction_ready === true) {
      target.navigable = true;
      target.interaction_ready = true;
      target.readiness_reason = null;
    }
    // A facility component root is not necessarily its walk-in trigger. Game
    // probes may ground a child collider as the authoritative interaction
    // anchor while keeping the stable parent path for semantic identity. Use
    // that measured point for routing and retain the component root only as
    // semantic_position. This prevents enclosure classification and path
    // planning from steering toward a wall-side implementation origin.
    const observedInteractionAnchor = worldPoint({
      position: station.observed_state?.interaction_anchor_position
        || station.interaction_anchor_position
    });
    if (observedInteractionAnchor) {
      target.semantic_position = target.semantic_position || target.position;
      target.position = observedInteractionAnchor;
      target.interaction_anchor = {
        path: station.observed_state?.interaction_anchor_path
          || station.interaction_anchor_path
          || target.path,
        position: observedInteractionAnchor,
        source: "game_probe_measured_interaction_anchor",
        confidence: 0.99
      };
      // Re-ground the interaction envelope after a game probe replaces a
      // facility's semantic root with its measured child trigger. The generic
      // target pass may have resolved the parent before this authoritative
      // station fact was merged, leaving `interaction_bounds` null. Routing to
      // the trigger centre is unsafe when that centre overlaps a fence skin;
      // routing to the reachable edge of the measured trigger is both
      // collision-safe and sufficient to fire the automatic interaction.
      target.interaction_bounds = interactionBoundsForAnchor(
        target.interaction_anchor,
        obstacles
      ) || target.interaction_bounds || null;
    }
    if (typeof station.observed_state?.interaction_entry_policy === "string") {
      target.interaction_entry_policy = station.observed_state.interaction_entry_policy;
    }
    const observedEntryTolerance = finite(station.observed_state?.interaction_entry_tolerance);
    if (observedEntryTolerance !== null && observedEntryTolerance > 0) {
      target.interaction_entry_tolerance = observedEntryTolerance;
    }
    if (typeof station.observed_state?.interaction_reentry_policy === "string") {
      target.interaction_reentry_policy = station.observed_state.interaction_reentry_policy;
    }
    const observedReentryWaypoint = worldPoint({
      position: station.observed_state?.interaction_reentry_exit_waypoint
    });
    if (observedReentryWaypoint) target.interaction_reentry_exit_waypoint = observedReentryWaypoint;
    const observedReentryTolerance = finite(station.observed_state?.interaction_reentry_exit_tolerance);
    if (observedReentryTolerance !== null && observedReentryTolerance > 0) {
      target.interaction_reentry_exit_tolerance = observedReentryTolerance;
    }
    if (station.observed_state?.interaction_in_progress === true) {
      target.interaction_in_progress = true;
      target.interaction_progress_reason = station.observed_state?.interaction_progress_reason || "backend_transaction_in_progress";
    } else if (station.observed_state?.interaction_in_progress === false) {
      target.interaction_in_progress = false;
      target.interaction_progress_reason = null;
    }
    const observedOutputCount = station.observed_state?.output_count;
    if (observedOutputCount != null && finite(observedOutputCount) !== null) {
      target.output_count = finite(observedOutputCount);
    }
    target.discovery_confidence = Math.max(Number(target.discovery_confidence || 0), 0.95);
  }
  const guidePath = state?.current_guide?.target_path || null;
  if (guidePath) {
    let target = discoveredTargets.find((item) => item.path === guidePath) || null;
    const authoritativeGuide = ["resolved", "backend_world_target"].includes(
      String(state.current_guide.status || "")
    ) || (state.current_guide.actionable === true
      && state.current_guide.activation_authoritative !== false);
    // Some Cocos games activate the authoritative backend guide one render
    // tick before the guided upgrade root reports activeInHierarchy.  The
    // guide still supplies an exact path and world position, so preserve that
    // actionable handoff instead of converting it into guide_absent.  This is
    // an observation fallback only: strategy ownership remains with Codex and
    // the target disappears again as soon as the backend guide does.
    const semanticGuideTarget = [...(state.upgrades || []), ...(state.stations || [])]
      .find((item) => item?.path === guidePath) || null;
    const guideActivation = activationObservation(semanticGuideTarget);
    const authoritativeInactiveGuide = guideActivation.authoritative
      && guideActivation.explicitlyInactive;
    // Semantic station/upgrade observations carry the interaction root's
    // world coordinates. Prefer them over an arrow child's transient local
    // origin, which can be (0, 0) during an unlock camera handoff.
    const guidePosition = worldPoint({ position: semanticGuideTarget?.position })
      || worldPoint({ position: state.current_guide.target_position });
    if (!target && authoritativeGuide && guidePosition && !authoritativeInactiveGuide) {
      const kind = String(state.current_guide.target_kind || "interaction");
      const observedState = semanticGuideTarget?.observed_state || {};
      const interactionReady = observedState.interaction_ready;
      const terminalTrigger = semanticGuideTarget?.terminal_trigger === true
        || state.current_guide.target_role === "terminal_trigger";
      target = {
        id: stableId(kind, { path: guidePath }),
        kind,
        path: guidePath,
        name: state.current_guide.target_name || guidePath.split("/").at(-1) || null,
        components: [],
        role_hint: state.current_guide.target_role || (kind === "upgrade" ? "upgrade" : "interact"),
        active: true,
        // A resolved guide can precede the station root's discoverability,
        // but it must not erase an explicit readiness gate. A one-shot pickup
        // can become permanently useless if entered before its output is ready.
        navigable: interactionReady !== false,
        interaction_ready: interactionReady === true ? true : (interactionReady === false ? false : null),
        interaction_entry_policy: typeof observedState.interaction_entry_policy === "string"
          ? observedState.interaction_entry_policy
          : null,
        interaction_entry_tolerance: finite(observedState.interaction_entry_tolerance),
        interaction_reentry_policy: typeof observedState.interaction_reentry_policy === "string"
          ? observedState.interaction_reentry_policy
          : null,
        interaction_reentry_exit_waypoint: worldPoint({
          position: observedState.interaction_reentry_exit_waypoint
        }),
        interaction_reentry_exit_tolerance: finite(observedState.interaction_reentry_exit_tolerance),
        interaction_in_progress: observedState.interaction_in_progress === true
          || state.current_guide.interaction_in_progress === true,
        interaction_progress_reason: observedState.interaction_in_progress === true
          || state.current_guide.interaction_in_progress === true
          ? (observedState.interaction_progress_reason
            || state.current_guide.interaction_progress_reason
            || "backend_transaction_in_progress")
          : null,
        readiness_reason: interactionReady === false
          ? (observedState.readiness_reason || "interaction_not_ready")
          : null,
        output_count: observedState.output_count == null ? null : finite(observedState.output_count),
        terminal_trigger: terminalTrigger,
        collider_group: terminalTrigger ? finite(semanticGuideTarget?.collider_group) : null,
        position: guidePosition,
        semantic_position: guidePosition,
        screen_position: null,
        screen_visible: false,
        value: terminalTrigger ? null : finite(semanticGuideTarget?.remaining_cost),
        discovery_confidence: Math.max(0.95, Number(state.current_guide.confidence || 0)),
        interaction_anchor: {
          path: guidePath,
          position: guidePosition,
          source: "resolved_backend_guide_fallback",
          confidence: Math.max(0.95, Number(state.current_guide.confidence || 0))
        },
        interaction_bounds: null,
        anchor_candidates: [{
          path: guidePath,
          position: guidePosition,
          screen_position: null,
          screen_visible: false,
          source: "resolved_backend_guide_fallback"
        }],
        mechanic_state: null,
        semantic_identity_status: state.current_guide.semantic_identity_status || "unresolved",
        semantic_authority: state.current_guide.semantic_authority || "backend_guide_binding",
        observation_source: "resolved_backend_guide_fallback"
      };
      discoveredTargets.push(target);
    }
    if (target && authoritativeGuide && !authoritativeInactiveGuide) {
      // The exact backend guide is fresher than a station's one-tick delayed
      // visible/unlocked flag. Keep only this matching target actionable for
      // the current observation; the override naturally vanishes with the
      // guide on the next probe.
      target.active = true;
      target.backend_guide_actionable = true;
      if (state.current_guide.interaction_in_progress === true) {
        target.interaction_in_progress = true;
        target.interaction_progress_reason = state.current_guide.interaction_progress_reason
          || "backend_transaction_in_progress";
      }
      if (!target.observation_source) target.observation_source = "resolved_backend_guide_override";
    }
    if (target && authoritativeInactiveGuide) {
      target.active = false;
      target.navigable = false;
      target.backend_guide_actionable = false;
      target.activation_authoritative = true;
      target.interaction_ready = false;
      target.readiness_reason = semanticGuideTarget?.observed_state?.readiness_reason || "backend_node_inactive";
    }
    state.current_guide = {
      ...state.current_guide,
      status: authoritativeInactiveGuide ? "inactive" : state.current_guide.status,
      actionable: authoritativeInactiveGuide ? false : state.current_guide.actionable,
      conflict_reason: authoritativeInactiveGuide ? "guide_target_backend_inactive" : state.current_guide.conflict_reason,
      target_id: target?.id || null,
      target_kind: target?.kind || state.current_guide.target_kind || null,
      target_role: target?.role_hint || state.current_guide.target_role || null,
      target_position: target?.position || state.current_guide.target_position || null
    };
  }
  const inferredMechanisms = inferredNavigationMechanisms(raw, obstacles, config);
  const mechanismMap = new Map();
  for (const item of [...(Array.isArray(state.navigation_mechanisms) ? state.navigation_mechanisms : []), ...inferredMechanisms]) {
    if (!item?.path) continue;
    const previous = mechanismMap.get(item.path);
    const mergedSourceFields = [...(previous?.source_fields || []), ...(item.source_fields || [])]
        .filter((entry, index, values) => values.findIndex((candidate) => candidate.component_path === entry.component_path
          && candidate.component_class === entry.component_class
          && candidate.field === entry.field) === index)
        .slice(0, 8);
    mechanismMap.set(item.path, previous ? {
      ...previous,
      ...item,
      source_fields: mergedSourceFields,
      reference_count: Math.max(mergedSourceFields.length, Number(previous.reference_count || 0), Number(item.reference_count || 0)),
      confidence: Math.max(Number(previous.confidence || 0), Number(item.confidence || 0))
    } : item);
  }
  if (mechanismMap.size > 0) state.navigation_mechanisms = [...mechanismMap.values()]
    .sort((left, right) => String(left.path).localeCompare(String(right.path)));
  return state;
}

function inferredResourceCapacityState(nodes, playerPath) {
  const root = String(playerPath || "").replace(/\/+$/, "");
  if (!root) return null;
  const directIndicatorPaths = new Set([`${root}/MAX`, `${root}/max`]);
  const candidates = (nodes || []).filter((item) => {
    const path = pathOf(item);
    return directIndicatorPaths.has(path);
  });
  const componentBacked = candidates.find((item) =>
    active(item)
    && pathOf(item) === `${root}/max`
    &&
    [...(item?.components || []), item?.className]
      .filter(Boolean)
      .some((entry) => String(entry).toLowerCase() === "maxnode")
  ) || null;
  // Many playables keep a decorative uppercase `MAX` label node active for
  // the whole run while toggling its lowercase MaxNode parent only when the
  // inventory is actually saturated. A name-only active node is therefore
  // insufficient and would create an endless collect/deliver loop.
  if (!componentBacked) return null;
  const primary = componentBacked;
  const activeEvidence = candidates.filter(active);
  return {
    status: "SATURATED",
    saturated: true,
    scope: "player_inventory",
    resource: null,
    source: "active_player_max_component",
    indicator_path: pathOf(primary),
    indicator_name: primary?.name || primary?.nodeName || null,
    evidence_paths: [...new Set(activeEvidence.map(pathOf).filter(Boolean))].sort(),
    confidence: 0.99
  };
}

export function normalizeGenericProbe(raw, at = new Date().toISOString(), config = {}) {
  const nodes = allNodes(raw);
  const mechanicComponents = causalComponentItems(raw);
  // `raw.player` is not another fuzzy candidate: the browser probe has already
  // resolved it as the active gameplay root. Game-local semantic nodes may
  // contain useful descendants (range rings, VFX, skeleton parts) and can score
  // equally or even higher than the root. Letting those descendants win makes
  // the canonical position jump between unrelated local transforms and poisons
  // movement calibration. Fall back to scoring only when no usable resolved
  // root exists.
  const resolvedPlayerCandidate = raw?.player || raw?.fast?.player || null;
  const resolvedPlayer = resolvedPlayerCandidate && active(resolvedPlayerCandidate) && worldPoint(resolvedPlayerCandidate)
    ? resolvedPlayerCandidate
    : null;
  const playerCandidate = resolvedPlayer
    ? { item: resolvedPlayer, score: Math.max(9, playerScore(resolvedPlayer, config)) }
    : nodes.map((item) => ({ item, score: playerScore(item, config) })).sort((a, b) => b.score - a.score)[0];
  const player = playerCandidate?.score >= 4 ? {
    active: active(playerCandidate.item),
    position: worldPoint(playerCandidate.item),
    path: pathOf(playerCandidate.item),
    confidence: Math.min(1, playerCandidate.score / 9),
    mechanic_state: mechanicStateForTarget(pathOf(playerCandidate.item), mechanicComponents, { includeDescendants: false })
  } : null;
  const viewport = raw?.canvas && finite(raw.canvas.width) !== null && finite(raw.canvas.height) !== null
    ? { width: finite(raw.canvas.width), height: finite(raw.canvas.height) }
    : null;
  const obstacles = normalizeObstacles(raw, playerCandidate?.item || null);
  const explicitlyUnreadyResourcePaths = new Set((raw?.moneyResources?.resources || [])
    .filter((item) => item?.primitiveFields?.isReady === false
      || item?.primitiveFields?.ready === false)
    .map(pathOf)
    .filter(Boolean));
  const discoveredTargets = targets(nodes, config, {
    playerPath: player?.path || null,
    viewport,
    obstacles,
    mechanicComponents,
    explicitlyUnreadyResourcePaths
  });
  const completion = completionSignals(raw, nodes, config);
  const failure = failureSignals(raw, nodes, config);
  const probeReady = raw?.observe?.ready === true || raw?.fast?.ready === true;
  const gameState = normalizedGameState(raw, discoveredTargets, obstacles, config);
  const resourceCapacity = inferredResourceCapacityState(nodes, player?.path || null);
  if (resourceCapacity) gameState.resource_capacity = resourceCapacity;
  const structuralSignals = structuralLifecycleSignals(raw, config);
  return {
    at,
    ready: raw?.ready !== false && probeReady,
    player,
    targets: discoveredTargets.map(({ candidate, ...item }) => item),
    pads: discoveredTargets.filter((item) => item.kind === "upgrade").map((item) => ({
      id: item.id,
      path: item.path,
      active: item.active,
      remainingCost: item.value,
      position: item.position,
      interactionBounds: item.interaction_bounds
    })),
    moneySources: discoveredTargets.filter((item) => item.kind === "money").map((item) => ({
      id: item.id,
      path: item.path,
      active: item.active,
      coinNodeCount: item.value,
      position: item.position,
      interactionBounds: item.interaction_bounds
    })),
    resourceCounters: resourceCounters(raw, nodes),
    obstacles,
    structuralSignals,
    winPanelActive: completion.length > 0,
    completionSignals: completion,
    failureEvidence: failure,
    gameState,
    backendCandidates: discoveredTargets.filter((item) => item.screen_visible).map((item) => ({
      candidate_id: item.id,
      path: item.path,
      name: item.name,
      components: item.components,
      active: item.active,
      world_position: item.position,
      semantic_position: item.semantic_position,
      interaction_anchor: item.interaction_anchor,
      interaction_bounds: item.interaction_bounds,
      interaction_entry_policy: item.interaction_entry_policy || null,
      interaction_entry_tolerance: item.interaction_entry_tolerance ?? null,
      interaction_reentry_policy: item.interaction_reentry_policy || null,
      interaction_reentry_exit_waypoint: item.interaction_reentry_exit_waypoint || null,
      interaction_reentry_exit_tolerance: item.interaction_reentry_exit_tolerance ?? null,
      interaction_in_progress: item.interaction_in_progress === true,
      interaction_progress_reason: item.interaction_progress_reason || null,
      mechanic_state: item.mechanic_state,
      screen_position: item.screen_position,
      kind_hint: item.kind,
      role_hint: item.role_hint,
      discovery_confidence: item.discovery_confidence
    }))
  };
}
