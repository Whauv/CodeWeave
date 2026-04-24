(function attachGraphState(globalScope) {
  function createGraphStateStore() {
    const state = {
      graphData: null,
      renderedGraphData: null,
      selectedNodeId: null,
      blastMode: false,
      currentBlastData: null,
      currentSearchQuery: "",
      svg: null,
      zoomBehavior: null,
      zoomLayer: null,
      linkLayer: null,
      nodeLayer: null,
      labelLayer: null,
      simulation: null,
      linkSelection: null,
      nodeSelection: null,
      labelSelection: null,
      monacoEditor: null,
      scanFrameTimer: null,
      isScanning: false,
      isDraggingDivider: false,
      blastRequestInFlight: false,
      progressiveLoadTimer: null,
      buildAnimationStopped: false,
      resizeFrameId: null,
      currentZoomTransform: d3.zoomIdentity,
      linkData: [],
      nodeData: [],
      themeMode: "dark",
      currentScanTarget: "",
      currentLanguage: "python",
      scanHistory: [],
      viewMode: "split",
      splitRatio: 0.68,
      layoutMode: "tree",
      clusterMode: true,
      edgeLabelsEnabled: false,
      neighborDepth: 2,
      graphSpacing: 1,
      fullNodeOrder: [],
      loadedNodeCount: 0,
      currentFocusedClusterKey: null,
      collapsedClusterKeys: new Set(),
      historyCommits: [],
      historyIndex: 0,
      historyMode: false,
      historyPlaybackTimer: null,
      historyPlaybackDelay: 7000,
      historySnapshotCache: new Map(),
      liveGraphSnapshot: null,
      liveSelectedNodeId: null,
      historyRequestInFlight: false,
      historyMeta: null,
    };

    function getState() {
      return state;
    }

    function setState(nextState) {
      Object.entries(nextState || {}).forEach(([key, value]) => {
        state[key] = value;
      });
      return state;
    }

    function updateState(updater) {
      const nextState = typeof updater === "function" ? updater(state) : updater;
      return setState(nextState);
    }

    return {
      getState,
      setState,
      updateState,
      state,
    };
  }

  globalScope.CodeWeaveGraphState = { createGraphStateStore };
})(window);
