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
    const REQUEST_TIMEOUT_MS = 35000;
    let queuedHistoryIndex = null;
    let queuedHistoryOptions = null;
    let playbackLoopActive = false;
    let previousSnapshot = null;
    let transformWorker = null;
    let transformWorkerSeq = 0;

    function ensureTransformWorker() {
      if (transformWorker || typeof Worker === "undefined") {
        return transformWorker;
      }
      try {
        transformWorker = new Worker("/history_transform_worker.js");
      } catch (_error) {
        transformWorker = null;
      }
      return transformWorker;
    }

    async function computeSnapshotTransformWithWorker(nextSnapshot) {
      const worker = ensureTransformWorker();
      if (!worker) {
        const transition = buildSnapshotChangeSet(nextSnapshot);
        const snapshotWithColors = applyEvolutionMutationColors(nextSnapshot, transition);
        return { transition, snapshotWithColors };
      }

      const state = getState();
      const previousNodes = Array.isArray(previousSnapshot?.nodes) ? previousSnapshot.nodes : [];
      const nextNodes = Array.isArray(nextSnapshot?.nodes) ? nextSnapshot.nodes : [];
      const liveNodes = Array.isArray(state.liveGraphSnapshot?.nodes) ? state.liveGraphSnapshot.nodes : [];
      const requestId = `hist-transform-${Date.now()}-${transformWorkerSeq++}`;

      const result = await new Promise((resolve, reject) => {
        const onMessage = (event) => {
          const payload = event?.data || {};
          if (payload.requestId !== requestId) {
            return;
          }
          worker.removeEventListener("message", onMessage);
          worker.removeEventListener("error", onError);
          if (!payload.ok) {
            reject(new Error(payload.error || "history transform failed"));
            return;
          }
          resolve(payload.result || {});
        };
        const onError = (event) => {
          worker.removeEventListener("message", onMessage);
          worker.removeEventListener("error", onError);
          reject(new Error(event?.message || "history transform worker crashed"));
        };
        worker.addEventListener("message", onMessage);
        worker.addEventListener("error", onError);
        worker.postMessage({
          type: "compute_snapshot_transform",
          requestId,
          previousNodes,
          nextNodes,
          liveNodes,
        });
      });

      const transition = result.transition || { added: [], removed: [], updated: [] };
      const decoratedNodes = Array.isArray(result.decoratedNodes) ? result.decoratedNodes : nextNodes;
      return { transition, snapshotWithColors: { ...nextSnapshot, nodes: decoratedNodes } };
    }

    async function parseJsonResponse(response, options = {}) {
      const { allowNonJsonError = false } = options;
      const rawText = await response.text();
      if (!rawText) {
        return {};
      }
      try {
        return JSON.parse(rawText);
      } catch (_error) {
        if (allowNonJsonError && !response.ok) {
          return {};
        }
        if (!response.ok) {
          throw new Error(`Request failed (${response.status}).`);
        }
        throw new Error("Server returned an invalid JSON response.");
      }
    }

    async function fetchJsonWithFallback(urls, options = {}) {
      let lastError = "Request failed";
      for (const url of urls) {
        const response = await fetchWithTimeout(url, options);
        const data = await parseJsonResponse(response, { allowNonJsonError: true });
        if (response.ok) {
          return { response, data };
        }
        const maybeError = data && typeof data === "object" ? data.error : "";
        lastError = maybeError || `Request failed (${response.status}).`;
        if (response.status !== 404) {
          throw new Error(lastError);
        }
      }
      throw new Error(lastError);
    }

    async function fetchWithTimeout(url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        return await fetch(url, { ...options, signal: controller.signal });
      } finally {
        clearTimeout(timer);
      }
    }

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

    function normalizeDiffPayload(data, fromCommit, toCommit) {
      const payload = data && typeof data === "object" ? data : {};
      const changedFiles = Array.isArray(payload.changed_files)
        ? payload.changed_files
        : (Array.isArray(payload.changedFiles) ? payload.changedFiles : []);
      return {
        ...payload,
        from_commit: payload.from_commit || payload.fromCommit || fromCommit?.hash || "",
        to_commit: payload.to_commit || payload.toCommit || toCommit?.hash || "",
        shortstat: String(payload.shortstat || payload.summary || "Diff summary unavailable."),
        changed_files: changedFiles,
        status_counts: payload.status_counts || payload.statusCounts || {},
        diff_excerpt: String(payload.diff_excerpt || payload.diffExcerpt || ""),
        truncated: Boolean(payload.truncated),
      };
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
          let data = null;
          try {
            const { data: submitData } = await fetchJsonWithFallback(
              ["/api/v1/jobs/history-snapshot"],
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ commit_hash: commit.hash }),
              }
            );
            const jobId = String(submitData.job_id || "").trim();
            if (!jobId) {
              throw new Error("History snapshot job id was missing.");
            }

            for (let attempt = 0; attempt < 180; attempt += 1) {
              const delayMs = Math.min(1400, 240 + attempt * 20);
              await new Promise((resolve) => setTimeout(resolve, delayMs));
              const statusResponse = await fetchWithTimeout(`/api/v1/jobs/${jobId}`);
              const statusData = await parseJsonResponse(statusResponse);
              if (!statusResponse.ok) {
                throw new Error(statusData.error || "Failed to read history snapshot job status");
              }
              const status = String(statusData.status || "").toLowerCase();
              if (status === "queued" || status === "running") {
                continue;
              }
              if (status === "failed") {
                throw new Error(statusData.error || "History snapshot job failed");
              }
              const resultResponse = await fetchWithTimeout(`/api/v1/jobs/${jobId}/result`);
              const resultData = await parseJsonResponse(resultResponse);
              if (!resultResponse.ok) {
                throw new Error(resultData.error || "Failed to read history snapshot result");
              }
              data = resultData;
              break;
            }
            if (!data) {
              throw new Error("Timed out while loading history snapshot.");
            }
          } catch (jobError) {
            try {
              const fallback = await fetchJsonWithFallback([
                `/api/v1/history/${commit.hash}`,
                `/api/history/${commit.hash}`,
              ]);
              data = fallback.data;
            } catch (_fallbackError) {
              throw jobError;
            }
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
        const transformed = await computeSnapshotTransformWithWorker(snapshot);
        const transition = transformed.transition;
        const snapshotWithColors = transformed.snapshotWithColors;
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

        const { data } = await fetchJsonWithFallback(["/api/v1/history", "/api/history"]);

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
          `/api/v1/history-diff/${fromCommit.hash}/${toCommit.hash}`,
          `/api/history-diff/${fromCommit.hash}/${toCommit.hash}`,
          `/api/history/diff/${fromCommit.hash}/${toCommit.hash}`,
        ];
        let data = null;
        let requestError = null;
        for (const url of candidateUrls) {
          const response = await fetchWithTimeout(url);
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
        const normalized = normalizeDiffPayload(data, fromCommit, toCommit);
        deps.renderHistoryDiff?.(normalized);
        if (!silentStatus) {
          deps.setHistoryStatus(`Diff loaded for ${fromCommit.short_hash} -> ${toCommit.short_hash}.`);
        }
      } catch (error) {
        deps.renderHistoryDiff?.({
          shortstat: `Failed to load commit diff: ${error.message}`,
          changed_files: [],
          from_commit: fromCommit?.hash || "",
          to_commit: toCommit?.hash || "",
          truncated: false,
          status_counts: {},
          diff_excerpt: "",
        });
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
