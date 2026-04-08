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

    async function loadHistorySnapshot(index) {
      const state = getState();
      if (state.historyRequestInFlight || !state.historyCommits.length) {
        return;
      }

      const clamped = Math.max(0, Math.min(index, state.historyCommits.length - 1));
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
        updateGraph(snapshot);
        setMode("History");
        setStatus(`Viewing ${commit.short_hash} from ${commit.date}`);
        deps.setHistoryStatus(`${commit.short_hash} • ${commit.date}`);
      } catch (error) {
        console.error(error);
        deps.setHistoryStatus(error.message);
        setStatus(error.message);
      } finally {
        setState({ historyRequestInFlight: false });
        renderHistoryCommitInfo();
        syncHistoryButtons();
      }
    }

    async function openHistoryMode() {
      const state = getState();
      if (state.historyRequestInFlight || state.isScanning) {
        return;
      }

      try {
        if (!state.graphData) {
          setStatus("Scan a project first before opening evolution mode.");
          return;
        }

        if (!state.liveGraphSnapshot && state.graphData) {
          setState({
            liveGraphSnapshot: JSON.parse(JSON.stringify(state.graphData)),
            liveSelectedNodeId: state.selectedNodeId,
          });
        }

        document.getElementById("history-overlay")?.classList.add("visible");
        deps.setHistoryStatus("Loading timeline...");
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
      deps.stopHistoryPlayback();
      document.getElementById("history-overlay")?.classList.remove("visible");
      const state = getState();
      setState({
        historyMode: false,
        historyCommits: [],
        historyMeta: null,
      });
      state.historySnapshotCache.clear();
      if (state.liveGraphSnapshot) {
        updateGraph(state.liveGraphSnapshot);
        if (state.liveSelectedNodeId) {
          highlightNode(state.liveSelectedNodeId);
        }
      }
      setState({ liveGraphSnapshot: null, liveSelectedNodeId: null });
      renderHistoryCommitInfo();
      deps.setHistoryStatus("Load a scanned git repo to begin.");
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
        deps.stopHistoryPlayback();
        syncHistoryButtons();
        return;
      }
      const button = document.getElementById("history-play-btn");
      if (button) {
        button.textContent = "Pause Timeline";
      }
      const timer = setInterval(async () => {
        const current = getState();
        const nextIndex = current.historyIndex >= current.historyCommits.length - 1 ? 0 : current.historyIndex + 1;
        await loadHistorySnapshot(nextIndex);
      }, 1800);
      setState({ historyPlaybackTimer: timer });
      syncHistoryButtons();
    }

    return {
      closeHistoryMode,
      loadHistorySnapshot,
      openHistoryMode,
      toggleHistoryPlayback,
    };
  }

  globalScope.CodeWeaveHistoryController = { createHistoryController };
})(window);
