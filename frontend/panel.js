function getNodeById(nodeId, graphData) {
  return graphData?.nodes?.find((node) => node.id === nodeId) || null;
}

function setActiveDetailNodeId(nodeId) {
  window.__CODEWEAVE_ACTIVE_DETAIL_NODE_ID__ = nodeId || null;
}

function getActiveDetailNodeId() {
  return window.__CODEWEAVE_ACTIVE_DETAIL_NODE_ID__ || null;
}

function getActiveDetailNode() {
  const nodeId = getActiveDetailNodeId();
  if (!nodeId) {
    return null;
  }
  return getNodeById(nodeId, window.__CODEMAPPER_GRAPH__);
}

function getSelectedGraphNode() {
  const selectedId =
    window.getSelectedGraphNodeId?.() ||
    window.__CODEWEAVE_SELECTED_NODE_ID__ ||
    window.__CODEWEAVE_TEST_API__?.getSelectedNodeId?.();
  if (!selectedId) {
    return null;
  }
  return getNodeById(selectedId, window.__CODEMAPPER_GRAPH__);
}

function resolveActionPlanNode() {
  return getActiveDetailNode() || getSelectedGraphNode() || lastDetailNodeSnapshot;
}

let activeChatNodeId = null;
let chatSessions = [];
let activeChatSessionId = null;
let isChatRequestInFlight = false;
let activeActionPlan = null;
let lastDetailNodeSnapshot = null;
const DEFAULT_CHAT_PROVIDER = "groq";
const CHAT_STORAGE_KEY = "codeweave-chat-sessions-v1";

async function fetchJsonWithFallback(urls, options = {}) {
  let lastError = "Request failed";
  for (const url of urls) {
    const response = await fetch(url, options);
    const rawText = await response.text();
    let payload = {};
    if (rawText) {
      try {
        payload = JSON.parse(rawText);
      } catch (_error) {
        payload = {};
      }
    }
    if (response.ok) {
      return { response, payload };
    }
    const maybeError = payload && typeof payload === "object" ? payload.error : "";
    lastError = maybeError || `Request failed (${response.status})`;
    if (response.status !== 404) {
      throw new Error(lastError);
    }
  }
  throw new Error(lastError);
}

function makeSessionId() {
  return `chat_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`;
}

function getChatElements() {
  return {
    messages: document.getElementById("chat-messages"),
    input: document.getElementById("chat-input"),
    sendButton: document.getElementById("chat-send-btn"),
    hint: document.getElementById("chat-hint"),
    newButton: document.getElementById("chat-new-btn"),
    clearAllButton: document.getElementById("chat-clear-all-btn"),
    sessionBar: document.getElementById("chat-session-bar"),
  };
}

function getActiveChatSession() {
  return chatSessions.find((session) => session.id === activeChatSessionId) || null;
}

function loadStoredChatSessions() {
  try {
    const raw = window.localStorage.getItem(CHAT_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    chatSessions = Array.isArray(parsed)
      ? parsed.map((session) => {
          const normalizedNodeName = formatReadableLabel(session.nodeName, "Project");
          const normalizedTitle =
            !session.title || / thread$/i.test(session.title)
              ? buildSessionTitle(normalizedNodeName)
              : session.title;
          return {
            ...session,
            nodeName: normalizedNodeName,
            title: normalizedTitle,
          };
        })
      : [];
  } catch (error) {
    console.error(error);
    chatSessions = [];
  }
}

function persistChatSessions() {
  window.localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(chatSessions));
}

