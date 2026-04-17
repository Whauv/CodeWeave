(function attachHistoryController(globalScope) {
  function createHistoryController(deps) {
    const {
      getState,
      setState,
      setStatus,
      setMode,
      renderHistoryCommitInfo,
      syncHistoryButtons,
      updateGraph,
      highlightNode,
    } = deps;
    let queuedHistoryIndex = null;
    let queuedHistoryOptions = null;
    let playbackLoopActive = false;
    let previousSnapshot = null;

    function normalizePathTail(pathValue) {
      const normalized = String(pathValue || "").replace(/\\/g, "/").toLowerCase().trim();
      const parts = normalized.split("/").filter(Boolean);
      if (!parts.length) {
        return "";
      }
      const snapshotMarkers = new Set([".codeweave_tmp", "history_snapshots_runtime", "codeweave_repo_cache", "repo"]);
      for (let index = 0; index < parts.length; index += 1) {
        const part = parts[index];
        if (snapshotMarkers.has(part) && index < parts.length - 1) {
          if (part === "repo") {
            return parts.slice(index + 1).join("/");
          }
          if (index + 2 < parts.length) {
            return parts.slice(index + 2).join("/");
          }
          return parts.slice(index + 1).join("/");
        }
      }
      return parts.slice(-4).join("/");
    }

    function normalizePathLoose(pathValue) {
      const normalized = String(pathValue || "").replace(/\\/g, "/").toLowerCase();
      const parts = normalized.split("/").filter(Boolean);
      if (!parts.length) {
        return "";
      }
      const repoIdx = parts.lastIndexOf("repo");
      if (repoIdx >= 0 && repoIdx < parts.length - 1) {
        return parts.slice(repoIdx + 1).join("/");
      }
      return parts.slice(-3).join("/");
    }

    function getNodeIdentity(node) {
      return `${String(node?.name || "").toLowerCase()}::${normalizePathTail(node?.file)}`;
    }

    function getNodeIdentityLoose(node) {
      return `${String(node?.name || "").toLowerCase()}::${normalizePathLoose(node?.file)}`;
    }

    function buildSnapshotChangeSet(nextSnapshot) {
      const beforeNodes = Array.isArray(previousSnapshot?.nodes) ? previousSnapshot.nodes : [];
      const afterNodes = Array.isArray(nextSnapshot?.nodes) ? nextSnapshot.nodes : [];
      const beforeByIdentity = new Map(beforeNodes.map((node) => [getNodeIdentity(node), node]));
      const afterByIdentity = new Map(afterNodes.map((node) => [getNodeIdentity(node), node]));
      const added = [];
      const removed = [];
      const updated = [];

      afterByIdentity.forEach((afterNode, identity) => {
        const beforeNode = beforeByIdentity.get(identity);
        if (!beforeNode) {
          added.push(afterNode.id);
          return;
        }
        const changed =
          (beforeNode.line || 0) !== (afterNode.line || 0) ||
          String(beforeNode.source_code || "") !== String(afterNode.source_code || "");
        if (changed) {
          updated.push(afterNode.id);
        }
      });

      beforeByIdentity.forEach((_beforeNode, identity) => {
        if (!afterByIdentity.has(identity)) {
          removed.push(identity);
        }
      });

      return { added, removed, updated };
    }

    function applyEvolutionMutationColors(snapshot, transition) {
      const state = getState();
      const liveNodes = Array.isArray(state.liveGraphSnapshot?.nodes) ? state.liveGraphSnapshot.nodes : [];
      const byStrict = new Map();
      const byLoose = new Map();
      const byName = new Map();

      liveNodes.forEach((node) => {
        byStrict.set(getNodeIdentity(node), node);
        byLoose.set(getNodeIdentityLoose(node), node);
        const key = String(node?.name || "").toLowerCase();
        if (!byName.has(key)) {
          byName.set(key, []);
        }
        byName.get(key).push(node);
      });

      const addedIds = new Set(transition?.added || []);
      const updatedIds = new Set(transition?.updated || []);
      const coloredNodes = (snapshot.nodes || []).map((node) => {
        const nextNode = { ...node };

        const strictMatch = byStrict.get(getNodeIdentity(node));
        const looseMatch = byLoose.get(getNodeIdentityLoose(node));
        const sameName = byName.get(String(node?.name || "").toLowerCase()) || [];
        const sameNameMatch = sameName.length === 1 ? sameName[0] : null;
        const liveMatch = strictMatch || looseMatch || sameNameMatch;
        if (liveMatch?.mutation_color) {
          nextNode.mutation_color = liveMatch.mutation_color;
          nextNode.mutation_status = liveMatch.mutation_status || nextNode.mutation_status || "stable";
        } else if (updatedIds.has(node.id)) {
          nextNode.mutation_status = "modified";
          nextNode.mutation_color = "#ffcc00";
        } else if (addedIds.has(node.id)) {
          nextNode.mutation_status = "stable";
          nextNode.mutation_color = "#aaaaaa";
        } else if (!nextNode.mutation_color) {
          nextNode.mutation_status = nextNode.mutation_status || "stable";
          nextNode.mutation_color = "#aaaaaa";
        }
        return nextNode;
      });

      return { ...snapshot, nodes: coloredNodes };
    }

    async function loadHistorySnapshot(index, options = {}) {
      const skipDiff = Boolean(options.skipDiff);
      const state = getState();
      if (!state.historyCommits.length) {
        return;
      }

      const clamped = Math.max(0, Math.min(index, state.historyCommits.length - 1));
      if (state.historyRequestInFlight) {
        queuedHistoryIndex = clamped;
        queuedHistoryOptions = options;
        return;
      }
      const commit = state.historyCommits[clamped];
      if (!commit) {
        return;
      }

      setState({ historyRequestInFlight: true, historyIndex: clamped });
      renderHistoryCommitInfo();
      deps.setHistoryStatus(`Loading ${commit.short_hash}...`);
      syncHistoryButtons();

      try {
        let snapshot = getState().historySnapshotCache.get(commit.hash);
        if (!snapshot) {
          const response = await fetch(`/api/history/${commit.hash}`);
          const data = await response.json();
          if (!response.ok) {
            throw new Error(data.error || "Failed to load history snapshot");
          }
          snapshot = data;
          getState().historySnapshotCache.set(commit.hash, snapshot);
        }

        setState({
          historyMode: true,
          selectedNodeId: null,
          currentFocusedClusterKey: null,
          currentSearchQuery: "",
        });
        const searchInput = document.getElementById("search-input");
        if (searchInput) {
          searchInput.value = "";
        }
        const transition = buildSnapshotChangeSet(snapshot);
        const snapshotWithColors = applyEvolutionMutationColors(snapshot, transition);
        updateGraph(snapshotWithColors, { instantRender: true, historySnapshot: true });
        deps.applyHistoryTransition?.(transition);
        setMode("History");
        setStatus(`Viewing ${commit.short_hash} from ${commit.date}`);
        deps.setHistoryStatus(
          `${commit.short_hash} • ${commit.date} • +${transition.added.length} ~${transition.updated.length} -${transition.removed.length}`
        );
        previousSnapshot = snapshotWithColors;
        if (!skipDiff) {
          loadHistoryDiff({ silentStatus: true });
        }
      } catch (error) {
        console.error(error);
        deps.setHistoryStatus(error.message);
        setStatus(error.message);
      } finally {
        setState({ historyRequestInFlight: false });
        renderHistoryCommitInfo();
        syncHistoryButtons();
        if (queuedHistoryIndex !== null) {
          const nextIndex = queuedHistoryIndex;
          const nextOptions = queuedHistoryOptions || {};
          queuedHistoryIndex = null;
          queuedHistoryOptions = null;
          if (nextIndex !== getState().historyIndex) {
            loadHistorySnapshot(nextIndex, nextOptions);
          }
        }
      }
    }

    function haltHistoryPlayback() {
      playbackLoopActive = false;
      deps.stopHistoryPlayback();
    }

    async function openHistoryMode() {
      const state = getState();
      if (state.historyRequestInFlight || state.isScanning) {
        return;
      }
      haltHistoryPlayback();
      queuedHistoryIndex = null;
      queuedHistoryOptions = null;
      previousSnapshot = null;

      try {
        if (!state.graphData) {
          setStatus("Scan a project first before opening evolution mode.");
          return;
        }

        setState({
          liveGraphSnapshot: JSON.parse(JSON.stringify(state.graphData)),
          liveSelectedNodeId: state.selectedNodeId,
        });

        document.getElementById("history-overlay")?.classList.add("visible");
        deps.setHistoryStatus("Loading timeline...");
        deps.renderHistoryDiff?.({
          shortstat: "Click Show Diff to compare commits.",
          changed_files: [],
          from_commit: "",
          to_commit: "",
          truncated: false,
        });
        syncHistoryButtons();

        const response = await fetch("/api/history");
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || "Could not load project history");
        }

        const commits = Array.isArray(data.commits) ? data.commits : [];
        setState({
          historyCommits: commits,
          historyMeta: { ...(data.history_meta || {}), source_kind: data.source_kind || "local" },
          historyIndex: commits.length ? commits.length - 1 : 0,
        });
        getState().historySnapshotCache.clear();
        renderHistoryCommitInfo();

        if (!commits.length) {
          deps.setHistoryStatus("No git history available.");
          syncHistoryButtons();
          return;
        }

        if (commits.length === 1) {
          const historyMeta = getState().historyMeta || {};
          const attemptedFetch = Boolean(historyMeta.attempted_fetch);
          const fetched = Boolean(historyMeta.fetched);
          const stillShallow = Boolean(historyMeta.is_shallow);
          const fetchFailed = Boolean(historyMeta.fetch_error);
          const remoteBranches = Array.isArray(historyMeta.branch_names) ? historyMeta.branch_names.length : 0;

          if (historyMeta.source_kind === "github" && remoteBranches > 1) {
            deps.setHistoryStatus("Remote branches were found, but only one reachable commit was returned.");
          } else if (attemptedFetch && fetched) {
            deps.setHistoryStatus("Only one commit was available after deepening history.");
          } else if (attemptedFetch && fetchFailed) {
            deps.setHistoryStatus("Could not fetch more commit history for this repo.");
          } else if (stillShallow) {
            deps.setHistoryStatus("Only one commit is currently available from a shallow history.");
          } else {
            deps.setHistoryStatus("This repository appears to contain only one reachable commit.");
          }
          syncHistoryButtons();
          await loadHistorySnapshot(getState().historyIndex);
          return;
        }

        deps.setHistoryStatus(`Loaded ${commits.length} commits.`);
        syncHistoryButtons();
        await loadHistorySnapshot(getState().historyIndex);
      } catch (error) {
        console.error(error);
        setStatus(error.message);
        deps.setHistoryStatus(error.message);
        syncHistoryButtons();
      }
    }

    function closeHistoryMode() {
      haltHistoryPlayback();
      queuedHistoryIndex = null;
      queuedHistoryOptions = null;
      previousSnapshot = null;
      document.getElementById("history-overlay")?.classList.remove("visible");
      const state = getState();
      const restoredLiveSnapshot = state.liveGraphSnapshot
        ? JSON.parse(JSON.stringify(state.liveGraphSnapshot))
        : null;
      const restoredSelectedNodeId = state.liveSelectedNodeId;
      setState({
        historyMode: false,
        historyCommits: [],
        historyMeta: null,
      });
      state.historySnapshotCache.clear();
      if (restoredLiveSnapshot) {
        updateGraph(restoredLiveSnapshot, { instantRender: true, restoreLive: true });
        deps.restoreLiveVisualState?.();
        if (restoredSelectedNodeId) {
          highlightNode(restoredSelectedNodeId);
        }
        setStatus(`Returned to live graph with ${restoredLiveSnapshot.nodes?.length || 0} nodes.`);
      }
      setState({ liveGraphSnapshot: null, liveSelectedNodeId: null });
      renderHistoryCommitInfo();
      deps.setHistoryStatus("Load a scanned git repo to begin.");
      deps.renderHistoryDiff?.({
        shortstat: "Click Show Diff to compare commits.",
        changed_files: [],
        from_commit: "",
        to_commit: "",
        truncated: false,
      });
      syncHistoryButtons();
    }

    function toggleHistoryPlayback() {
      const state = getState();
      if (state.historyCommits.length <= 1) {
        deps.setHistoryStatus("Need at least two commits to play the timeline.");
        syncHistoryButtons();
        return;
      }
      if (state.historyPlaybackTimer) {
        haltHistoryPlayback();
        syncHistoryButtons();
        return;
      }

      const stepDelay = Math.max(1800, Number(state.historyPlaybackDelay) || 7000);
      playbackLoopActive = true;
      const timerToken = Date.now();
      setState({ historyPlaybackTimer: timerToken });
      deps.setHistoryStatus(`Playing timeline at ${(stepDelay / 1000).toFixed(1)}s per commit.`);

      const playbackLoop = async () => {
        while (playbackLoopActive && getState().historyPlaybackTimer === timerToken) {
          await new Promise((resolve) => setTimeout(resolve, Math.max(1800, Number(getState().historyPlaybackDelay) || 7000)));
          const current = getState();
          if (!playbackLoopActive || current.historyPlaybackTimer !== timerToken) {
            break;
          }
          const nextIndex =
            current.historyIndex >= current.historyCommits.length - 1 ? 0 : current.historyIndex + 1;
          await loadHistorySnapshot(nextIndex);
        }
      };
      playbackLoop();
      syncHistoryButtons();
    }

    function restartHistoryPlayback() {
      const state = getState();
      if (!state.historyPlaybackTimer) {
        return;
      }
      haltHistoryPlayback();
      syncHistoryButtons();
      toggleHistoryPlayback();
    }

    function stepHistory(direction) {
      const state = getState();
      if (!state.historyCommits.length) {
        return;
      }
      const delta = direction < 0 ? -1 : 1;
      const nextIndex = Math.max(0, Math.min(state.historyCommits.length - 1, state.historyIndex + delta));
      loadHistorySnapshot(nextIndex);
    }

    async function loadHistoryDiff(options = {}) {
      const silentStatus = Boolean(options.silentStatus);
      const state = getState();
      if (state.historyCommits.length < 2) {
        deps.renderHistoryDiff?.({
          shortstat: "Need at least two commits to view a diff.",
          changed_files: [],
          from_commit: "",
          to_commit: "",
          truncated: false,
        });
        return;
      }

      const toCommit = state.historyCommits[state.historyIndex];
      const fromCommit = state.historyCommits[Math.max(0, state.historyIndex - 1)];
      if (!toCommit || !fromCommit || toCommit.hash === fromCommit.hash) {
        deps.renderHistoryDiff?.({
          shortstat: "No earlier commit available for comparison.",
          changed_files: [],
          from_commit: fromCommit?.hash || "",
          to_commit: toCommit?.hash || "",
          truncated: false,
        });
        return;
      }

      if (!silentStatus) {
        deps.setHistoryStatus(`Loading diff ${fromCommit.short_hash} -> ${toCommit.short_hash}...`);
      }
      try {
        const parseApiPayload = async (response) => {
          const text = await response.text();
          if (!text) {
            return { data: {}, rawText: "" };
          }
          try {
            return { data: JSON.parse(text), rawText: text };
          } catch (_parseError) {
            return { data: null, rawText: text };
          }
        };

        const candidateUrls = [
          `/api/history-diff/${fromCommit.hash}/${toCommit.hash}`,
          `/api/history/diff/${fromCommit.hash}/${toCommit.hash}`,
        ];
        let data = null;
        let requestError = null;
        for (const url of candidateUrls) {
          const response = await fetch(url);
          const { data: payload, rawText } = await parseApiPayload(response);
          if (response.ok) {
            data = payload && typeof payload === "object"
              ? payload
              : {
                  shortstat: "Diff endpoint returned non-JSON response.",
                  changed_files: [],
                  from_commit: fromCommit.hash,
                  to_commit: toCommit.hash,
                  truncated: false,
                  diff_excerpt: rawText || "",
                };
            requestError = null;
            break;
          }
          const maybeError = payload && typeof payload === "object" ? payload.error : null;
          const textPreview = String(rawText || "").replace(/\s+/g, " ").trim().slice(0, 220);
          requestError = maybeError || textPreview || `Diff endpoint returned ${response.status}`;
        }
        if (!data) {
          throw new Error(requestError || "Failed to load commit diff");
        }
        deps.renderHistoryDiff?.(data);
        if (!silentStatus) {
          deps.setHistoryStatus(`Diff loaded for ${fromCommit.short_hash} -> ${toCommit.short_hash}.`);
        }
      } catch (error) {
        if (!silentStatus) {
          deps.setHistoryStatus(error.message);
        }
      }
    }

    return {
      closeHistoryMode,
      loadHistorySnapshot,
      loadHistoryDiff,
      openHistoryMode,
      restartHistoryPlayback,
      stepHistory,
      toggleHistoryPlayback,
    };
  }

  globalScope.CodeWeaveHistoryController = { createHistoryController };
})(window);
