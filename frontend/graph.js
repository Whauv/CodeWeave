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
      zoomLayer.attr("transform", event.transform);
    }),
  );
}

function renderGraph(data) {
  const container = document.getElementById("graph-container");
  const width = container.clientWidth;
  const height = container.clientHeight;

  buildSvgShell();

  const links = data.edges.map((edge) => ({ ...edge }));
  const nodes = data.nodes.map((node) => ({ ...node }));
  const nodeById = new Map(nodes.map((node) => [node.id, node]));

  links.forEach((link) => {
    link.source = nodeById.get(link.source);
    link.target = nodeById.get(link.target);
  });

  simulation = d3
    .forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((node) => node.id).distance(120))
    .force("charge", d3.forceManyBody().strength(-300))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide().radius((node) => getNodeRadius(node) + 10));

  linkSelection = linkLayer
    .selectAll("line")
    .data(links, (link) => `${link.source.id}-${link.target.id}`)
    .join("line")
    .attr("stroke", "#444")
    .attr("stroke-width", 1.2)
    .attr("marker-end", "url(#arrowhead)");

  nodeSelection = nodeLayer
    .selectAll("circle")
    .data(nodes, (node) => node.id)
    .join("circle")
    .attr("r", (node) => getNodeRadius(node))
    .attr("fill", (node) => node.mutation_color || "#7a7a7a")
    .attr("stroke", "#111")
    .attr("stroke-width", 2)
    .attr("data-node-id", (node) => node.id)
    .style("cursor", "pointer")
    .on("click", (_, node) => {
      selectedNodeId = node.id;
      highlightNode(node.id);
      window.loadNodeDetail(node, graphData);
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
    .attr("fill", "#ddd")
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
}

function updateGraph(data) {
  initGraph(data);
}

function highlightNode(nodeId) {
  selectedNodeId = nodeId;
  const selectedNode = graphData?.nodes?.find((node) => node.id === nodeId);
  setSelectedLabel(selectedNode ? selectedNode.name : "None");
  nodeSelection
    .attr("stroke", (node) => (node.id === nodeId ? "#ffcc00" : "#111"))
    .attr("stroke-width", (node) => (node.id === nodeId ? 4 : 2));
}

async function triggerBlastRadius(node) {
  try {
    setStatus(`Simulating blast radius for ${node.name}...`);
    const response = await fetch(`/api/blast/${node.id}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Blast radius failed");
    }

    blastMode = true;
    currentBlastData = data;
    setMode("Blast");
    const affected = new Set(data.affected_nodes || []);

    nodeSelection
      .classed("epicenter", (d) => d.id === data.epicenter)
      .attr("fill", (d) => data.risk_colors?.[d.id] || d.mutation_color || "#7a7a7a")
      .attr("opacity", (d) => (affected.has(d.id) ? 1 : 0.15))
      .attr("stroke", (d) => (d.id === data.epicenter ? "#fff" : "#111"));

    labelSelection.attr("opacity", (d) => (affected.has(d.id) ? 1 : 0.15));
    linkSelection.attr("opacity", (link) => (
      affected.has(link.source.id) && affected.has(link.target.id) ? 0.9 : 0.08
    ));

    if (window.showBlastInfo) {
      window.showBlastInfo(data);
    }
    setStatus(data.summary);
  } catch (error) {
    console.error(error);
    setStatus(error.message);
  }
}

function clearBlastRadius() {
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
    .attr("stroke", (node) => (node.id === selectedNodeId ? "#ffcc00" : "#111"))
    .attr("stroke-width", (node) => (node.id === selectedNodeId ? 4 : 2));

  labelSelection.attr("opacity", 1);
  linkSelection.attr("opacity", 0.7);

  if (window.showBlastInfo) {
    window.showBlastInfo({ summary: "Right-click a node to simulate impact.", depth_map: {} });
  }
}

function searchNodes(query) {
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
      .attr("stroke", (d) => (d.id === selectedNodeId ? "#ffcc00" : "#111"))
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
    .attr("stroke", (node) => (matches.has(node.id) ? "#ffcc00" : "#111"))
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
        theme: "vs-dark",
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
  const input = document.getElementById("path-input");
  const path = input.value.trim();
  if (!path) {
    setStatus("Enter an absolute project path.");
    return;
  }

  try {
    setStatus("Scanning project...");
    setMode("Scan");
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

    updateGraph(data);
    setStatus(`Loaded ${data.nodes.length} nodes and ${data.edges.length} edges`);
  } catch (error) {
    console.error(error);
    setStatus(error.message);
    setMode("Error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  showEmptyState();
  document.getElementById("scan-btn").addEventListener("click", scanProject);
  document.getElementById("search-input").addEventListener("input", (event) => {
    searchNodes(event.target.value);
  });
  document.getElementById("clear-blast-btn").addEventListener("click", clearBlastRadius);
  document.getElementById("modal-close-btn").addEventListener("click", () => {
    document.getElementById("monaco-modal").classList.remove("visible");
  });

  window.addEventListener("resize", () => {
    if (graphData) {
      renderGraph(graphData);
      if (selectedNodeId) {
        highlightNode(selectedNodeId);
      }
      if (blastMode && currentBlastData) {
        const epicenterNode = graphData.nodes.find((node) => node.id === currentBlastData.epicenter);
        if (epicenterNode) {
          triggerBlastRadius(epicenterNode);
        }
      }
    }
    if (monacoEditor) {
      monacoEditor.layout();
    }
  });
});

window.initGraph = initGraph;
window.updateGraph = updateGraph;
window.highlightNode = highlightNode;
window.triggerBlastRadius = triggerBlastRadius;
window.clearBlastRadius = clearBlastRadius;
window.searchNodes = searchNodes;
window.openMonacoModal = openMonacoModal;