function formatReadableLabel(value, fallback = "Project") {
  const raw = String(value || "").trim();
  if (!raw) {
    return fallback;
  }

  const withoutModulePrefix = raw.includes(".") ? raw.split(".").at(-1) : raw;
  const spaced = withoutModulePrefix
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();

  if (!spaced) {
    return fallback;
  }

  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function buildSessionTitle(nodeName, firstUserMessage = "") {
  if (firstUserMessage) {
    return firstUserMessage.length > 28 ? `${firstUserMessage.slice(0, 28)}...` : firstUserMessage;
  }
  return nodeName ? formatReadableLabel(nodeName, "Project") : "Project chat";
}

function createChatSession(nodeId, nodeName) {
  const scanTarget = window.__CODEWEAVE_SCAN_TARGET__ || null;
  const formattedNodeName = formatReadableLabel(nodeName, "Project");
  const session = {
    id: makeSessionId(),
    nodeId: nodeId || null,
    nodeName: formattedNodeName,
    scanTarget,
    title: buildSessionTitle(formattedNodeName),
    updatedAt: Date.now(),
    messages: [
      {
        role: "assistant",
        content: `Ready to help with ${formattedNodeName === "Project" ? "this project" : formattedNodeName}. Ask what breaks if you change something, where a feature should live, or which modules are tightly coupled.`,
      },
    ],
  };
  chatSessions.unshift(session);
  activeChatSessionId = session.id;
  persistChatSessions();
  return session;
}

async function activateChatSession(sessionId) {
  activeChatSessionId = sessionId;
  const session = getActiveChatSession();
  if (session) {
    activeChatNodeId = session.nodeId || null;
    if (session.scanTarget && typeof window.loadCachedScanTarget === "function") {
      await window.loadCachedScanTarget(session.scanTarget, { silent: true });
    }
    const graphData = window.__CODEMAPPER_GRAPH__;
    if (session.nodeId && graphData) {
      const node = getNodeById(session.nodeId, graphData);
      if (node) {
        if (window.highlightNode) {
          window.highlightNode(node.id);
        }
        document.getElementById("detail-panel")?.classList.remove("hidden");
        document.getElementById("detail-shell")?.classList.remove("hidden");
        loadNodeDetail(node, graphData);
      }
    }
  }
  renderChatSessions();
  renderChatMessages();
}

function deleteChatSession(sessionId) {
  const deletedWasActive = activeChatSessionId === sessionId;
  chatSessions = chatSessions.filter((session) => session.id !== sessionId);
  if (deletedWasActive) {
    const fallback = chatSessions[0] || null;
    activeChatSessionId = fallback ? fallback.id : null;
    activeChatNodeId = fallback ? (fallback.nodeId || null) : null;
  }
  persistChatSessions();
  renderChatSessions();
  renderChatMessages();
}

function ensureChatSession(nodeId, nodeName) {
  const matchingSession = chatSessions.find((session) => session.nodeId === nodeId);
  if (matchingSession) {
    activeChatSessionId = matchingSession.id;
    activeChatNodeId = matchingSession.nodeId;
    if (nodeName) {
      matchingSession.nodeName = formatReadableLabel(nodeName, matchingSession.nodeName || "Project");
      if (!matchingSession.title || matchingSession.title.endsWith("thread")) {
        matchingSession.title = buildSessionTitle(matchingSession.nodeName);
      }
    }
    matchingSession.scanTarget = matchingSession.scanTarget || window.__CODEWEAVE_SCAN_TARGET__ || null;
    persistChatSessions();
    return matchingSession;
  }
  return createChatSession(nodeId, nodeName);
}

function renderChatSessions() {
  const { sessionBar } = getChatElements();
  if (!sessionBar) {
    return;
  }

  sessionBar.innerHTML = "";
  if (chatSessions.length === 0) {
    const empty = document.createElement("div");
    empty.className = "chat-session-empty";
    empty.textContent = "Your saved chat threads will appear here.";
    sessionBar.appendChild(empty);
    return;
  }

  chatSessions
    .slice()
    .sort((left, right) => (right.updatedAt || 0) - (left.updatedAt || 0))
    .forEach((session) => {
      const button = document.createElement("button");
      button.className = `chat-session-chip ${session.id === activeChatSessionId ? "active" : ""}`;
      button.innerHTML = `
        <span class="chat-session-chip-inner">
          <span class="chat-session-label">${session.title || "Untitled thread"}</span>
          <span class="chat-session-meta">${session.nodeName || "Project"}</span>
        </span>
        <span class="chip-delete-btn" title="Delete conversation">x</span>
      `;
      button.addEventListener("click", async (event) => {
        if (event.target instanceof HTMLElement && event.target.classList.contains("chip-delete-btn")) {
          event.stopPropagation();
          deleteChatSession(session.id);
          return;
        }
        await activateChatSession(session.id);
      });
      sessionBar.appendChild(button);
    });
}

function renderChatMessages() {
  const { messages, hint } = getChatElements();
  if (!messages) {
    return;
  }

  const activeSession = getActiveChatSession();
  messages.innerHTML = "";
  if (!activeSession || !Array.isArray(activeSession.messages) || activeSession.messages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "chat-empty";
    empty.textContent = "Ask what breaks if you change a node, where a feature should live, or which modules are tightly coupled.";
    messages.appendChild(empty);
  } else {
    activeSession.messages.forEach((item) => {
      const message = document.createElement("div");
      message.className = `chat-message ${item.role === "assistant" ? "assistant" : "user"}`;
      message.textContent = item.content;
      messages.appendChild(message);
    });
  }

  if (hint) {
    hint.textContent = `Try: "What breaks if I change this?", "Where should I add feature X?", or "Which modules are tightly coupled?"`;
  }

  messages.scrollTop = messages.scrollHeight;
}

function resetChatForNode(nodeId, nodeName) {
  if (!nodeId) {
    return;
  }
  activeChatNodeId = nodeId;
  ensureChatSession(nodeId, nodeName);
  renderChatSessions();
  renderChatMessages();
}

function setChatPending(isPending) {
  isChatRequestInFlight = isPending;
  const { input, sendButton, newButton } = getChatElements();
  if (input) {
    input.disabled = isPending;
  }
  if (sendButton) {
    sendButton.disabled = isPending;
    sendButton.textContent = isPending ? "Thinking..." : "Ask AI";
  }
  if (newButton) {
    newButton.disabled = isPending;
  }
}

async function submitChatMessage() {
  const { input } = getChatElements();
  if (!input || isChatRequestInFlight) {
    return;
  }

  const message = input.value.trim();
  if (!message) {
    return;
  }

  let activeSession = getActiveChatSession();
  if (!activeSession) {
    activeSession = createChatSession(activeChatNodeId, activeChatNodeId ? "Selected node" : "Project");
  }
  activeSession.scanTarget = window.__CODEWEAVE_SCAN_TARGET__ || activeSession.scanTarget || null;
  if (!activeSession.title || activeSession.title.endsWith("thread")) {
    activeSession.title = buildSessionTitle(activeSession.nodeName, message);
  }
  activeSession.messages.push({ role: "user", content: message });
  activeSession.updatedAt = Date.now();
  input.value = "";
  persistChatSessions();
  renderChatSessions();
  renderChatMessages();
  setChatPending(true);

  try {
    const { payload: data } = await fetchJsonWithFallback(
      ["/api/v1/chat", "/api/chat"],
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message,
          node_id: activeSession.nodeId || activeChatNodeId,
          provider: DEFAULT_CHAT_PROVIDER,
          history: activeSession.messages,
        }),
      }
    );
    activeSession.messages.push({
      role: "assistant",
      content: data.answer || "No response generated.",
    });
  } catch (error) {
    activeSession.messages.push({
      role: "assistant",
      content: `Could not answer right now: ${error.message}`,
    });
  } finally {
    activeSession.updatedAt = Date.now();
    persistChatSessions();
    setChatPending(false);
    renderChatSessions();
    renderChatMessages();
  }
}

