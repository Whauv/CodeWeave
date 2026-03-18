function getNodeById(nodeId, graphData) {
  return graphData?.nodes?.find((node) => node.id === nodeId) || null;
}

let activeChatNodeId = null;
let chatSessions = [];
let activeChatSessionId = null;
let isChatRequestInFlight = false;
const DEFAULT_CHAT_PROVIDER = "groq";
const CHAT_STORAGE_KEY = "codeweave-chat-sessions-v1";

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
    chatSessions = Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    console.error(error);
    chatSessions = [];
  }
}

function persistChatSessions() {
  window.localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(chatSessions));
}

function buildSessionTitle(nodeName, firstUserMessage = "") {
  if (firstUserMessage) {
    return firstUserMessage.length > 28 ? `${firstUserMessage.slice(0, 28)}...` : firstUserMessage;
  }
  return nodeName ? `${nodeName} thread` : "Project thread";
}

function createChatSession(nodeId, nodeName) {
  const session = {
    id: makeSessionId(),
    nodeId: nodeId || null,
    nodeName: nodeName || "Project",
    title: buildSessionTitle(nodeName || "Project"),
    updatedAt: Date.now(),
    messages: [
      {
        role: "assistant",
        content: `Ready to help with ${nodeName || "this project"}. Ask about logic, dependencies, risks, or impact.`,
      },
    ],
  };
  chatSessions.unshift(session);
  activeChatSessionId = session.id;
  persistChatSessions();
  return session;
}

function activateChatSession(sessionId) {
  activeChatSessionId = sessionId;
  const session = getActiveChatSession();
  if (session) {
    activeChatNodeId = session.nodeId || null;
  }
  renderChatSessions();
  renderChatMessages();
}

function ensureChatSession(nodeId, nodeName) {
  const matchingSession = chatSessions.find((session) => session.nodeId === nodeId);
  if (matchingSession) {
    activeChatSessionId = matchingSession.id;
    activeChatNodeId = matchingSession.nodeId;
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
        <span class="chat-session-label">${session.title || "Untitled thread"}</span>
        <span class="chat-session-meta">${session.nodeName || "Project"}</span>
      `;
      button.addEventListener("click", () => {
        activateChatSession(session.id);
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
    empty.textContent = "Ask about the selected node or overall project architecture.";
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
    hint.textContent = `Uses provider: ${DEFAULT_CHAT_PROVIDER} (configurable later).`;
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
    const response = await fetch("/api/chat", {
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
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Chat request failed");
    }
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

function initializeChatUi() {
  const { input, sendButton, newButton } = getChatElements();
  if (!input || !sendButton || !newButton) {
    return;
  }

  loadStoredChatSessions();
  if (chatSessions.length > 0) {
    activeChatSessionId = chatSessions[0].id;
    activeChatNodeId = chatSessions[0].nodeId || null;
  }

  sendButton.addEventListener("click", submitChatMessage);
  newButton.addEventListener("click", createNewChatFromCurrentContext);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submitChatMessage();
    }
  });

  renderChatSessions();
  renderChatMessages();
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
    resetChatForNode(node.id, data.name || node.name);

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
  initializeChatUi();
});

window.loadNodeDetail = loadNodeDetail;
window.showBlastInfo = showBlastInfo;
window.hidePanel = hidePanel;
window.getMutationBadgeHTML = getMutationBadgeHTML;
