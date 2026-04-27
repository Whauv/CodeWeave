(function attachNodeInteractions(globalScope) {
  function createNodeInteractionHelpers(deps) {
    const {
      getRawNode,
      getSelectedNode,
      getState,
      setState,
      getTooltipElements,
      hideNodeTooltip,
      openMonacoModal,
      renderGraph,
      restoreVisualState,
      setSelectedLabel,
      setStatus,
      syncActionButtons,
      triggerBlastRadius,
      truncateLabel,
      updateNodeDetail,
    } = deps;

    function updateState(nextState) {
      return setState(nextState);
    }

    function showNodeTooltip(node, event) {
      const { container, title, body } = getTooltipElements();
      if (!container || !title || !body) {
        return;
      }
      title.textContent = node.isCluster ? `${node.name} cluster` : (node.name || "Node");
      body.textContent = node.summary || "No summary available.";
      container.classList.add("visible");
      moveNodeTooltip(event);
    }

    function moveNodeTooltip(event) {
      const { container } = getTooltipElements();
      const graphContainer = document.getElementById("graph-container");
      if (!container || !graphContainer) {
        return;
      }
      const bounds = graphContainer.getBoundingClientRect();
      const tooltipWidth = container.offsetWidth || 280;
      const tooltipHeight = container.offsetHeight || 80;
      let left = event.clientX - bounds.left + 18;
      let top = event.clientY - bounds.top + 18;
      if (left + tooltipWidth > bounds.width - 12) {
        left = bounds.width - tooltipWidth - 12;
      }
      if (top + tooltipHeight > bounds.height - 12) {
        top = bounds.height - tooltipHeight - 12;
      }
      container.style.left = `${Math.max(12, left)}px`;
      container.style.top = `${Math.max(12, top)}px`;
    }

    function ensureNodeVisibleAndExpanded(nodeId) {
      const raw = getRawNode(nodeId);
      if (!raw) {
        return false;
      }

      const state = getState();
      let changed = false;

      const clusterKey = deps.getClusterKey(raw);
      if (state.collapsedClusterKeys.has(clusterKey)) {
        state.collapsedClusterKeys.delete(clusterKey);
        changed = true;
      }
      return changed;
    }

    function selectNode(nodeId, openDetail = false) {
      performHighlightNode(nodeId);
      if (openDetail) {
        const raw = getRawNode(nodeId);
        if (raw) {
          updateNodeDetail(raw, getState().graphData);
        }
      }
    }

    function handleNodePrimaryAction(node) {
      const state = getState();
      if (node.isCluster) {
        state.collapsedClusterKeys.delete(node.clusterKey);
        updateState({
          currentFocusedClusterKey: node.clusterKey,
          selectedNodeId: null,
        });
        renderGraph(state.graphData);
        restoreVisualState();
        setStatus(`Expanded ${node.name} cluster branch.`);
        return;
      }
      selectNode(node.id, true);
    }

    function bindNodeInteractions(selection) {
      selection
        .style("cursor", "pointer")
        .on("click", (_, node) => handleNodePrimaryAction(node))
        .on("mouseenter", (event, node) => {
          if (Number(globalScope.__CODEWEAVE_SUPPRESS_HOVER_TRACE_UNTIL__ || 0) > Date.now()) {
            hideNodeTooltip();
            restoreVisualState();
            return;
          }
          showNodeTooltip(node, event);
          deps.applyHoverTrace(node.id);
        })
        .on("mousemove", (event) => moveNodeTooltip(event))
        .on("mouseleave", () => {
          hideNodeTooltip();
          restoreVisualState();
        })
        .on("contextmenu", (event, node) => {
          if (node.isCluster) {
            return;
          }
          event.preventDefault();
          triggerBlastRadius(node);
        })
        .on("dblclick", (_, node) => {
          if (node.isCluster) {
            handleNodePrimaryAction(node);
            return;
          }
          openMonacoModal(node.source_code || "# No source code available");
        });
    }

    function performHighlightNode(nodeId) {
      const state = getState();
      if (!state.graphData) {
        return;
      }
      const changed = ensureNodeVisibleAndExpanded(nodeId);
      if (changed) {
        renderGraph(state.graphData);
      }
      updateState({ selectedNodeId: nodeId, currentFocusedClusterKey: null });
      globalScope.__CODEWEAVE_SELECTED_NODE_ID__ = nodeId || null;
      const selected = getSelectedNode();
      setSelectedLabel(selected ? selected.name : "None");
      deps.renderBreadcrumbs();
      syncActionButtons();
      restoreVisualState();
    }

    return {
      bindNodeInteractions,
      ensureNodeVisibleAndExpanded,
      handleNodePrimaryAction,
      highlightNode: performHighlightNode,
      moveNodeTooltip,
      selectNode,
      showNodeTooltip,
    };
  }

  globalScope.CodeWeaveNodeInteractions = { createNodeInteractionHelpers };
})(window);