function createNewChatFromCurrentContext() {
  const nodeName = document.getElementById("node-name")?.textContent?.trim() || "Project";
  const session = createChatSession(activeChatNodeId, activeChatNodeId ? nodeName : "Project");
  renderChatSessions();
  renderChatMessages();
  getChatElements().input?.focus();
  return session;
}

function clearAllChatSessions() {
  chatSessions = [];
  activeChatSessionId = null;
  activeChatNodeId = null;
  persistChatSessions();
  renderChatSessions();
  renderChatMessages();
}

function initializeChatUi() {
  const { input, sendButton, newButton, clearAllButton } = getChatElements();
  if (!input || !sendButton || !newButton || !clearAllButton) {
    return;
  }

  loadStoredChatSessions();
  if (chatSessions.length > 0) {
    activeChatSessionId = chatSessions[0].id;
    activeChatNodeId = chatSessions[0].nodeId || null;
  }

  sendButton.addEventListener("click", submitChatMessage);
  newButton.addEventListener("click", createNewChatFromCurrentContext);
  clearAllButton.addEventListener("click", clearAllChatSessions);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitChatMessage();
    }
  });

  renderChatSessions();
  renderChatMessages();
}

function renderActionPlan(plan) {
  const container = document.getElementById("action-plan-content");
  const exportButton = document.getElementById("export-action-plan-btn");
  if (!container) {
    return;
  }

  if (!plan) {
    activeActionPlan = null;
    container.innerHTML = `<div class="muted">Select a node, then click Generate Plan to produce rollout steps and test focus.</div>`;
    if (exportButton) {
      exportButton.disabled = true;
    }
    return;
  }

  activeActionPlan = plan;
  const hotspots = Array.isArray(plan.risk_hotspots) ? plan.risk_hotspots : [];
  const tests = Array.isArray(plan.test_focus_areas) ? plan.test_focus_areas : [];
  const checklist = Array.isArray(plan.staged_checklist) ? plan.staged_checklist : [];

  container.innerHTML = `
    <div><strong>${plan.summary || "Action plan generated."}</strong></div>
    <div><span class="muted">Impacted files:</span> ${(plan.impacted_files || []).length} | <span class="muted">Modules:</span> ${(plan.impacted_modules || []).length}</div>
    <div><strong>Risk hotspots</strong></div>
    <ul>${hotspots.length ? hotspots.map((item) => `<li>${item.name} (${item.status}, churn ${item.churn_count})</li>`).join("") : "<li>No elevated hotspots detected.</li>"}</ul>
    <div><strong>Test focus</strong></div>
    <ul>${tests.length ? tests.map((item) => `<li>${item}</li>`).join("") : "<li>Add focused tests around impacted call paths.</li>"}</ul>
    <div><strong>Checklist</strong></div>
    <ul>${checklist.length ? checklist.map((item) => `<li>${item}</li>`).join("") : "<li>Validate changes before rollout.</li>"}</ul>
  `;
  if (exportButton) {
    exportButton.disabled = !String(plan.markdown || "").trim();
  }
}

