(function attachGraphEffectsController(globalScope) {
  function createGraphEffectsController(deps) {
    const {
      getGraphData,
      getRenderedGraphData,
      getSelections,
      getState,
      getNodeFill,
      getGraphPalette,
      getSelectedNodeId,
      getLayoutMode,
      getCurrentSearchQuery,
      setState,
      setMode,
      setStatus,
      syncActionButtons,
      showBlastInfo,
    } = deps;

    function collectReachable(startId, direction) {
      const renderedGraphData = getRenderedGraphData();
      if (!renderedGraphData) {
        return new Set();
      }
      const adjacency = new Map();
      renderedGraphData.nodes.forEach((node) => adjacency.set(node.id, []));
      renderedGraphData.edges.forEach((edge) => {
        const from = direction === "out" ? edge.source : edge.target;
        const to = direction === "out" ? edge.target : edge.source;
        adjacency.get(from)?.push(to);
      });

      const visited = new Set();
      const queue = [startId];
      while (queue.length) {
        const current = queue.shift();
        (adjacency.get(current) || []).forEach((neighbor) => {
          if (!visited.has(neighbor) && neighbor !== startId) {
            visited.add(neighbor);
            queue.push(neighbor);
          }
        });
      }
      return visited;
    }

    function applyBaseGraphStyle() {
      const { nodeSelection, labelSelection, linkSelection } = getSelections();
      if (!nodeSelection || !labelSelection || !linkSelection) {
        return;
      }
      const palette = getGraphPalette();
      const selectedNodeId = getSelectedNodeId();
      const layoutMode = getLayoutMode();
      nodeSelection
        .classed("epicenter", false)
        .attr("fill", (node) => getNodeFill(node))
        .attr("opacity", 1)
        .attr("stroke", (node) => (node.id === selectedNodeId ? palette.nodeSelected : palette.nodeStroke))
        .attr("stroke-width", (node) => (node.id === selectedNodeId ? 4 : 2));
      labelSelection.attr("opacity", 1);
      linkSelection
        .attr("opacity", layoutMode === "tree" ? 0.78 : 0.72)
        .attr("stroke", layoutMode === "tree" ? palette.linkTree : palette.linkForce);
    }

    function applyHoverTrace(nodeId) {
      const { nodeSelection, labelSelection, linkSelection } = getSelections();
      const state = getState();
      if (!nodeSelection || state.blastMode || getCurrentSearchQuery().trim()) {
        return;
      }

      const palette = getGraphPalette();
      const outgoing = collectReachable(nodeId, "out");
      const incoming = collectReachable(nodeId, "in");
      const activeNodes = new Set([nodeId, ...outgoing, ...incoming]);

      nodeSelection
        .attr("opacity", (node) => (activeNodes.has(node.id) ? 1 : 0.14))
        .attr("stroke", (node) => {
          if (node.id === nodeId) {
            return palette.nodeSelected;
          }
          if (incoming.has(node.id)) {
            return "#66d7d1";
          }
          if (outgoing.has(node.id)) {
            return "#ffb65c";
          }
          return palette.nodeStroke;
        })
        .attr("stroke-width", (node) => (node.id === nodeId ? 4 : activeNodes.has(node.id) ? 3 : 1.6));

      labelSelection.attr("opacity", (node) => (activeNodes.has(node.id) ? 1 : 0.18));
      linkSelection
        .attr("opacity", (link) => {
          const forward =
            (link.source.id === nodeId || outgoing.has(link.source.id)) &&
            (outgoing.has(link.target.id) || link.target.id === nodeId);
          const reverse =
            (link.target.id === nodeId || incoming.has(link.target.id)) &&
            (incoming.has(link.source.id) || link.source.id === nodeId);
          return forward || reverse ? 0.96 : 0.06;
        })
        .attr("stroke", (link) => {
          const forward =
            (link.source.id === nodeId || outgoing.has(link.source.id)) &&
            (outgoing.has(link.target.id) || link.target.id === nodeId);
          const reverse =
            (link.target.id === nodeId || incoming.has(link.target.id)) &&
            (incoming.has(link.source.id) || link.source.id === nodeId);
          if (forward) {
            return "#ffb65c";
          }
          if (reverse) {
            return "#66d7d1";
          }
          return getLayoutMode() === "tree" ? palette.linkTreeMuted : palette.linkForce;
        });
    }

    function applyBlastData(data) {
      const { nodeSelection, labelSelection, linkSelection } = getSelections();
      if (!data || !nodeSelection) {
        return;
      }
      const palette = getGraphPalette();
      setState({ blastMode: true, currentBlastData: data });
      setMode("Blast");
      const affected = new Set(data.affected_nodes || []);
      nodeSelection
        .classed("epicenter", (node) => node.id === data.epicenter)
        .attr("fill", (node) => data.risk_colors?.[node.id] || getNodeFill(node))
        .attr("opacity", (node) => (affected.has(node.id) ? 1 : 0.12))
        .attr("stroke", (node) => {
          if (node.id === data.epicenter) {
            return "#fff5d6";
          }
          return node.id === getSelectedNodeId() ? palette.nodeSelected : palette.nodeStroke;
        });
      labelSelection.attr("opacity", (node) => (affected.has(node.id) ? 1 : 0.14));
      linkSelection
        .attr("opacity", (link) => (affected.has(link.source.id) && affected.has(link.target.id) ? 0.95 : 0.05))
        .attr("stroke", (link) => {
          if (affected.has(link.source.id) && affected.has(link.target.id)) {
            return "rgba(255, 190, 137, 0.65)";
          }
          return getLayoutMode() === "tree" ? palette.linkTreeMuted : palette.linkForce;
        });
      showBlastInfo(data);
    }

    function searchNodes(query) {
      const graphData = getGraphData();
      const { nodeSelection, labelSelection, linkSelection } = getSelections();
      const normalizedQuery = String(query || "").trim();
      setState({ currentSearchQuery: normalizedQuery });
      if (!graphData || !nodeSelection) {
        return;
      }
      if (!normalizedQuery) {
        applyBaseGraphStyle();
        const blastData = getState().currentBlastData;
        if (getState().blastMode && blastData) {
          applyBlastData(blastData);
        }
        setStatus(graphData ? `Loaded ${graphData.nodes.length} nodes and ${graphData.edges.length} edges` : "Ready to scan");
        return;
      }

      const lowered = normalizedQuery.toLowerCase();
      const matchingRawIds = new Set(
        graphData.nodes
          .filter((node) => node.name.toLowerCase().includes(lowered) || (node.summary || "").toLowerCase().includes(lowered))
          .map((node) => node.id)
      );

      const renderedGraphData = getRenderedGraphData();
      const matchingDisplayIds = new Set();
      renderedGraphData.nodes.forEach((node) => {
        if (node.isCluster) {
          if ((node.memberIds || []).some((id) => matchingRawIds.has(id))) {
            matchingDisplayIds.add(node.id);
          }
        } else if (matchingRawIds.has(node.id)) {
          matchingDisplayIds.add(node.id);
        }
      });

      const palette = getGraphPalette();
      nodeSelection
        .attr("opacity", (node) => (matchingDisplayIds.has(node.id) ? 1 : 0.18))
        .attr("stroke", (node) => (matchingDisplayIds.has(node.id) ? palette.nodeSelected : palette.nodeStroke))
        .attr("stroke-width", (node) => (matchingDisplayIds.has(node.id) ? 4 : 2));
      labelSelection.attr("opacity", (node) => (matchingDisplayIds.has(node.id) ? 1 : 0.2));
      linkSelection.attr("opacity", (link) => (matchingDisplayIds.has(link.source.id) || matchingDisplayIds.has(link.target.id) ? 0.85 : 0.06));
      setStatus(`Search matched ${matchingRawIds.size} node${matchingRawIds.size === 1 ? "" : "s"}`);
    }

    function clearBlastRadius() {
      setState({ blastMode: false, currentBlastData: null });
      setMode(getState().historyMode ? "History" : "Explore");
      applyBaseGraphStyle();
      showBlastInfo({ summary: "Right-click a node to simulate impact.", depth_map: {} });
      if (getCurrentSearchQuery()) {
        searchNodes(getCurrentSearchQuery());
      }
      syncActionButtons();
    }

    function restoreVisualState() {
      applyBaseGraphStyle();
      const state = getState();
      if (state.blastMode && state.currentBlastData) {
        applyBlastData(state.currentBlastData);
      } else if (state.currentSearchQuery) {
        searchNodes(state.currentSearchQuery);
      }
    }

    return {
      applyBaseGraphStyle,
      applyBlastData,
      applyHoverTrace,
      clearBlastRadius,
      restoreVisualState,
      searchNodes,
    };
  }

  globalScope.CodeWeaveGraphEffects = { createGraphEffectsController };
})(window);
