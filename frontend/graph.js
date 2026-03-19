let graphData = null;
let selectedNodeId = null;
let blastMode = false;
let currentBlastData = null;

let svg;
let zoomLayer;
let linkLayer;
let nodeLayer;
let labelLayer;
let simulation;
let linkSelection;
let nodeSelection;
let labelSelection;
let monacoEditor = null;
let scanFrameTimer = null;
let isScanning = false;
let linkData = [];
let nodeData = [];
let layoutMode = "tree";
let blastRequestInFlight = false;
let themeMode = "dark";
let currentScanTarget = "";
let scanHistory = [];
const SCAN_HISTORY_STORAGE_KEY = "codeweave-scan-history-v1";
const GRAPH_CACHE_DB_NAME = "codeweave-graph-cache";
const GRAPH_CACHE_STORE = "snapshots";
const VIEW_MODE_STORAGE_KEY = "codeweave-view-mode-v1";
const SPLIT_RATIO_STORAGE_KEY = "codeweave-split-ratio-v1";
let graphCacheDbPromise = null;
let viewMode = "split";
let splitRatio = 0.68;
let isDraggingDivider = false;
let zoomFramePending = false;
let pendingZoomTransform = null;
let dividerRenderFrame = null;

function getSelectedNode() {
  return graphData?.nodes?.find((node) => node.id === selectedNodeId) || null;
}

function getTooltipElements() {
  return {
    container: document.getElementById("node-tooltip"),
    title: document.getElementById("node-tooltip-title"),
    body: document.getElementById("node-tooltip-body"),
  };
}

function setViewMode(nextMode, persist = true) {
  viewMode = nextMode;
  const frame = document.querySelector(".main-frame");
  if (frame) {
    frame.dataset.view = nextMode;
    if (nextMode === "split") {
      frame.style.gridTemplateColumns = `minmax(0, ${splitRatio}fr) 14px minmax(320px, ${Math.max(0.35, 1 - splitRatio)}fr)`;
    } else if (nextMode === "graph") {
      frame.style.gridTemplateColumns = "1fr 0 0";
    } else if (nextMode === "panel") {
      frame.style.gridTemplateColumns = "0 0 minmax(420px, 1fr)";
    }
  }
  document.getElementById("view-split-btn")?.classList.toggle("active", nextMode === "split");
  document.getElementById("view-graph-btn")?.classList.toggle("active", nextMode === "graph");
  document.getElementById("view-panel-btn")?.classList.toggle("active", nextMode === "panel");
  if (persist) {
    window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, nextMode);
  }
}

function loadSplitRatio() {
  const raw = Number(window.localStorage.getItem(SPLIT_RATIO_STORAGE_KEY));
  if (!Number.isNaN(raw) && raw >= 0.35 && raw <= 0.82) {
    splitRatio = raw;
  }
}

function persistSplitRatio() {
  window.localStorage.setItem(SPLIT_RATIO_STORAGE_KEY, String(splitRatio));
}

function applySplitRatio() {
  if (viewMode === "split") {
    setViewMode("split", false);
  }
}

function setSplitRatioFromPointer(clientX) {
  const frame = document.querySelector(".main-frame");
  if (!frame) {
    return;
  }
  const bounds = frame.getBoundingClientRect();
  const relativeX = clientX - bounds.left;
  const nextRatio = relativeX / bounds.width;
  splitRatio = Math.min(0.82, Math.max(0.35, nextRatio));
  persistSplitRatio();
  applySplitRatio();
  scheduleGraphResize();
}

function setDividerDragging(isDragging) {
  isDraggingDivider = isDragging;
  document.getElementById("pane-divider")?.classList.toggle("dragging", isDragging);
  document.body.style.userSelect = isDragging ? "none" : "";
}