async function ensureServerGraphCache() {
  const target = String(window.__CODEWEAVE_SCAN_TARGET__ || "").trim();
  if (!target) {
    return false;
  }
  const language =
    document.getElementById("language-input")?.value?.trim() || "python";
  await fetchJsonWithFallback(
    ["/api/v1/scan", "/api/scan"],
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: target, language }),
    }
  );
  return true;
}

async function generateActionPlan() {
  const node = resolveActionPlanNode();
  if (!node) {
    renderActionPlan(null);
    const container = document.getElementById("action-plan-content");
    if (container) {
      container.innerHTML = `<div class="muted">Select a concrete node first, then click Generate Plan.</div>`;
    }
    return;
  }

  const container = document.getElementById("action-plan-content");
  if (container) {
    container.innerHTML = `<div class="muted">Generating action plan...</div>`;
  }

  try {
    const candidateUrls = [
      `/api/action-plan/${node.id}`,
      `/api/action/plan/${node.id}`,
      `/api/action_plan/${node.id}`,
    ];
    let payload = null;
    let requestError = null;
    for (const url of candidateUrls) {
      let response = await fetch(url);
      const rawText = await response.text();
      let parsed = null;
      try {
        parsed = rawText ? JSON.parse(rawText) : {};
      } catch (_parseError) {
        parsed = null;
      }
      const isNoGraphError =
        !response.ok &&
        String(
          (parsed && typeof parsed === "object" && parsed.error) || rawText || ""
        ).toLowerCase().includes("no graph scanned yet");
      if (isNoGraphError) {
        try {
          await ensureServerGraphCache();
          response = await fetch(url);
          const retryText = await response.text();
          try {
            parsed = retryText ? JSON.parse(retryText) : {};
          } catch (_retryParseError) {
            parsed = null;
          }
          if (response.ok) {
            payload = parsed && typeof parsed === "object" ? parsed : null;
            requestError = null;
            break;
          }
          const retryPreview = String(retryText || "").replace(/\s+/g, " ").trim().slice(0, 220);
          requestError =
            (parsed && typeof parsed === "object" && parsed.error) ||
            retryPreview ||
            `Failed to generate action plan (${response.status})`;
          continue;
        } catch (restoreError) {
          requestError = restoreError.message || "Could not rebuild server graph cache.";
          continue;
        }
      }
      if (response.ok) {
        payload = parsed && typeof parsed === "object" ? parsed : null;
        requestError = null;
        break;
      }
      const textPreview = String(rawText || "").replace(/\s+/g, " ").trim().slice(0, 220);
      requestError =
        (parsed && typeof parsed === "object" && parsed.error) ||
        textPreview ||
        `Failed to generate action plan (${response.status})`;
    }
    if (!payload) {
      throw new Error(
        requestError ||
        "Action plan endpoint not found. Restart server to load latest routes."
      );
    }
    if (!payload || typeof payload !== "object") {
      throw new Error("Action plan endpoint returned invalid response format.");
    }
    renderActionPlan(payload);
  } catch (error) {
    renderActionPlan(null);
    const fallback = document.getElementById("action-plan-content");
    if (fallback) {
      fallback.innerHTML = `<div class="muted">Could not generate action plan: ${error.message}</div>`;
    }
  }
}

