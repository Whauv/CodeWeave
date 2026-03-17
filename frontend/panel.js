function getNodeById(nodeId, graphData) {
  return graphData?.nodes?.find((node) => node.id === nodeId) || null;
}

function makeLinkedNodeItem(node, graphData) {
  const button = document.createElement("button");
  button.className = "linked-item";
  button.innerHTML = `<span>${node.name}</span><span class="linked-meta">${node.type || "node"}</span>`;
  button.addEventListener("click", () => {
    if (window.highlightNode) {
      window.highlightNode(node.id);
    }
    loadNodeDetail(node, graphData);
  });
  return button;
}

function getMutationBadgeHTML(status) {
  const normalized = (status || "stable").toLowerCase();
  const palette = {
    new: { background: "#003f2d", color: "#00ff88", label: "NEW" },
    modified: { background: "#4f3f00", color: "#ffcc00", label: "MODIFIED" },
    hotspot: { background: "#4a1515", color: "#ff5555", label: "HOTSPOT" },
    stable: { background: "#333333", color: "#dddddd", label: "STABLE" },
  };
  const badge = palette[normalized] || palette.stable;
  return `<span class="mutation-badge" style="background:${badge.background};color:${badge.color};">${badge.label}</span>`;
}

async function loadNodeDetail(node, graphData) {
  try {
    if (window.highlightNode) {
      window.highlightNode(node.id);
    }
    const response = await fetch(`/api/node/${node.id}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Failed to load node details");
    }

    document.getElementById("detail-panel").classList.remove("hidden");
    document.getElementById("detail-shell").classList.remove("hidden");
    document.getElementById("node-name").textContent = data.name || "Unknown Node";
    document.getElementById("node-file").textContent = `${data.file || "unknown"}:${data.line || 0}`;
    document.getElementById("node-summary").textContent = data.summary || "No summary available.";
    document.getElementById("mutation-badge").innerHTML = getMutationBadgeHTML(data.mutation_status);
    document.getElementById("churn-info").textContent =
      `Churn: ${data.churn_count || 0} commits | Last: ${data.last_modified_commit || "N/A"}`;

    const callersContainer = document.getElementById("callers-list");
    const calleesContainer = document.getElementById("callees-list");
    callersContainer.innerHTML = "";
    calleesContainer.innerHTML = "";

    const callerIds = graphData.edges
      .filter((edge) => edge.target === node.id)
      .map((edge) => edge.source);
    const calleeIds = graphData.edges
      .filter((edge) => edge.source === node.id)
      .map((edge) => edge.target);

    const callers = callerIds
      .map((callerId) => getNodeById(callerId, graphData))
      .filter(Boolean);
    const callees = calleeIds
      .map((calleeId) => getNodeById(calleeId, graphData))
      .filter(Boolean);

    if (callers.length === 0) {
      callersContainer.textContent = "No callers found.";
    } else {
      callers.forEach((caller) => callersContainer.appendChild(makeLinkedNodeItem(caller, graphData)));
    }

    if (callees.length === 0) {
      calleesContainer.textContent = "No callees found.";
    } else {
      callees.forEach((callee) => calleesContainer.appendChild(makeLinkedNodeItem(callee, graphData)));
    }
  } catch (error) {
    console.error(error);
  }
}

function showBlastInfo(blastData) {
  const blastContainer = document.getElementById("blast-info");
  blastContainer.innerHTML = "";
  const graphData = window.__CODEMAPPER_GRAPH__;

  const summary = document.createElement("div");
  summary.textContent = blastData.summary || "No blast data available.";
  summary.style.marginBottom = "12px";
  blastContainer.appendChild(summary);

  const depthMap = blastData.depth_map || {};
  const grouped = {};
  Object.entries(depthMap).forEach(([nodeId, depth]) => {
    grouped[depth] = grouped[depth] || [];
    grouped[depth].push(nodeId);
  });

  Object.keys(grouped)
    .sort((a, b) => Number(a) - Number(b))
    .forEach((depth) => {
      const details = document.createElement("details");
      details.className = "depth-group";
      details.open = Number(depth) <= 1;

      const summaryNode = document.createElement("summary");
      summaryNode.textContent = `Depth ${depth} - ${grouped[depth].length} node${grouped[depth].length === 1 ? "" : "s"}`;
      details.appendChild(summaryNode);

      const body = document.createElement("div");
      body.className = "depth-body";
      grouped[depth].forEach((nodeId) => {
        const node = getNodeById(nodeId, graphData) || { id: nodeId, name: nodeId };
        body.appendChild(makeLinkedNodeItem(node, graphData));
      });
      details.appendChild(body);

      blastContainer.appendChild(details);
    });
}

function hidePanel() {
  document.getElementById("detail-panel").classList.add("hidden");
  document.getElementById("detail-shell").classList.add("hidden");
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("panel-close-btn")?.addEventListener("click", hidePanel);
});

window.loadNodeDetail = loadNodeDetail;
window.showBlastInfo = showBlastInfo;
window.hidePanel = hidePanel;
window.getMutationBadgeHTML = getMutationBadgeHTML;
