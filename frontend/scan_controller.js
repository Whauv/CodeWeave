(function attachScanController(globalScope) {
  function createScanController(deps) {
    const {
      getState,
      setState,
      browserStore,
      buildScanHistoryLabel,
      isGithubUrl,
      scanHistoryStorageKey,
      setStatus,
      setMode,
      renderHistoryCommitInfo,
      syncHistoryButtons,
      updateGraph,
    } = deps;
    const REQUEST_TIMEOUT_MS = 35000;

    function getDom(id) {
      return document.getElementById(id);
    }

    function updateState(nextState) {
      return setState(nextState);
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

    function resetScanMetrics() {
      const nodes = getDom("metric-nodes");
      const edges = getDom("metric-edges");
      const selected = getDom("metric-selected");
      if (nodes) {
        nodes.textContent = "0";
      }
      if (edges) {
        edges.textContent = "0";
      }
      if (selected) {
        selected.textContent = "None";
      }
    }

    function setScanOverlayMessage(message) {
      const element = getDom("scan-overlay-copy");
      if (element) {
        element.textContent = message;
      }
    }

    function showScanOverlay() {
      let state = getState();
      const overlay = getDom("scan-overlay");
      overlay?.classList.add("visible");
      const frames = [
        "Parsing source files, linking calls, and preparing the dependency map.",
        "Resolving functions, classes, and import relationships.",
        "Enriching the graph and preparing the UI snapshot.",
      ];
      let index = 0;
      setScanOverlayMessage(frames[index]);
      clearInterval(state.scanFrameTimer);
      const timer = setInterval(() => {
        index = (index + 1) % frames.length;
        setScanOverlayMessage(frames[index]);
      }, 1200);
      state = updateState({ scanFrameTimer: timer });
    }

    function hideScanOverlay() {
      const state = getState();
      getDom("scan-overlay")?.classList.remove("visible");
      clearInterval(state.scanFrameTimer);
      updateState({ scanFrameTimer: null });
    }

    async function loadLanguageOptions() {
      const select = getDom("language-input");
      if (!select) {
        return;
      }
      try {
        const response = await fetch("/api/languages");
        const data = await response.json();
        if (!response.ok || !Array.isArray(data.languages)) {
          return;
        }
        const latestState = getState();
        const preferredValue = select.value || latestState.currentLanguage || "python";
        select.innerHTML = "";
        data.languages.forEach((item) => {
          const option = document.createElement("option");
          option.value = String(item.language || "python");
          option.textContent = item.ready === false ? `${item.label} (stub)` : item.label;
          select.appendChild(option);
        });
        const availableValues = new Set(data.languages.map((item) => String(item.language || "python")));
        const resolvedValue = availableValues.has(preferredValue) ? preferredValue : latestState.currentLanguage;
        select.value = resolvedValue || "python";
        updateState({ currentLanguage: select.value || "python" });
      } catch (error) {
        console.error(error);
      }
    }

    function loadScanHistory() {
      updateState({ scanHistory: browserStore.loadLocalList(scanHistoryStorageKey) });
    }

    function persistScanHistory() {
      browserStore.persistLocalList(scanHistoryStorageKey, getState().scanHistory);
    }

    async function loadCachedScanTarget(target, options = {}) {
      const normalized = String(target || "").trim();
      if (!normalized) {
        return false;
      }
      const snapshot = await browserStore.getGraphSnapshot(normalized);
      if (!snapshot?.data) {
        return false;
      }

      updateState({ currentScanTarget: normalized });
      globalScope.__CODEWEAVE_SCAN_TARGET__ = normalized;
      updateGraph(snapshot.data);
      if (!options.silent) {
        setStatus(`Loaded cached graph for ${normalized}`);
      }
      renderScanHistory();
      return true;
    }

    function renderScanHistory() {
      const state = getState();
      const container = getDom("scan-history-list");
      if (!container) {
        return;
      }
      container.innerHTML = "";
      if (!state.scanHistory.length) {
        const empty = document.createElement("div");
        empty.className = "scan-history-empty";
        empty.textContent = "Scanned project paths and GitHub repos will appear here.";
        container.appendChild(empty);
        return;
      }

      state.scanHistory.forEach((entry) => {
        const button = document.createElement("button");
        button.className = `scan-history-chip ${entry.target === state.currentScanTarget ? "active" : ""}`;
        button.innerHTML = `<span class="scan-history-chip-inner"><span class="scan-history-kind">${entry.kind}</span><span class="scan-history-label">${entry.label}</span></span><span class="chip-delete-btn" title="Remove from recent scans">x</span>`;
        button.title = entry.target;
        button.addEventListener("click", async (event) => {
          if (event.target instanceof HTMLElement && event.target.classList.contains("chip-delete-btn")) {
            event.stopPropagation();
            deleteScanHistoryEntry(entry.target);
            return;
          }
          const pathInput = getDom("path-input");
          if (pathInput) {
            pathInput.value = entry.target;
          }
          updateState({ currentScanTarget: entry.target });
          renderScanHistory();
          const loaded = await loadCachedScanTarget(entry.target, { silent: true });
          setStatus(loaded ? `Loaded cached graph for ${entry.target}` : `Loaded recent target: ${entry.target}`);
        });
        container.appendChild(button);
      });
    }

    function addScanHistoryEntry(target) {
      const normalized = String(target || "").trim();
      if (!normalized) {
        return;
      }
      const state = getState();
      const entry = {
        target: normalized,
        label: buildScanHistoryLabel(normalized),
        kind: isGithubUrl(normalized) ? "GitHub" : "Local",
        updatedAt: Date.now(),
      };
      updateState({
        currentScanTarget: normalized,
        scanHistory: [entry, ...state.scanHistory.filter((item) => item.target !== normalized)]
          .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
          .slice(0, 12),
      });
      persistScanHistory();
      renderScanHistory();
    }

    function clearScanHistory() {
      updateState({ scanHistory: [], currentScanTarget: "" });
      localStorage.removeItem(scanHistoryStorageKey);
      renderScanHistory();
      setStatus("Cleared recent scan history.");
    }

    function deleteScanHistoryEntry(target) {
      const state = getState();
      updateState({
        scanHistory: state.scanHistory.filter((entry) => entry.target !== target),
        currentScanTarget: state.currentScanTarget === target ? "" : state.currentScanTarget,
      });
      persistScanHistory();
      renderScanHistory();
      setStatus(`Removed recent scan: ${target}`);
    }

    async function scanProject() {
      let state = getState();
      if (state.isScanning) {
        return;
      }

      const path = getDom("path-input")?.value.trim() || "";
      state = updateState({ currentLanguage: getDom("language-input")?.value?.trim() || "python" });
      if (!path) {
        setStatus("Enter an absolute project path or GitHub repository URL.");
        return;
      }

      try {
        state = updateState({
          isScanning: true,
          historyMode: false,
          historyMeta: null,
          liveGraphSnapshot: null,
          liveSelectedNodeId: null,
        });
        syncHistoryButtons();
        deps.stopHistoryPlayback();
        getDom("history-overlay")?.classList.remove("visible");
        setStatus(`Scanning ${state.currentLanguage} project...`);
        setMode("Scan");
        resetScanMetrics();
        showScanOverlay();

        let data = null;
        try {
          const { data: submitData } = await fetchJsonWithFallback(
            ["/api/v1/jobs/scan"],
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ path, language: state.currentLanguage }),
            }
          );
          const jobId = String(submitData.job_id || "").trim();
          if (!jobId) {
            throw new Error("Scan job was created without a job id.");
          }

          for (let attempt = 0; attempt < 180; attempt += 1) {
            const delayMs = Math.min(1400, 220 + attempt * 20);
            await new Promise((resolve) => setTimeout(resolve, delayMs));
            const statusResponse = await fetchWithTimeout(`/api/v1/jobs/${jobId}`);
            const statusData = await parseJsonResponse(statusResponse);
            if (!statusResponse.ok) {
              throw new Error(statusData.error || "Failed to read scan job status");
            }
            const status = String(statusData.status || "").toLowerCase();
            if (status === "queued" || status === "running") {
              continue;
            }
            if (status === "failed") {
              throw new Error(statusData.error || "Scan job failed");
            }
            const resultResponse = await fetchWithTimeout(`/api/v1/jobs/${jobId}/result`);
            const resultData = await parseJsonResponse(resultResponse);
            if (!resultResponse.ok) {
              throw new Error(resultData.error || "Failed to read scan result");
            }
            data = resultData;
            break;
          }

          if (!data) {
            throw new Error("Scan timed out waiting for completion.");
          }
        } catch (jobError) {
          try {
            const fallback = await fetchJsonWithFallback(
              ["/api/v1/scan", "/api/scan"],
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path, language: state.currentLanguage }),
              }
            );
            data = fallback.data;
          } catch (_fallbackError) {
            throw jobError;
          }
        }

        updateState({ currentScanTarget: path });
        globalScope.__CODEWEAVE_SCAN_TARGET__ = path;
        updateGraph(data);
        addScanHistoryEntry(path);
        await browserStore.saveGraphSnapshot(path, data);
        setStatus(`Loaded ${data.nodes.length} nodes and ${data.edges.length} edges (${state.currentLanguage})`);
        deps.setHistoryStatus("Load a scanned git repo to begin.");
        renderHistoryCommitInfo();
        syncHistoryButtons();
      } catch (error) {
        console.error(error);
        setStatus(error.message);
        setMode("Error");
      } finally {
        updateState({ isScanning: false });
        hideScanOverlay();
        syncHistoryButtons();
      }
    }

    function bindScanEvents() {
      getDom("language-input")?.addEventListener("change", (event) => {
        updateState({ currentLanguage: event.target.value || "python" });
      });
      getDom("scan-history-clear-btn")?.addEventListener("click", clearScanHistory);
    }

    return {
      addScanHistoryEntry,
      bindScanEvents,
      clearScanHistory,
      deleteScanHistoryEntry,
      hideScanOverlay,
      loadCachedScanTarget,
      loadLanguageOptions,
      loadScanHistory,
      renderScanHistory,
      scanProject,
      showScanOverlay,
    };
  }

  globalScope.CodeWeaveScanController = { createScanController };
})(window);