function exportActionPlanMarkdown() {
  if (!activeActionPlan || !String(activeActionPlan.markdown || "").trim()) {
    return;
  }

  const nodeName = String(activeActionPlan.node_name || "node")
    .replace(/[^a-z0-9]+/gi, "_")
    .replace(/^_+|_+$/g, "")
    .toLowerCase() || "node";
  const filename = `action_plan_${nodeName}.md`;
  const blob = new Blob([activeActionPlan.markdown], { type: "text/markdown;charset=utf-8" });
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
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
  const isLightTheme = document.body?.dataset?.theme === "light";
  const palette = isLightTheme
    ? {
      new: { background: "#dff8ed", color: "#136f4e", label: "NEW" },
      modified: { background: "#fff3cf", color: "#7a5500", label: "MODIFIED" },
      hotspot: { background: "#fde2e4", color: "#8e2834", label: "HOTSPOT" },
      stable: { background: "#e8eef2", color: "#355165", label: "STABLE" },
    }
    : {
      new: { background: "#003f2d", color: "#00ff88", label: "NEW" },
      modified: { background: "#4f3f00", color: "#ffcc00", label: "MODIFIED" },
      hotspot: { background: "#4a1515", color: "#ff5555", label: "HOTSPOT" },
      stable: { background: "#333333", color: "#dddddd", label: "STABLE" },
    };
  const badge = palette[normalized] || palette.stable;
  return `<span class="mutation-badge" style="background:${badge.background};color:${badge.color};">${badge.label}</span>`;
}

function renderProjectInsights(insights) {
  const container = document.getElementById("project-insights");
  if (!container) {
    return;
  }

  if (!insights || typeof insights !== "object" || !Object.keys(insights).length) {
    container.textContent = "Scan a project to load architecture insights.";
    return;
  }

  const summary = insights.summary || "Architecture insights loaded.";
  const fanIn = Array.isArray(insights.fan_in) ? insights.fan_in.slice(0, 3) : [];
  const fanOut = Array.isArray(insights.fan_out) ? insights.fan_out.slice(0, 3) : [];
  const coupling = Array.isArray(insights.tight_coupling) ? insights.tight_coupling.slice(0, 2) : [];
  const deadCode = Array.isArray(insights.dead_code_candidates) ? insights.dead_code_candidates.slice(0, 3) : [];

  const lines = [`<div>${summary}</div>`];
  if (fanIn.length) {
    lines.push(`<div><strong>High fan-in:</strong> ${fanIn.map((item) => `${item.name} (${item.score})`).join(", ")}</div>`);
  }
  if (fanOut.length) {
    lines.push(`<div><strong>High fan-out:</strong> ${fanOut.map((item) => `${item.name} (${item.score})`).join(", ")}</div>`);
  }
  if (coupling.length) {
    lines.push(`<div><strong>Tight coupling:</strong> ${coupling.map((item) => `${item.left} ↔ ${item.right}`).join(", ")}</div>`);
  }
  if (deadCode.length) {
    lines.push(`<div><strong>Dead-code candidates:</strong> ${deadCode.map((item) => item.name).join(", ")}</div>`);
  }

  container.innerHTML = lines.join("");
}

function ensureDetailPanelVisible() {
  const shell = document.getElementById("detail-shell");
  const panel = document.getElementById("detail-panel");
  shell?.classList.remove("hidden");
  panel?.classList.remove("hidden");

  const frame = document.querySelector(".main-frame");
  if (frame?.dataset?.view === "graph") {
    document.getElementById("view-split-btn")?.click();
  }
}

function setDetailActionButtonsEnabled(enabled) {
  const blastButton = document.getElementById("simulate-blast-btn");
  const sourceButton = document.getElementById("view-source-btn");
  if (blastButton) {
    blastButton.disabled = !enabled;
  }
  if (sourceButton) {
    sourceButton.disabled = !enabled;
  }
}