function openGraphCacheDb() {
  if (graphCacheDbPromise) {
    return graphCacheDbPromise;
  }

  graphCacheDbPromise = new Promise((resolve, reject) => {
    const request = window.indexedDB.open(GRAPH_CACHE_DB_NAME, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(GRAPH_CACHE_STORE)) {
        db.createObjectStore(GRAPH_CACHE_STORE, { keyPath: "target" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

  return graphCacheDbPromise;
}

async function saveGraphSnapshot(target, data) {
  if (!target || !data) {
    return;
  }
  try {
    const db = await openGraphCacheDb();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(GRAPH_CACHE_STORE, "readwrite");
      const store = transaction.objectStore(GRAPH_CACHE_STORE);
      store.put({
        target,
        data,
        updatedAt: Date.now(),
      });
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
    });
  } catch (error) {
    console.error(error);
  }
}

async function getGraphSnapshot(target) {
  if (!target) {
    return null;
  }
  try {
    const db = await openGraphCacheDb();
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(GRAPH_CACHE_STORE, "readonly");
      const store = transaction.objectStore(GRAPH_CACHE_STORE);
      const request = store.get(target);
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
  } catch (error) {
    console.error(error);
    return null;
  }
}

function isGithubUrl(value) {
  return /^https?:\/\/(www\.)?github\.com\//i.test(value || "");
}

function buildScanHistoryLabel(target) {
  const normalized = String(target || "").trim();
  if (!normalized) {
    return "Unknown target";
  }
  if (isGithubUrl(normalized)) {
    try {
      const url = new URL(normalized);
      const path = url.pathname.replace(/^\//, "").replace(/\.git$/, "");
      return path || normalized;
    } catch (_) {
      return normalized;
    }
  }
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || normalized;
}

function loadScanHistory() {
  try {
    const raw = window.localStorage.getItem(SCAN_HISTORY_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    scanHistory = Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    console.error(error);
    scanHistory = [];
  }
}

function persistScanHistory() {
  window.localStorage.setItem(SCAN_HISTORY_STORAGE_KEY, JSON.stringify(scanHistory));
}

function renderScanHistory() {
  const container = document.getElementById("scan-history-list");
  if (!container) {
    return;
  }

  container.innerHTML = "";
  if (scanHistory.length === 0) {
    const empty = document.createElement("div");
    empty.className = "scan-history-empty";
    empty.textContent = "Scanned project paths and GitHub repos will appear here.";
    container.appendChild(empty);
    return;
  }

  scanHistory.forEach((entry) => {
    const button = document.createElement("button");
    button.className = `scan-history-chip ${entry.target === currentScanTarget ? "active" : ""}`;
    button.innerHTML = `
      <span class="scan-history-chip-inner">
        <span class="scan-history-kind">${entry.kind}</span>
        <span class="scan-history-label">${entry.label}</span>
      </span>
      <span class="chip-delete-btn" title="Remove from recent scans">x</span>
    `;
    button.title = entry.target;
    button.addEventListener("click", async (event) => {
      if (event.target instanceof HTMLElement && event.target.classList.contains("chip-delete-btn")) {
        event.stopPropagation();
        deleteScanHistoryEntry(entry.target);
        return;
      }
      const input = document.getElementById("path-input");
      if (input) {
        input.value = entry.target;
        input.focus();
      }
      currentScanTarget = entry.target;
      renderScanHistory();
      const loaded = await loadCachedScanTarget(entry.target, { silent: true });
      setStatus(
        loaded
          ? `Loaded cached graph for ${entry.target}`
          : `Loaded recent target: ${entry.target}`
      );
    });
    container.appendChild(button);
  });
}

function addScanHistoryEntry(target) {
  const normalized = String(target || "").trim();
  if (!normalized) {
    return;
  }
  currentScanTarget = normalized;
  const entry = {
    target: normalized,
    label: buildScanHistoryLabel(normalized),
    kind: isGithubUrl(normalized) ? "GitHub" : "Local",
    updatedAt: Date.now(),
  };
  scanHistory = [entry, ...scanHistory.filter((item) => item.target !== normalized)]
    .sort((left, right) => (right.updatedAt || 0) - (left.updatedAt || 0))
    .slice(0, 12);
  persistScanHistory();
  renderScanHistory();
}

function clearScanHistory() {
  scanHistory = [];
  currentScanTarget = "";
  window.localStorage.removeItem(SCAN_HISTORY_STORAGE_KEY);
  renderScanHistory();
  setStatus("Cleared recent scan history.");
}

function deleteScanHistoryEntry(target) {
  scanHistory = scanHistory.filter((entry) => entry.target !== target);
  if (currentScanTarget === target) {
    currentScanTarget = "";
  }
  persistScanHistory();
  renderScanHistory();
  setStatus(`Removed recent scan: ${target}`);
}

async function loadCachedScanTarget(target, options = {}) {
  const normalized = String(target || "").trim();
  if (!normalized) {
    return false;
  }

  const snapshot = await getGraphSnapshot(normalized);
  if (!snapshot || !snapshot.data) {
    return false;
  }

  currentScanTarget = normalized;
  window.__CODEWEAVE_SCAN_TARGET__ = normalized;
  updateGraph(snapshot.data);
  if (!options.silent) {
    setStatus(`Loaded cached graph for ${normalized}`);
  }
  renderScanHistory();
  return true;
}

function setMetricValue(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value;
  }
}

function setStatus(message) {
  const status = document.getElementById("status-label");
  if (status) {
    status.textContent = message;
  }
}

function syncActionButtons() {
  const hasSelection = Boolean(getSelectedNode());
  const blastButton = document.getElementById("simulate-blast-btn");
  const sourceButton = document.getElementById("view-source-btn");
  if (blastButton) {
    blastButton.disabled = !hasSelection || blastRequestInFlight;
    blastButton.textContent = blastRequestInFlight ? "Running Blast..." : "Simulate Blast Radius";
  }
  if (sourceButton) {
    sourceButton.disabled = !hasSelection;
  }
}

function setMode(mode) {
  setMetricValue("metric-mode", mode);
}

function setSelectedLabel(label) {
  setMetricValue("metric-selected", label);
}

function setGraphMetrics(nodesCount, edgesCount) {
  setMetricValue("metric-nodes", String(nodesCount));
  setMetricValue("metric-edges", String(edgesCount));
}

function showEmptyState() {
  document.getElementById("graph-empty-state")?.classList.remove("hidden");
}

function hideEmptyState() {
  document.getElementById("graph-empty-state")?.classList.add("hidden");
}

function setScanOverlayMessage(message) {
  const copy = document.getElementById("scan-overlay-copy");
  if (copy) {
    copy.textContent = message;
  }
}

function showScanOverlay() {
  const overlay = document.getElementById("scan-overlay");
  overlay?.classList.add("visible");
  const frames = [
    "Parsing Python files, linking calls, and preparing the dependency map.",
    "Resolving functions, classes, and import relationships.",
    "Batching summaries and enriching the graph for the UI.",
  ];
  let frameIndex = 0;
  setScanOverlayMessage(frames[frameIndex]);
  if (scanFrameTimer) {
    window.clearInterval(scanFrameTimer);
  }
  scanFrameTimer = window.setInterval(() => {
    frameIndex = (frameIndex + 1) % frames.length;
    setScanOverlayMessage(frames[frameIndex]);
  }, 1200);
}

function hideScanOverlay() {
  document.getElementById("scan-overlay")?.classList.remove("visible");
  if (scanFrameTimer) {
    window.clearInterval(scanFrameTimer);
    scanFrameTimer = null;
  }
}

function setLayoutMode(nextMode) {
  layoutMode = nextMode;
  document.getElementById("tree-layout-btn")?.classList.toggle("active", nextMode === "tree");
  document.getElementById("force-layout-btn")?.classList.toggle("active", nextMode === "force");
  if (graphData) {
    renderGraph(graphData);
    restoreVisualState();
  }
}

function showNodeTooltip(node, event) {
  const { container, title, body } = getTooltipElements();
  if (!container || !title || !body) {
    return;
  }

  title.textContent = node.name || "Node";
  body.textContent = node.summary || "No summary available.";
  container.classList.add("visible");
  moveNodeTooltip(event);
}

function moveNodeTooltip(event) {
  const { container } = getTooltipElements();
  const graphContainer = document.getElementById("graph-container");
  if (!container || !graphContainer) {
    return;
  }

  const bounds = graphContainer.getBoundingClientRect();
  const offsetX = 18;
  const offsetY = 18;
  const tooltipWidth = container.offsetWidth || 280;
  const tooltipHeight = container.offsetHeight || 80;
  let left = event.clientX - bounds.left + offsetX;
  let top = event.clientY - bounds.top + offsetY;

  if (left + tooltipWidth > bounds.width - 12) {
    left = bounds.width - tooltipWidth - 12;
  }
  if (top + tooltipHeight > bounds.height - 12) {
    top = bounds.height - tooltipHeight - 12;
  }

  container.style.left = `${Math.max(12, left)}px`;
  container.style.top = `${Math.max(12, top)}px`;
}

function hideNodeTooltip() {
  const { container } = getTooltipElements();
  container?.classList.remove("visible");
}

function applyTheme(nextTheme, persist = true) {
  themeMode = nextTheme;
  document.body.dataset.theme = nextTheme;
  const themeButton = document.getElementById("theme-toggle-btn");
  if (themeButton) {
    themeButton.textContent = nextTheme === "dark" ? "Light Theme" : "Dark Theme";
  }
  if (persist) {
    window.localStorage.setItem("codemapper-theme", nextTheme);
  }
}

function getThemeColor(variableName, fallback) {
  const value = window.getComputedStyle(document.body).getPropertyValue(variableName).trim();
  return value || fallback;
}

function getGraphPalette() {
  return {
    nodeStroke: getThemeColor("--graph-node-stroke", "#111111"),
    nodeSelected: getThemeColor("--graph-node-selected", "#ffcc00"),
    linkTree: getThemeColor("--graph-link", "rgba(149, 193, 214, 0.34)"),
    linkTreeMuted: getThemeColor("--graph-link-muted", "rgba(149, 193, 214, 0.14)"),
    linkForce: getThemeColor("--graph-link-force", "#444444"),
    label: getThemeColor("--graph-label", "#d9e3e8"),
  };
}

function getMonacoTheme() {
  return themeMode === "light" ? "vs" : "vs-dark";
}

function getNodeRadius(node) {
  const degree = (node.incomingCount || 0) + (node.outgoingCount || 0);
  return Math.max(8, Math.min(24, 8 + degree * 1.5));
}

function truncateLabel(label) {
  return label.length > 20 ? `${label.slice(0, 17)}...` : label;
}

function computeNodeDegrees(data) {
  const degreeMap = new Map();
  data.nodes.forEach((node) => {
    degreeMap.set(node.id, { incoming: 0, outgoing: 0 });
  });

  data.edges.forEach((edge) => {
    if (degreeMap.has(edge.source)) {
      degreeMap.get(edge.source).outgoing += 1;
    }
    if (degreeMap.has(edge.target)) {
      degreeMap.get(edge.target).incoming += 1;
    }
  });

  data.nodes.forEach((node) => {
    const counts = degreeMap.get(node.id) || { incoming: 0, outgoing: 0 };
    node.incomingCount = counts.incoming;
    node.outgoingCount = counts.outgoing;
  });
}

function buildSvgShell() {
  svg = d3.select("#graph-svg");
  svg.selectAll("*").remove();

  const defs = svg.append("defs");
  defs
    .append("marker")
    .attr("id", "arrowhead")
    .attr("viewBox", "0 -5 10 10")
    .attr("refX", 22)
    .attr("refY", 0)
    .attr("markerWidth", 7)
    .attr("markerHeight", 7)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,-5L10,0L0,5")
    .attr("fill", "#666");

  zoomLayer = svg.append("g").attr("class", "zoom-layer");
  linkLayer = zoomLayer.append("g").attr("class", "links");
  nodeLayer = zoomLayer.append("g").attr("class", "nodes");
  labelLayer = zoomLayer.append("g").attr("class", "labels");

  svg.call(
    d3.zoom().scaleExtent([0.2, 3]).on("zoom", (event) => {
      pendingZoomTransform = event.transform;
      if (zoomFramePending) {
        return;
      }
      zoomFramePending = true;
      window.requestAnimationFrame(() => {
        if (zoomLayer && pendingZoomTransform) {
          zoomLayer.attr("transform", pendingZoomTransform);
        }
        zoomFramePending = false;
      });
    }),
  );
}

function scheduleGraphResize() {
  if (!graphData) {
    return;
  }
  if (dividerRenderFrame) {
    window.cancelAnimationFrame(dividerRenderFrame);
  }
  dividerRenderFrame = window.requestAnimationFrame(() => {
    renderGraph(graphData);
    restoreVisualState();
    dividerRenderFrame = null;
  });
}

function buildGraphCollections(data) {
  nodeData = data.nodes.map((node) => ({ ...node }));
  const nodeById = new Map(nodeData.map((node) => [node.id, node]));
  linkData = data.edges
    .map((edge) => ({
      source: nodeById.get(edge.source),
      target: nodeById.get(edge.target),
    }))
    .filter((edge) => edge.source && edge.target);
  return { nodes: nodeData, links: linkData, nodeById };
}

function applyTreeLayout(nodes, links, width, height) {
  const incoming = new Map(nodes.map((node) => [node.id, []]));
  const outgoing = new Map(nodes.map((node) => [node.id, []]));

  links.forEach((link) => {
    incoming.get(link.target.id)?.push(link.source.id);
    outgoing.get(link.source.id)?.push(link.target.id);
  });

  let roots = nodes.filter((node) => (incoming.get(node.id) || []).length === 0);
  if (roots.length === 0 && nodes.length > 0) {
    roots = [...nodes].sort((left, right) => (right.outgoingCount || 0) - (left.outgoingCount || 0)).slice(0, 1);
  }

  const depthMap = new Map();
  const queue = roots.map((node) => ({ id: node.id, depth: 0 }));
  while (queue.length > 0) {
    const current = queue.shift();
    if (depthMap.has(current.id)) {
      continue;
    }
    depthMap.set(current.id, current.depth);
    (outgoing.get(current.id) || []).forEach((childId) => {
      if (!depthMap.has(childId)) {
        queue.push({ id: childId, depth: current.depth + 1 });
      }
    });
  }

  let fallbackDepth = depthMap.size > 0 ? Math.max(...depthMap.values()) + 1 : 0;
  nodes.forEach((node) => {
    if (!depthMap.has(node.id)) {
      depthMap.set(node.id, fallbackDepth);
      fallbackDepth += 1;
    }
  });

  const layers = new Map();
  nodes.forEach((node) => {
    const depth = depthMap.get(node.id) || 0;
    if (!layers.has(depth)) {
      layers.set(depth, []);
    }
    layers.get(depth).push(node);
  });

  const orderedDepths = [...layers.keys()].sort((left, right) => left - right);
  const leftMargin = 110;
  const rightMargin = 140;
  const topMargin = 90;
  const bottomMargin = 90;
  const xStep = orderedDepths.length > 1
    ? (width - leftMargin - rightMargin) / (orderedDepths.length - 1)
    : 0;

  orderedDepths.forEach((depth) => {
    const layerNodes = layers.get(depth).sort((left, right) => {
      const leftWeight = (left.outgoingCount || 0) + (left.incomingCount || 0);
      const rightWeight = (right.outgoingCount || 0) + (right.incomingCount || 0);
      return rightWeight - leftWeight;
    });
    const yStep = layerNodes.length > 1
      ? (height - topMargin - bottomMargin) / (layerNodes.length - 1)
      : 0;
    layerNodes.forEach((node, index) => {
      node.x = leftMargin + (depth * xStep);
      node.y = layerNodes.length === 1
        ? height / 2
        : topMargin + (index * yStep);
    });
  });
}

function treePath(link) {
  const midX = (link.source.x + link.target.x) / 2;
  return `M${link.source.x},${link.source.y} C${midX},${link.source.y} ${midX},${link.target.y} ${link.target.x},${link.target.y}`;
}

function renderTreeGraph(data, width, height) {
  const { nodes, links } = buildGraphCollections(data);
  const palette = getGraphPalette();
  applyTreeLayout(nodes, links, width, height);

  linkSelection = linkLayer
    .selectAll("path")
    .data(links, (link) => `${link.source.id}-${link.target.id}`)
    .join("path")
    .attr("d", (link) => treePath(link))
    .attr("fill", "none")
    .attr("stroke", palette.linkTree)
    .attr("stroke-width", 1.6)
    .attr("marker-end", "url(#arrowhead)");

  nodeSelection = nodeLayer
    .selectAll("circle")
    .data(nodes, (node) => node.id)
    .join("circle")
    .attr("cx", (node) => node.x)
    .attr("cy", (node) => node.y)
    .attr("r", (node) => getNodeRadius(node))
    .attr("fill", (node) => node.mutation_color || "#7a7a7a")
    .attr("stroke", palette.nodeStroke)
    .attr("stroke-width", 2)
    .attr("data-node-id", (node) => node.id)
    .style("cursor", "pointer")
    .on("click", (_, node) => {
      selectedNodeId = node.id;
      highlightNode(node.id);
      window.loadNodeDetail(node, graphData);
    })
    .on("mouseenter", (event, node) => {
      showNodeTooltip(node, event);
    })
    .on("mousemove", (event) => {
      moveNodeTooltip(event);
    })
    .on("mouseleave", () => {
      hideNodeTooltip();
    })
    .on("contextmenu", (event, node) => {
      event.preventDefault();
      triggerBlastRadius(node);
    })
    .on("dblclick", (_, node) => {
      openMonacoModal(node.source_code || "# No source code available");
    });

  labelSelection = labelLayer
    .selectAll("text")
    .data(nodes, (node) => node.id)
    .join("text")
    .text((node) => truncateLabel(node.name))
    .attr("x", (node) => node.x + getNodeRadius(node) + 10)
    .attr("y", (node) => node.y + 4)
    .attr("fill", palette.label)
    .attr("font-size", 12)
    .attr("text-anchor", "start")
    .attr("pointer-events", "none");
}

function renderForceGraph(data, width, height) {
  const { nodes, links } = buildGraphCollections(data);
  const palette = getGraphPalette();

  simulation = d3
    .forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((node) => node.id).distance(120))
    .force("charge", d3.forceManyBody().strength(-300))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide().radius((node) => getNodeRadius(node) + 10))
    .alphaDecay(0.08)
    .velocityDecay(0.45);

  linkSelection = linkLayer
    .selectAll("line")
    .data(links, (link) => `${link.source.id}-${link.target.id}`)
    .join("line")
    .attr("stroke", palette.linkForce)
    .attr("stroke-width", 1.2)
    .attr("marker-end", "url(#arrowhead)");

  nodeSelection = nodeLayer
    .selectAll("circle")
    .data(nodes, (node) => node.id)
    .join("circle")
    .attr("r", (node) => getNodeRadius(node))
    .attr("fill", (node) => node.mutation_color || "#7a7a7a")
    .attr("stroke", palette.nodeStroke)
    .attr("stroke-width", 2)
    .attr("data-node-id", (node) => node.id)
    .style("cursor", "pointer")
    .on("click", (_, node) => {
      selectedNodeId = node.id;
      highlightNode(node.id);
      window.loadNodeDetail(node, graphData);
    })
    .on("mouseenter", (event, node) => {
      showNodeTooltip(node, event);
    })
    .on("mousemove", (event) => {
      moveNodeTooltip(event);
    })
    .on("mouseleave", () => {
      hideNodeTooltip();
    })
    .on("contextmenu", (event, node) => {
      event.preventDefault();
      triggerBlastRadius(node);
    })
    .on("dblclick", (_, node) => {
      openMonacoModal(node.source_code || "# No source code available");
    })
    .call(
      d3
        .drag()
        .on("start", dragStarted)
        .on("drag", dragged)
        .on("end", dragEnded),
    );

  labelSelection = labelLayer
    .selectAll("text")
    .data(nodes, (node) => node.id)
    .join("text")
    .text((node) => truncateLabel(node.name))
    .attr("fill", palette.label)
    .attr("font-size", 12)
    .attr("text-anchor", "middle")
    .attr("pointer-events", "none");

  simulation.on("tick", () => {
    linkSelection
      .attr("x1", (link) => link.source.x)
      .attr("y1", (link) => link.source.y)
      .attr("x2", (link) => link.target.x)
      .attr("y2", (link) => link.target.y);
    nodeSelection
      .attr("cx", (node) => node.x)
      .attr("cy", (node) => node.y);
    labelSelection
      .attr("x", (node) => node.x)
      .attr("y", (node) => node.y + getNodeRadius(node) + 14);
  });
}

function renderGraph(data) {
  const container = document.getElementById("graph-container");
  const width = container.clientWidth;
  const height = container.clientHeight;

  buildSvgShell();
  if (simulation) {
    simulation.stop();
    simulation = null;
  }
  if (layoutMode === "tree") {
    renderTreeGraph(data, width, height);
  } else {
    renderForceGraph(data, width, height);
  }
}

function dragStarted(event) {
  if (!event.active) {
    simulation.alphaTarget(0.3).restart();
  }
  event.subject.fx = event.subject.x;
  event.subject.fy = event.subject.y;
}

function dragged(event) {
  event.subject.fx = event.x;
  event.subject.fy = event.y;
}

function dragEnded(event) {
  if (!event.active) {
    simulation.alphaTarget(0);
  }
  event.subject.fx = null;
  event.subject.fy = null;
}

function initGraph(data) {
  graphData = data;
  window.__CODEMAPPER_GRAPH__ = data;
  computeNodeDegrees(graphData);
  renderGraph(graphData);
  hideEmptyState();
  setGraphMetrics(graphData.nodes.length, graphData.edges.length);
  setMode("Explore");
  setSelectedLabel("None");
  clearBlastRadius();
  syncActionButtons();
}

function restoreVisualState() {
  if (selectedNodeId) {
    highlightNode(selectedNodeId);
  }
  if (blastMode && currentBlastData) {
    applyBlastData(currentBlastData);
  }
}

function updateGraph(data) {
  initGraph(data);
}

function highlightNode(nodeId) {
  const palette = getGraphPalette();
  selectedNodeId = nodeId;
  const selectedNode = getSelectedNode();
  setSelectedLabel(selectedNode ? selectedNode.name : "None");
  syncActionButtons();
  nodeSelection
    .attr("stroke", (node) => (node.id === nodeId ? palette.nodeSelected : palette.nodeStroke))
    .attr("stroke-width", (node) => (node.id === nodeId ? 4 : 2));
}

function applyBlastData(data) {
  const palette = getGraphPalette();
  blastMode = true;
  currentBlastData = data;
  setMode("Blast");
  const affected = new Set(data.affected_nodes || []);

  nodeSelection
    .classed("epicenter", (d) => d.id === data.epicenter)
    .attr("fill", (d) => data.risk_colors?.[d.id] || d.mutation_color || "#7a7a7a")
    .attr("opacity", (d) => (affected.has(d.id) ? 1 : 0.12))
    .attr("stroke", (d) => {
      if (d.id === data.epicenter) {
        return "#fff5d6";
      }
      return d.id === selectedNodeId ? palette.nodeSelected : palette.nodeStroke;
    });

  labelSelection.attr("opacity", (d) => (affected.has(d.id) ? 1 : 0.14));
  linkSelection
    .attr("opacity", (link) => (
      affected.has(link.source.id) && affected.has(link.target.id) ? 0.95 : 0.05
    ))
    .attr("stroke", (link) => (
      affected.has(link.source.id) && affected.has(link.target.id)
        ? "rgba(255, 190, 137, 0.65)"
        : layoutMode === "tree"
          ? palette.linkTreeMuted
          : palette.linkForce
    ));

  if (window.showBlastInfo) {
    window.showBlastInfo(data);
  }
}

async function triggerBlastRadius(node) {
  if (!node || blastRequestInFlight) {
    return;
  }

  try {
    blastRequestInFlight = true;
    syncActionButtons();
    setStatus(`Simulating blast radius for ${node.name}...`);
    const response = await fetch(`/api/blast/${node.id}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Blast radius failed");
    }

    applyBlastData(data);
    setStatus(data.summary);
  } catch (error) {
    console.error(error);
    setStatus(error.message);
  } finally {
    blastRequestInFlight = false;
    syncActionButtons();
  }
}

function clearBlastRadius() {
  const palette = getGraphPalette();
  blastMode = false;
  currentBlastData = null;
  setMode("Explore");
  if (!nodeSelection) {
    return;
  }

  nodeSelection
    .classed("epicenter", false)
    .attr("fill", (node) => node.mutation_color || "#7a7a7a")
    .attr("opacity", 1)
    .attr("stroke", (node) => (node.id === selectedNodeId ? palette.nodeSelected : palette.nodeStroke))
    .attr("stroke-width", (node) => (node.id === selectedNodeId ? 4 : 2));

  labelSelection.attr("opacity", 1);
  linkSelection
    .attr("opacity", layoutMode === "tree" ? 0.75 : 0.7)
    .attr("stroke", layoutMode === "tree" ? palette.linkTree : palette.linkForce);

  if (window.showBlastInfo) {
    window.showBlastInfo({ summary: "Right-click a node to simulate impact.", depth_map: {} });
  }
  syncActionButtons();
}

function searchNodes(query) {
  const palette = getGraphPalette();
  if (!graphData || !nodeSelection) {
    return;
  }

  const normalized = query.trim().toLowerCase();
  if (!normalized) {
    setStatus(graphData ? `Loaded ${graphData.nodes.length} nodes and ${graphData.edges.length} edges` : "Ready to scan");
    nodeSelection
      .attr("opacity", blastMode && currentBlastData
        ? (d) => (currentBlastData.affected_nodes || []).includes(d.id) ? 1 : 0.15
        : 1)
      .attr("stroke", (d) => (d.id === selectedNodeId ? palette.nodeSelected : palette.nodeStroke))
      .attr("stroke-width", (d) => (d.id === selectedNodeId ? 4 : 2));
    labelSelection.attr("opacity", blastMode && currentBlastData
      ? (d) => (currentBlastData.affected_nodes || []).includes(d.id) ? 1 : 0.15
      : 1);
    return;
  }

  const matches = new Set(
    graphData.nodes
      .filter((node) => {
        const summary = (node.summary || "").toLowerCase();
        return node.name.toLowerCase().includes(normalized) || summary.includes(normalized);
      })
      .map((node) => node.id),
  );

  nodeSelection
    .attr("opacity", (node) => (matches.has(node.id) ? 1 : 0.2))
    .attr("stroke", (node) => (matches.has(node.id) ? palette.nodeSelected : palette.nodeStroke))
    .attr("stroke-width", (node) => (matches.has(node.id) ? 4 : 2));

  labelSelection.attr("opacity", (node) => (matches.has(node.id) ? 1 : 0.2));
  setStatus(`Search matched ${matches.size} node${matches.size === 1 ? "" : "s"}`);
}

function ensureMonaco(sourceCode) {
  return new Promise((resolve) => {
    if (monacoEditor && window.monaco) {
      monacoEditor.setValue(sourceCode);
      resolve();
      return;
    }

    window.require.config({
      paths: {
        vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs",
      },
    });

    window.require(["vs/editor/editor.main"], () => {
      monacoEditor = window.monaco.editor.create(document.getElementById("monaco-editor"), {
        value: sourceCode,
        language: "python",
        readOnly: true,
        theme: getMonacoTheme(),
        minimap: { enabled: false },
        fontSize: 14,
      });
      resolve();
    });
  });
}

async function openMonacoModal(source) {
  const modal = document.getElementById("monaco-modal");
  modal.classList.add("visible");
  await ensureMonaco(source);
  if (monacoEditor) {
    monacoEditor.layout();
  }
}

async function scanProject() {
  if (isScanning) {
    return;
  }

  const input = document.getElementById("path-input");
  const path = input.value.trim();
  if (!path) {
    setStatus("Enter an absolute project path or GitHub repository URL.");
    return;
  }

  try {
    isScanning = true;
    setStatus("Scanning project...");
    setMode("Scan");
    showScanOverlay();
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ path }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Scan failed");
    }

    currentScanTarget = path;
    window.__CODEWEAVE_SCAN_TARGET__ = path;
    updateGraph(data);
    addScanHistoryEntry(path);
    await saveGraphSnapshot(path, data);
    setStatus(`Loaded ${data.nodes.length} nodes and ${data.edges.length} edges`);
  } catch (error) {
    console.error(error);
    setStatus(error.message);
    setMode("Error");
  } finally {
    isScanning = false;
    hideScanOverlay();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(window.localStorage.getItem("codemapper-theme") || "dark", false);
  window.__CODEWEAVE_SCAN_TARGET__ = currentScanTarget;
  loadSplitRatio();
  setViewMode(window.localStorage.getItem(VIEW_MODE_STORAGE_KEY) || "split", false);
  loadScanHistory();
  renderScanHistory();
  showEmptyState();
  document.getElementById("scan-btn").addEventListener("click", scanProject);
  document.getElementById("view-split-btn").addEventListener("click", () => setViewMode("split"));
  document.getElementById("view-graph-btn").addEventListener("click", () => setViewMode("graph"));
  document.getElementById("view-panel-btn").addEventListener("click", () => setViewMode("panel"));
  document.getElementById("scan-history-clear-btn").addEventListener("click", clearScanHistory);
  document.getElementById("tree-layout-btn").addEventListener("click", () => setLayoutMode("tree"));
  document.getElementById("force-layout-btn").addEventListener("click", () => setLayoutMode("force"));
  document.getElementById("theme-toggle-btn").addEventListener("click", () => {
    applyTheme(themeMode === "dark" ? "light" : "dark");
    if (graphData) {
      renderGraph(graphData);
      restoreVisualState();
    }
    if (window.monaco) {
      window.monaco.editor.setTheme(getMonacoTheme());
    }
  });
  document.getElementById("search-input").addEventListener("input", (event) => {
    searchNodes(event.target.value);
  });
  document.getElementById("clear-blast-btn").addEventListener("click", clearBlastRadius);
  document.getElementById("pane-divider").addEventListener("mousedown", (event) => {
    if (window.innerWidth <= 980) {
      return;
    }
    if (viewMode !== "split") {
      setViewMode("split");
    }
    setDividerDragging(true);
    event.preventDefault();
  });
  document.getElementById("simulate-blast-btn").addEventListener("click", () => {
    const node = getSelectedNode();
    if (node) {
      triggerBlastRadius(node);
    } else {
      setStatus("Select a node first to simulate blast radius.");
    }
  });
  document.getElementById("view-source-btn").addEventListener("click", () => {
    const node = getSelectedNode();
    if (node) {
      openMonacoModal(node.source_code || "# No source code available");
    } else {
      setStatus("Select a node first to view source.");
    }
  });
  document.getElementById("modal-close-btn").addEventListener("click", () => {
    document.getElementById("monaco-modal").classList.remove("visible");
  });

  window.addEventListener("resize", () => {
    hideNodeTooltip();
    if (window.innerWidth > 980 && viewMode === "split") {
      applySplitRatio();
    }
    if (graphData) {
      renderGraph(graphData);
      restoreVisualState();
    }
    if (monacoEditor) {
      monacoEditor.layout();
    }
  });
  window.addEventListener("mousemove", (event) => {
    if (!isDraggingDivider || window.innerWidth <= 980) {
      return;
    }
    setSplitRatioFromPointer(event.clientX);
  });
  window.addEventListener("mouseup", () => {
    if (isDraggingDivider) {
      setDividerDragging(false);
    }
  });
  window.addEventListener("resize", () => {
    scheduleGraphResize();
  });
  syncActionButtons();
});

window.initGraph = initGraph;
window.updateGraph = updateGraph;
window.highlightNode = highlightNode;
window.triggerBlastRadius = triggerBlastRadius;
window.clearBlastRadius = clearBlastRadius;
window.searchNodes = searchNodes;
window.openMonacoModal = openMonacoModal;
window.loadCachedScanTarget = loadCachedScanTarget;
