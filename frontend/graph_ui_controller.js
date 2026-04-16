(function attachGraphUIController(globalScope) {
  function createGraphUIController(deps) {
    const {
      getState,
      setState,
      getStorageKeys,
      isCompactViewport,
      isNarrowDesktop,
      getMonacoTheme,
      getSelectedNode,
      getActiveDetailNode,
      renderGraph,
      restoreVisualState,
      getGraphData,
      setStatus,
      syncHistoryButtons,
      onSearch,
      onLoadMore,
      onShowAll,
      onStopBuild,
      onTreeLayout,
      onForceLayout,
      onToggleCluster,
      onOpenHistory,
      onCloseHistory,
      onToggleHistoryPlayback,
      onHistoryPrev,
      onHistoryNext,
      onHistoryDiff,
      onLoadHistorySnapshot,
      onSetHistoryPlaybackDelay,
      onScan,
    } = deps;

    function getDom(id) {
      return document.getElementById(id);
    }

    function updateState(nextState) {
      return setState(nextState);
    }

    function applyTheme(nextTheme, persist = true) {
      updateState({ themeMode: nextTheme });
      document.body.dataset.theme = nextTheme;
      const button = getDom("theme-toggle-btn");
      if (button) {
        button.textContent = nextTheme === "dark" ? "Light Theme" : "Dark Theme";
      }
      if (persist) {
        localStorage.setItem(getStorageKeys().theme, nextTheme);
      }
    }

    function setViewMode(nextMode, persist = true) {
      const state = updateState({ viewMode: nextMode });
      const frame = document.querySelector(".main-frame");
      if (frame) {
        frame.dataset.view = nextMode;
        if (isCompactViewport()) {
          frame.style.gridTemplateColumns = "1fr";
        } else if (nextMode === "split") {
          const panelMinWidth = isNarrowDesktop() ? 280 : 320;
          const panelFlex = Math.max(0.4, 1 - state.splitRatio);
          frame.style.gridTemplateColumns = `minmax(0, ${state.splitRatio}fr) 14px minmax(${panelMinWidth}px, ${panelFlex}fr)`;
        } else if (nextMode === "graph") {
          frame.style.gridTemplateColumns = "1fr 0 0";
        } else {
          frame.style.gridTemplateColumns = isNarrowDesktop()
            ? "0 0 minmax(320px, 1fr)"
            : "0 0 minmax(420px, 1fr)";
        }
      }

      getDom("view-split-btn")?.classList.toggle("active", nextMode === "split");
      getDom("view-graph-btn")?.classList.toggle("active", nextMode === "graph");
      getDom("view-panel-btn")?.classList.toggle("active", nextMode === "panel");

      if (persist) {
        localStorage.setItem(getStorageKeys().viewMode, nextMode);
      }
    }

    function loadSplitRatio() {
      const raw = Number(localStorage.getItem(getStorageKeys().splitRatio));
      if (!Number.isNaN(raw) && raw >= 0.35 && raw <= 0.82) {
        updateState({ splitRatio: raw });
      }
    }

    function persistSplitRatio() {
      const state = getState();
      localStorage.setItem(getStorageKeys().splitRatio, String(state.splitRatio));
    }

    function applySplitRatio() {
      if (getState().viewMode === "split") {
        setViewMode("split", false);
      }
    }

    function setSplitRatioFromPointer(clientX) {
      const frame = document.querySelector(".main-frame");
      if (!frame) {
        return;
      }
      const bounds = frame.getBoundingClientRect();
      const minRatio = isNarrowDesktop() ? 0.42 : 0.35;
      const maxRatio = isNarrowDesktop() ? 0.76 : 0.82;
      updateState({
        splitRatio: Math.min(maxRatio, Math.max(minRatio, (clientX - bounds.left) / bounds.width)),
      });
      persistSplitRatio();
      applySplitRatio();
    }

    function setDividerDragging(isDragging) {
      updateState({ isDraggingDivider: isDragging });
      getDom("pane-divider")?.classList.toggle("dragging", isDragging);
      document.body.style.userSelect = isDragging ? "none" : "";
    }

    function showEmptyState() {
      getDom("graph-empty-state")?.classList.remove("hidden");
    }

    function hideEmptyState() {
      getDom("graph-empty-state")?.classList.add("hidden");
    }

    function syncActionButtons() {
      const state = getState();
      const hasSelection = Boolean(getSelectedNode() || getActiveDetailNode?.());
      const blastButton = getDom("simulate-blast-btn");
      if (blastButton) {
        blastButton.disabled = !hasSelection || state.blastRequestInFlight;
        blastButton.textContent = state.blastRequestInFlight ? "Running Blast..." : "Simulate Blast Radius";
      }
      getDom("view-source-btn").disabled = !hasSelection;
      getDom("cluster-toggle-btn")?.classList.toggle("active", state.clusterMode);
      getDom("tree-layout-btn")?.classList.toggle("active", state.layoutMode === "tree");
      getDom("force-layout-btn")?.classList.toggle("active", state.layoutMode === "force");
    }

    function ensureMonaco(sourceCode) {
      return new Promise((resolve) => {
        const state = getState();
        if (state.monacoEditor && globalScope.monaco) {
          state.monacoEditor.setValue(sourceCode);
          resolve();
          return;
        }

        globalScope.require.config({
          paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.44.0/min/vs" },
        });
        globalScope.require(["vs/editor/editor.main"], () => {
          const editor = globalScope.monaco.editor.create(getDom("monaco-editor"), {
            value: sourceCode,
            language: "python",
            readOnly: true,
            theme: getMonacoTheme(),
            minimap: { enabled: false },
            fontSize: 14,
          });
          updateState({ monacoEditor: editor });
          resolve();
        });
      });
    }

    async function openMonacoModal(source) {
      getDom("monaco-modal").classList.add("visible");
      await ensureMonaco(source);
      getState().monacoEditor?.layout();
    }

    function serializeSvg() {
      const source = new XMLSerializer().serializeToString(getDom("graph-svg"));
      return source.includes("xmlns=")
        ? source
        : source.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"');
    }

    function downloadBlob(filename, type, payload) {
      const blob = payload instanceof Blob ? payload : new Blob([payload], { type });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    }

    function getExportBaseName() {
      return deps.buildScanHistoryLabel(getState().currentScanTarget || "graph");
    }

    function exportGraphSvg() {
      downloadBlob(`${getExportBaseName()}.svg`, "image/svg+xml;charset=utf-8", serializeSvg());
      setStatus("Exported SVG snapshot.");
    }

    async function exportGraphPng() {
      try {
        const svgMarkup = serializeSvg();
        const url = URL.createObjectURL(new Blob([svgMarkup], { type: "image/svg+xml;charset=utf-8" }));
        const image = new Image();
        const container = getDom("graph-container");
        image.onload = () => {
          const canvas = document.createElement("canvas");
          canvas.width = container.clientWidth * devicePixelRatio;
          canvas.height = container.clientHeight * devicePixelRatio;
          const context = canvas.getContext("2d");
          context.scale(devicePixelRatio, devicePixelRatio);
          context.fillStyle = getState().themeMode === "light" ? "#eef4f7" : "#0d0d0d";
          context.fillRect(0, 0, container.clientWidth, container.clientHeight);
          context.drawImage(image, 0, 0, container.clientWidth, container.clientHeight);
          canvas.toBlob((blob) => {
            downloadBlob(`${getExportBaseName()}.png`, "image/png", blob);
            URL.revokeObjectURL(url);
            setStatus("Exported PNG snapshot.");
          });
        };
        image.src = url;
      } catch (error) {
        console.error(error);
        setStatus("Could not export PNG snapshot.");
      }
    }

    function exportGraphJson() {
      downloadBlob(
        `${getExportBaseName()}.json`,
        "application/json;charset=utf-8",
        JSON.stringify({ graph: getGraphData() }, null, 2)
      );
      setStatus("Exported JSON snapshot.");
    }

    function scheduleGraphResize() {
      const state = getState();
      if (!state.graphData) {
        return;
      }
      if (state.resizeFrameId) {
        cancelAnimationFrame(state.resizeFrameId);
      }
      const resizeFrameId = requestAnimationFrame(() => {
        renderGraph(state.graphData);
        restoreVisualState();
        updateState({ resizeFrameId: null });
      });
      updateState({ resizeFrameId });
    }

    function bindUiEvents() {
      getDom("scan-btn")?.addEventListener("click", onScan);
      getDom("view-split-btn")?.addEventListener("click", () => setViewMode("split"));
      getDom("view-graph-btn")?.addEventListener("click", () => setViewMode("graph"));
      getDom("view-panel-btn")?.addEventListener("click", () => setViewMode("panel"));
      getDom("tree-layout-btn")?.addEventListener("click", onTreeLayout);
      getDom("force-layout-btn")?.addEventListener("click", onForceLayout);
      getDom("cluster-toggle-btn")?.addEventListener("click", onToggleCluster);
      getDom("history-btn")?.addEventListener("click", onOpenHistory);
      getDom("history-close-btn")?.addEventListener("click", onCloseHistory);
      getDom("history-live-btn")?.addEventListener("click", onCloseHistory);
      getDom("history-play-btn")?.addEventListener("click", onToggleHistoryPlayback);
      getDom("history-prev-btn")?.addEventListener("click", onHistoryPrev);
      getDom("history-next-btn")?.addEventListener("click", onHistoryNext);
      getDom("history-diff-btn")?.addEventListener("click", onHistoryDiff);
      getDom("history-speed-select")?.addEventListener("change", (event) => {
        onSetHistoryPlaybackDelay?.(Number(event.target.value || 5000));
      });
      getDom("history-range")?.addEventListener("input", (event) => {
        deps.stopHistoryPlayback();
        onLoadHistorySnapshot(Number(event.target.value || 0));
      });
      getDom("theme-toggle-btn")?.addEventListener("click", () => {
        applyTheme(getState().themeMode === "dark" ? "light" : "dark");
        if (getState().graphData) {
          renderGraph(getState().graphData);
          restoreVisualState();
        }
        if (globalScope.monaco) {
          globalScope.monaco.editor.setTheme(getMonacoTheme());
        }
      });
      getDom("search-input")?.addEventListener("input", (event) => onSearch(event.target.value));
      getDom("graph-load-more-btn")?.addEventListener("click", onLoadMore);
      getDom("graph-show-all-btn")?.addEventListener("click", onShowAll);
      getDom("stop-build-btn")?.addEventListener("click", onStopBuild);
      getDom("export-svg-btn")?.addEventListener("click", exportGraphSvg);
      getDom("export-png-btn")?.addEventListener("click", exportGraphPng);
      getDom("export-json-btn")?.addEventListener("click", exportGraphJson);
      getDom("pane-divider")?.addEventListener("mousedown", (event) => {
        if (isCompactViewport()) {
          return;
        }
        if (getState().viewMode !== "split") {
          setViewMode("split");
        }
        setDividerDragging(true);
        event.preventDefault();
      });
      getDom("modal-close-btn")?.addEventListener("click", () => {
        getDom("monaco-modal").classList.remove("visible");
      });

      globalScope.addEventListener("resize", () => {
        deps.hideNodeTooltip();
        setViewMode(getState().viewMode, false);
        if (getState().graphData) {
          scheduleGraphResize();
        }
        getState().monacoEditor?.layout();
      });
      globalScope.addEventListener("mousemove", (event) => {
        if (!getState().isDraggingDivider || isCompactViewport()) {
          return;
        }
        setSplitRatioFromPointer(event.clientX);
      });
      globalScope.addEventListener("mouseup", () => {
        if (getState().isDraggingDivider) {
          setDividerDragging(false);
          scheduleGraphResize();
        }
      });
    }

    return {
      applyTheme,
      bindUiEvents,
      hideEmptyState,
      loadSplitRatio,
      openMonacoModal,
      scheduleGraphResize,
      setDividerDragging,
      setSplitRatioFromPointer,
      setViewMode,
      showEmptyState,
      syncActionButtons,
    };
  }

  globalScope.CodeWeaveGraphUI = { createGraphUIController };
})(window);