async function loadNodeDetail(node, graphData) {
  if (!node) {
    return;
  }
  try {
    if (window.highlightNode) {
      window.highlightNode(node.id);
    }
    ensureDetailPanelVisible();
    setActiveDetailNodeId(node.id);
    lastDetailNodeSnapshot = { ...node };
    setDetailActionButtonsEnabled(true);

    const nameEl = document.getElementById("node-name");
    const fileEl = document.getElementById("node-file");
    const summaryEl = document.getElementById("node-summary");
    const badgeEl = document.getElementById("mutation-badge");
    const churnEl = document.getElementById("churn-info");

    if (nameEl) {
      nameEl.textContent = node.name || "Unknown Node";
    }
    if (fileEl) {
      fileEl.textContent = `${node.file || "unknown"}:${node.line || 0}`;
    }
    if (summaryEl) {
      summaryEl.textContent = node.summary || "Loading detailed node summary...";
    }
    if (badgeEl) {
      badgeEl.innerHTML = getMutationBadgeHTML(node.mutation_status);
    }
    if (churnEl) {
      churnEl.textContent = `Churn: ${node.churn_count || 0} commits | Last: ${node.last_modified_commit || "N/A"}`;
    }
    resetChatForNode(node.id, node.name);
    renderActionPlan(null);

    const response = await fetch(`/api/node/${node.id}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Failed to load node details");
    }
    lastDetailNodeSnapshot = { ...data };

    if (nameEl) {
      nameEl.textContent = data.name || "Unknown Node";
    }
    if (fileEl) {
      fileEl.textContent = `${data.file || "unknown"}:${data.line || 0}`;
    }
    if (summaryEl) {
      summaryEl.textContent = data.summary || "No summary available.";
    }
    if (badgeEl) {
      badgeEl.innerHTML = getMutationBadgeHTML(data.mutation_status);
    }
    if (churnEl) {
      churnEl.textContent = `Churn: ${data.churn_count || 0} commits | Last: ${data.last_modified_commit || "N/A"}`;
    }
    resetChatForNode(node.id, data.name || node.name);

    const callersContainer = document.getElementById("callers-list");
    const calleesContainer = document.getElementById("callees-list");
    callersContainer.innerHTML = "";
    calleesContainer.innerHTML = "";

    const edges = Array.isArray(graphData?.edges) ? graphData.edges : [];
    const callerIds = edges
      .filter((edge) => edge.target === node.id)
      .map((edge) => edge.source);
    const calleeIds = edges
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
    const summaryEl = document.getElementById("node-summary");
    if (summaryEl) {
      summaryEl.textContent = `Could not load full node details: ${error.message}`;
    }
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
  setActiveDetailNodeId(null);
  lastDetailNodeSnapshot = null;
  setDetailActionButtonsEnabled(false);
  renderActionPlan(null);
}

function bindDetailPanelActions() {
  document.getElementById("simulate-blast-btn")?.addEventListener("click", () => {
    const node = resolveActionPlanNode();
    if (node && window.triggerBlastRadius) {
      window.triggerBlastRadius(node);
      return;
    }
    const blastInfo = document.getElementById("blast-info");
    if (blastInfo) {
      blastInfo.innerHTML = `<div class="muted">Select a node first, then click Simulate Blast Radius.</div>`;
    }
  });
  document.getElementById("clear-blast-btn")?.addEventListener("click", () => {
    if (window.clearBlastRadius) {
      window.clearBlastRadius();
    }
  });
  document.getElementById("view-source-btn")?.addEventListener("click", () => {
    const node = getActiveDetailNode();
    if (node && window.openMonacoModal) {
      window.openMonacoModal(node.source_code || "# No source code available");
    }
  });
  document.getElementById("generate-action-plan-btn")?.addEventListener("click", generateActionPlan);
  document.getElementById("export-action-plan-btn")?.addEventListener("click", exportActionPlanMarkdown);
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("panel-close-btn")?.addEventListener("click", hidePanel);
  bindDetailPanelActions();
  initializeChatUi();
  renderProjectInsights(window.__CODEWEAVE_INSIGHTS__ || null);
});

window.loadNodeDetail = loadNodeDetail;
window.showBlastInfo = showBlastInfo;
window.hidePanel = hidePanel;
window.getMutationBadgeHTML = getMutationBadgeHTML;
window.renderProjectInsights = renderProjectInsights;
