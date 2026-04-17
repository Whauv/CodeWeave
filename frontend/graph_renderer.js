(function attachGraphRenderer(globalScope) {
  function createGraphRenderer(deps) {
    const {
      getGraphCollections,
      getGraphPalette,
      getNodeFill,
      getNodeRadius,
      truncateLabel,
    } = deps;
    let previousNodePositions = new Map();
    let previousParentMap = new Map();
    let previousVisibleNodeIds = new Set();
    let pinnedParentMap = new Map();
    let previousRenderScope = "";

    function applyTreeLayout(nodes, links, width, height, options = {}) {
      const usePinnedParents = !Boolean(options.historyMode);
      const incoming = new Map(nodes.map((node) => [node.id, []]));
      const outgoing = new Map(nodes.map((node) => [node.id, []]));
      const nodeById = new Map(nodes.map((node) => [node.id, node]));

      links.forEach((link) => {
        if (!incoming.has(link.target.id) || !outgoing.has(link.source.id)) {
          return;
        }
        incoming.get(link.target.id).push(link.source.id);
        outgoing.get(link.source.id).push(link.target.id);
      });

      let roots = nodes.filter((node) => (incoming.get(node.id) || []).length === 0);
      if (!roots.length && nodes.length) {
        roots = [...nodes]
          .sort((a, b) => {
            const degreeA = (a.outgoingCount || 0) + (a.incomingCount || 0);
            const degreeB = (b.outgoingCount || 0) + (b.incomingCount || 0);
            return degreeB - degreeA || String(a.name || "").localeCompare(String(b.name || ""));
          })
          .slice(0, 1);
      }

      const depthMap = new Map();
      const queue = roots.map((node) => ({ id: node.id, depth: 0 }));
      while (queue.length) {
        const current = queue.shift();
        if (depthMap.has(current.id)) {
          continue;
        }
        depthMap.set(current.id, current.depth);
        (outgoing.get(current.id) || []).forEach((childId) => {
          if (!depthMap.has(childId)) {
            queue.push({ id: childId, depth: current.depth + 1 });
          }
        });
      }

      let fallbackDepth = depthMap.size ? Math.max(...depthMap.values()) + 1 : 0;
      nodes.forEach((node) => {
        if (!depthMap.has(node.id)) {
          depthMap.set(node.id, fallbackDepth);
          fallbackDepth += 1;
        }
      });

      const parentMap = new Map();
      const childMap = new Map(nodes.map((node) => [node.id, []]));

      const assignParent = (node) => {
        const nodeId = node.id;
        const nodeDepth = depthMap.get(nodeId) || 0;
        if (nodeDepth === 0) {
          return null;
        }
        const candidates = (incoming.get(nodeId) || []).filter((id) => nodeById.has(id));
        if (!candidates.length) {
          return null;
        }
        const ranked = candidates
          .map((candidateId) => {
            const parentNode = nodeById.get(candidateId);
            const parentDepth = depthMap.get(candidateId) || 0;
            const depthDelta = Math.max(0, nodeDepth - parentDepth);
            const parentDegree = (parentNode?.outgoingCount || 0) + (parentNode?.incomingCount || 0);
            return { candidateId, depthDelta, parentDegree, parentDepth };
          })
          .filter((candidate) => candidate.parentDepth < nodeDepth)
          .sort((left, right) => {
            const leftPenalty = left.parentDepth > nodeDepth ? 1000 : 0;
            const rightPenalty = right.parentDepth > nodeDepth ? 1000 : 0;
            return (
              (leftPenalty + left.depthDelta) - (rightPenalty + right.depthDelta) ||
              right.parentDegree - left.parentDegree ||
              left.candidateId.localeCompare(right.candidateId)
            );
          });
        const pinnedParentId = usePinnedParents ? pinnedParentMap.get(nodeId) : null;
        if (pinnedParentId && ranked.some((candidate) => candidate.candidateId === pinnedParentId)) {
          return pinnedParentId;
        }
        return ranked[0]?.candidateId || null;
      };

      nodes
        .slice()
        .sort((a, b) => {
          return (depthMap.get(a.id) || 0) - (depthMap.get(b.id) || 0) || String(a.name || "").localeCompare(String(b.name || ""));
        })
        .forEach((node) => {
          const parentId = assignParent(node);
          if (!parentId || parentId === node.id) {
            return;
          }
          parentMap.set(node.id, parentId);
          childMap.get(parentId)?.push(node.id);
        });

      const virtualRootId = "__virtual_root__";
      const rootIds = nodes
        .filter((node) => !parentMap.has(node.id))
        .sort((a, b) => {
          return (
            (depthMap.get(a.id) || 0) - (depthMap.get(b.id) || 0) ||
            ((b.outgoingCount || 0) + (b.incomingCount || 0)) - ((a.outgoingCount || 0) + (a.incomingCount || 0)) ||
            String(a.name || "").localeCompare(String(b.name || ""))
          );
        })
        .map((node) => node.id);

      if (!rootIds.length && nodes.length) {
        const fallbackRoot = nodes
          .slice()
          .sort((a, b) => {
            const degreeA = (a.outgoingCount || 0) + (a.incomingCount || 0);
            const degreeB = (b.outgoingCount || 0) + (b.incomingCount || 0);
            return degreeB - degreeA || String(a.name || "").localeCompare(String(b.name || ""));
          })[0];
        if (fallbackRoot) {
          parentMap.delete(fallbackRoot.id);
          rootIds.push(fallbackRoot.id);
        }
      }

      const buildHierarchyNode = (nodeId) => {
        if (nodeId === virtualRootId) {
          return {
            id: virtualRootId,
            dataRef: null,
            children: rootIds.map((id) => buildHierarchyNode(id)),
          };
        }
        const children = (childMap.get(nodeId) || []).map((id) => buildHierarchyNode(id));
        return {
          id: nodeId,
          dataRef: nodeById.get(nodeId),
          children,
        };
      };

      const hierarchyRoot = d3.hierarchy(buildHierarchyNode(virtualRootId), (entry) => entry.children);
      hierarchyRoot.eachAfter((hierNode) => {
        if (!hierNode.children || !hierNode.children.length) {
          hierNode.data.leafWeight = 1;
          return;
        }
        const weight = hierNode.children.reduce((sum, child) => sum + (child.data.leafWeight || 1), 0);
        hierNode.data.leafWeight = Math.max(1, weight);
      });

      const visibleLeafCount = Math.max(1, hierarchyRoot.children?.reduce((sum, child) => sum + (child.data.leafWeight || 1), 0) || 1);
      const maxDepth = Math.max(1, ...nodes.map((node) => depthMap.get(node.id) || 0));
      const horizontalGap = Math.max(150, Math.min(280, Math.floor((width - 220) / Math.max(1, maxDepth))));
      const verticalGap = visibleLeafCount > 260 ? 14 : visibleLeafCount > 180 ? 18 : visibleLeafCount > 110 ? 23 : 30;

      const treeLayout = d3
        .tree()
        .nodeSize([verticalGap, horizontalGap])
        .separation((left, right) => {
          const leftWeight = left.data?.leafWeight || 1;
          const rightWeight = right.data?.leafWeight || 1;
          const siblingFactor = left.parent === right.parent ? 1.2 : 2;
          return siblingFactor * Math.max(1, Math.sqrt((leftWeight + rightWeight) / 2));
        });
      treeLayout(hierarchyRoot);

      const drawableNodes = hierarchyRoot.descendants().filter((entry) => entry.data.id !== virtualRootId);
      const xValues = drawableNodes.map((entry) => entry.x);
      const yValues = drawableNodes.map((entry) => entry.y);
      const minTreeX = xValues.length ? Math.min(...xValues) : 0;
      const maxTreeX = xValues.length ? Math.max(...xValues) : 0;
      const minTreeY = yValues.length ? Math.min(...yValues) : 0;
      const maxTreeY = yValues.length ? Math.max(...yValues) : 0;

      const leftPad = 110;
      const rightPad = 140;
      const topPad = 84;
      const bottomPad = 84;
      const verticalSpan = Math.max(1, maxTreeX - minTreeX);
      const verticalOffset = topPad + (height - topPad - bottomPad - verticalSpan) / 2;

      drawableNodes.forEach((hierNode) => {
        const node = nodeById.get(hierNode.data.id);
        if (!node) {
          return;
        }
        const mappedX = leftPad + (hierNode.y - minTreeY);
        const mappedY = verticalOffset + (hierNode.x - minTreeX);
        node.x = Math.max(leftPad, Math.min(width - rightPad, mappedX));
        node.y = Math.max(topPad, Math.min(height - bottomPad, mappedY));
      });

      const layersByDepth = new Map();
      nodes.forEach((node) => {
        const depth = depthMap.get(node.id) || 0;
        if (!layersByDepth.has(depth)) {
          layersByDepth.set(depth, []);
        }
        layersByDepth.get(depth).push(node);
      });
      layersByDepth.forEach((layerNodes) => {
        const sorted = layerNodes.slice().sort((left, right) => (left.y || 0) - (right.y || 0));
        let previousNode = null;
        sorted.forEach((node) => {
          if (!previousNode) {
            previousNode = node;
            return;
          }
          const minimumGap = Math.max(24, getNodeRadius(previousNode) + getNodeRadius(node) + 8);
          const desiredY = (previousNode.y || 0) + minimumGap;
          if ((node.y || 0) < desiredY) {
            node.y = desiredY;
          }
          previousNode = node;
        });
      });

      const layoutParentMap = new Map();
      hierarchyRoot.descendants().forEach((entry) => {
        if (!entry.parent || entry.data.id === virtualRootId || entry.parent.data.id === virtualRootId) {
          return;
        }
        layoutParentMap.set(entry.data.id, entry.parent.data.id);
      });
      if (usePinnedParents) {
        pinnedParentMap = new Map(layoutParentMap);
      } else {
        pinnedParentMap = new Map();
      }

      return {
        depthMap,
        parentMap: layoutParentMap,
        nodeById,
        roots,
      };
    }

    function treePath(link) {
      const sourceOffset =
        (((link._sourceRank ?? 0) - ((link._sourceCount ?? 1) - 1) / 2) *
          (link._sourceCount && link._sourceCount > 1 ? 4 : 0));
      const targetOffset =
        (((link._targetRank ?? 0) - ((link._targetCount ?? 1) - 1) / 2) *
          (link._targetCount && link._targetCount > 1 ? 4 : 0));
      const startY = (link.source.y || 0) + sourceOffset;
      const endY = (link.target.y || 0) + targetOffset;
      const midX = (link.source.x + link.target.x) / 2;
      return `M${link.source.x},${startY} C${midX},${startY} ${midX},${endY} ${link.target.x},${endY}`;
    }

    function treePathFromPoints(sourcePoint, targetPoint) {
      const midX = ((sourcePoint?.x || 0) + (targetPoint?.x || 0)) / 2;
      const startY = sourcePoint?.y || 0;
      const endY = targetPoint?.y || 0;
      return `M${sourcePoint?.x || 0},${startY} C${midX},${startY} ${midX},${endY} ${targetPoint?.x || 0},${endY}`;
    }

    function resolveAnchorPosition(node, currentParentMap, currentNodesById) {
      const previous = previousNodePositions.get(node.id);
      if (previous) {
        return previous;
      }
      const parentId = currentParentMap.get(node.id) || previousParentMap.get(node.id);
      if (parentId) {
        const currentParent = currentNodesById.get(parentId);
        if (currentParent) {
          return { x: currentParent.x, y: currentParent.y };
        }
        const previousParent = previousNodePositions.get(parentId);
        if (previousParent) {
          return previousParent;
        }
      }
      return { x: node.x, y: node.y };
    }

    function resolveExitPosition(node, currentParentMap, currentNodesById) {
      const parentId = currentParentMap.get(node.id) || previousParentMap.get(node.id);
      if (parentId) {
        const currentParent = currentNodesById.get(parentId);
        if (currentParent) {
          return { x: currentParent.x, y: currentParent.y };
        }
        const previousParent = previousNodePositions.get(parentId);
        if (previousParent) {
          return previousParent;
        }
      }
      const previous = previousNodePositions.get(node.id);
      if (previous) {
        return previous;
      }
      return { x: node.x || 0, y: node.y || 0 };
    }

    function decorateTreeLinksForSplay(links) {
      const bySource = new Map();
      const byTarget = new Map();
      links.forEach((link) => {
        if (!bySource.has(link.source.id)) {
          bySource.set(link.source.id, []);
        }
        bySource.get(link.source.id).push(link);
        if (!byTarget.has(link.target.id)) {
          byTarget.set(link.target.id, []);
        }
        byTarget.get(link.target.id).push(link);
      });

      bySource.forEach((items) => {
        items
          .slice()
          .sort((left, right) => (left.target.y || 0) - (right.target.y || 0))
          .forEach((link, index, sorted) => {
            link._sourceRank = index;
            link._sourceCount = sorted.length;
          });
      });
      byTarget.forEach((items) => {
        items
          .slice()
          .sort((left, right) => (left.source.y || 0) - (right.source.y || 0))
          .forEach((link, index, sorted) => {
            link._targetRank = index;
            link._targetCount = sorted.length;
          });
      });
    }

    function getDeclutteredTreeLinks(nodes, links, depthMap = null) {
      if (!nodes.length || nodes.length <= 90) {
        return links;
      }
      const maxIncomingPerTarget = nodes.length > 420 ? 2 : nodes.length > 260 ? 3 : nodes.length > 160 ? 4 : 5;
      const incomingByTarget = new Map();
      links.forEach((link) => {
        const targetId = link.target.id;
        if (!incomingByTarget.has(targetId)) {
          incomingByTarget.set(targetId, []);
        }
        incomingByTarget.get(targetId).push(link);
      });

      const kept = [];
      const linkScore = (link) => {
        const depthDelta = depthMap
          ? Math.abs((depthMap.get(link.target.id) || 0) - (depthMap.get(link.source.id) || 0))
          : 1;
        return (
          (link.weight || 1) +
          1 / Math.max(1, depthDelta) +
          (link.source.outgoingCount || 0) * 0.08 +
          (link.source.incomingCount || 0) * 0.04
        );
      };
      incomingByTarget.forEach((incomingLinks) => {
        const forwardOnlyLinks =
          nodes.length > 220 && depthMap
            ? incomingLinks.filter((link) => {
                const sourceDepth = depthMap.get(link.source.id) || 0;
                const targetDepth = depthMap.get(link.target.id) || 0;
                return sourceDepth <= targetDepth;
              })
            : incomingLinks;
        const candidateLinks = forwardOnlyLinks.length ? forwardOnlyLinks : incomingLinks;
        const sorted = candidateLinks
          .sort((left, right) => {
            return linkScore(right) - linkScore(left);
          });

        const nearestParent = sorted.find((link) => {
          if (!depthMap) {
            return true;
          }
          const srcDepth = depthMap.get(link.source.id) || 0;
          const dstDepth = depthMap.get(link.target.id) || 0;
          return srcDepth <= dstDepth;
        });
        const selected = sorted.slice(0, maxIncomingPerTarget);
        if (nearestParent && !selected.includes(nearestParent)) {
          selected[selected.length - 1] = nearestParent;
        }
        selected.forEach((link) => kept.push(link));
      });

      if (nodes.length > 140) {
        const maxOutgoingPerSource = nodes.length > 420 ? 3 : nodes.length > 260 ? 4 : 5;
        const bySource = new Map();
        kept.forEach((link) => {
          if (!bySource.has(link.source.id)) {
            bySource.set(link.source.id, []);
          }
          bySource.get(link.source.id).push(link);
        });
        const outgoingPruned = [];
        bySource.forEach((sourceLinks) => {
          sourceLinks
            .sort((left, right) => linkScore(right) - linkScore(left))
            .slice(0, maxOutgoingPerSource)
            .forEach((link) => outgoingPruned.push(link));
        });
        const unique = new Map();
        outgoingPruned.forEach((link) => {
          unique.set(`${link.source.id}::${link.target.id}`, link);
        });
        const compact = [...unique.values()];
        if (nodes.length > 260) {
          const globalCap = Math.max(nodes.length * 2, 260);
          compact.sort((left, right) => linkScore(right) - linkScore(left));
          return compact.slice(0, globalCap);
        }
        return compact;
      }
      return kept;
    }

    function getTreeLabelVisibility(nodes, depthMap) {
      if (!nodes.length) {
        return new Set();
      }
      if (nodes.length <= 36) {
        return new Set(nodes.map((node) => node.id));
      }

      const visible = new Set();
      nodes.forEach((node) => {
        if (
          node.isCluster ||
          String(node.mutation_status || "").toLowerCase() === "hotspot" ||
          String(node.mutation_status || "").toLowerCase() === "new"
        ) {
          visible.add(node.id);
        }
      });

      const layers = new Map();
      nodes.forEach((node) => {
        const depth = depthMap.get(node.id) || 0;
        if (!layers.has(depth)) {
          layers.set(depth, []);
        }
        layers.get(depth).push(node);
      });

      const stride = nodes.length > 420 ? 8 : nodes.length > 260 ? 6 : 4;
      layers.forEach((layerNodes) => {
        const ranked = layerNodes.slice().sort((a, b) => {
          const degreeA = (a.incomingCount || 0) + (a.outgoingCount || 0);
          const degreeB = (b.incomingCount || 0) + (b.outgoingCount || 0);
          return degreeB - degreeA || String(a.name || "").localeCompare(String(b.name || ""));
        });
        const keepTop = Math.max(7, Math.floor(layerNodes.length * (nodes.length > 320 ? 0.16 : 0.22)));
        ranked.slice(0, keepTop).forEach((node) => visible.add(node.id));
        ranked.forEach((node, index) => {
          if (index % stride === 0) {
            visible.add(node.id);
          }
        });
      });

      const minLabelDy = nodes.length > 360 ? 18 : nodes.length > 220 ? 15 : 12;
      const minLabelDx = nodes.length > 360 ? 34 : 26;
      const byBand = new Map();
      [...visible].forEach((nodeId) => {
        const node = nodes.find((candidate) => candidate.id === nodeId);
        if (!node) {
          return;
        }
        const xBand = Math.round((node.x || 0) / minLabelDx);
        if (!byBand.has(xBand)) {
          byBand.set(xBand, []);
        }
        byBand.get(xBand).push(node);
      });
      byBand.forEach((bandNodes) => {
        bandNodes.sort((a, b) => (a.y || 0) - (b.y || 0));
        let lastY = Number.NEGATIVE_INFINITY;
        bandNodes.forEach((node) => {
          if ((node.y || 0) - lastY < minLabelDy && !node.isCluster) {
            visible.delete(node.id);
            return;
          }
          lastY = node.y || lastY;
        });
      });

      return visible;
    }

    function getTreeBackboneLinks(parentMap, nodeById) {
      const links = [];
      parentMap.forEach((parentId, childId) => {
        const source = nodeById.get(parentId);
        const target = nodeById.get(childId);
        if (!source || !target) {
          return;
        }
        links.push({ source, target, weight: 1, _isBackbone: true });
      });
      return links;
    }

    function getRootGuideLinks(nodes, parentMap) {
      const rootNodes = nodes.filter((node) => !parentMap.has(node.id));
      if (!rootNodes.length) {
        return [];
      }
      const anchorX = Math.max(22, (d3.min(rootNodes, (node) => node.x) || 0) - 78);
      const anchorY = d3.mean(rootNodes, (node) => node.y) || 0;
      return rootNodes.map((node) => ({
        id: `root-guide::${node.id}`,
        source: { id: "__root_anchor__", x: anchorX, y: anchorY },
        target: node,
        weight: 1,
        _isGuide: true,
      }));
    }

    function buildSvgShell(svg, currentZoomTransform, onZoomStart, onZoom, onZoomEnd) {
      let defs = svg.select("defs");
      if (defs.empty()) {
        defs = svg.append("defs");
      }
      if (defs.select("#arrowhead").empty()) {
        defs
          .append("marker")
          .attr("id", "arrowhead")
          .attr("viewBox", "0 -5 10 10")
          .attr("refX", 22)
          .attr("refY", 0)
          .attr("markerWidth", 7)
          .attr("markerHeight", 7)
          .attr("orient", "auto")
          .append("path")
          .attr("d", "M0,-5L10,0L0,5")
          .attr("fill", "#666");
      }

      let zoomLayer = svg.select("g.zoom-layer");
      if (zoomLayer.empty()) {
        zoomLayer = svg.append("g").attr("class", "zoom-layer");
      }
      let linkLayer = zoomLayer.select("g.links");
      if (linkLayer.empty()) {
        linkLayer = zoomLayer.append("g").attr("class", "links");
      }
      let nodeLayer = zoomLayer.select("g.nodes");
      if (nodeLayer.empty()) {
        nodeLayer = zoomLayer.append("g").attr("class", "nodes");
      }
      let labelLayer = zoomLayer.select("g.labels");
      if (labelLayer.empty()) {
        labelLayer = zoomLayer.append("g").attr("class", "labels");
      }

      svg.on(".zoom", null);
      const zoomBehavior = d3.zoom()
        .scaleExtent([0.2, 3])
        .on("start", (event) => onZoomStart(event, zoomLayer))
        .on("zoom", (event) => onZoom(event, zoomLayer))
        .on("end", (event) => onZoomEnd(event, zoomLayer));
      svg.call(zoomBehavior);
      svg.call(zoomBehavior.transform, currentZoomTransform || d3.zoomIdentity);
      return { zoomBehavior, zoomLayer, linkLayer, nodeLayer, labelLayer };
    }

    function fitTreeToViewport(layers, nodes, width, height) {
      if (!nodes.length) {
        return;
      }
      const minX = d3.min(nodes, (node) => node.x) ?? 0;
      const maxX = d3.max(nodes, (node) => node.x) ?? width;
      const minY = d3.min(nodes, (node) => node.y) ?? 0;
      const maxY = d3.max(nodes, (node) => node.y) ?? height;
      const boundsWidth = Math.max(1, maxX - minX + 160);
      const boundsHeight = Math.max(1, maxY - minY + 120);
      const scale = Math.max(0.2, Math.min(2.4, Math.min((width - 36) / boundsWidth, (height - 36) / boundsHeight)));
      const targetX = (width - (minX + maxX) * scale) / 2;
      const targetY = (height - (minY + maxY) * scale) / 2;
      const transform = d3.zoomIdentity.translate(targetX, targetY).scale(scale);
      const zoomLayer = layers.zoomLayer || d3.select(layers.linkLayer.node()?.parentNode);
      if (!zoomLayer || zoomLayer.empty()) {
        return;
      }
      if (layers.svg && layers.zoomBehavior) {
        layers.svg
          .transition()
          .duration(320)
          .ease(d3.easeCubicOut)
          .call(layers.zoomBehavior.transform, transform);
      } else {
        zoomLayer
          .transition()
          .duration(320)
          .ease(d3.easeCubicOut)
          .attr("transform", transform);
        const svgNode = zoomLayer.node()?.ownerSVGElement;
        if (svgNode) {
          svgNode.__zoom = transform;
        }
      }
    }

    function renderTreeGraph(data, width, height, layers, bindNodeInteractions) {
      const renderScope = `${layers.historyMode ? "history" : "live"}::${layers.scanTarget || "unknown"}`;
      if (renderScope !== previousRenderScope) {
        previousRenderScope = renderScope;
        previousNodePositions = new Map();
        previousParentMap = new Map();
        previousVisibleNodeIds = new Set();
        pinnedParentMap = new Map();
        layers.linkLayer.selectAll("*").interrupt().remove();
        layers.nodeLayer.selectAll("*").interrupt().remove();
        layers.labelLayer.selectAll("*").interrupt().remove();
      }
      layers.linkLayer.selectAll("line").interrupt().remove();
      const { nodes, links } = getGraphCollections(data);
      const layoutData = applyTreeLayout(nodes, links, width, height, { historyMode: Boolean(layers.historyMode) });
      const depthMap = layoutData.depthMap;
      const currentParentMap = layoutData.parentMap || new Map();
      const currentNodesById = layoutData.nodeById || new Map(nodes.map((node) => [node.id, node]));
      const renderLinks = getTreeBackboneLinks(currentParentMap, currentNodesById);
      const guideLinks = getRootGuideLinks(nodes, currentParentMap);
      const allRenderLinks = [...guideLinks, ...renderLinks];
      decorateTreeLinksForSplay(renderLinks);
      const visibleLabels = getTreeLabelVisibility(nodes, depthMap);
      const palette = getGraphPalette();
      const animationDuration = 320;
      const linkTransition = d3.transition().duration(animationDuration).ease(d3.easeCubicOut);
      const nodeTransition = d3.transition().duration(animationDuration).ease(d3.easeCubicOut);

      const linkSelection = layers.linkLayer
        .selectAll("path")
        .data(allRenderLinks, (link) => link.id || `${link.source.id}-${link.target.id}`)
        .join(
          (enter) =>
            enter
              .append("path")
              .attr("fill", "none")
              .attr("stroke", (link) => (link._isGuide ? palette.linkTreeMuted : palette.linkTree))
              .attr("stroke-width", (link) => (link._isGuide ? 1.2 : 1.8))
              .attr("marker-end", (link) => (link._isGuide ? null : "url(#arrowhead)"))
              .attr("stroke-dasharray", (link) => (link._isGuide ? "3,3" : null))
              .attr("d", (link) => {
                const sourceAnchor = resolveAnchorPosition(link.source, currentParentMap, currentNodesById);
                return treePathFromPoints(sourceAnchor, sourceAnchor);
              })
              .style("opacity", 0)
              .call((selection) =>
                selection
                  .transition(linkTransition)
                  .style("opacity", 1)
                  .attr("d", (link) => treePath(link))
              ),
          (update) =>
            update.call((selection) =>
              selection
                .transition(linkTransition)
                .attr("d", (link) => treePath(link))
            ),
          (exit) =>
            exit
              .call((selection) =>
                selection
                  .transition(linkTransition)
                  .style("opacity", 0)
                  .attr("d", (link) => {
                    const exitTarget = resolveExitPosition(link.target, currentParentMap, currentNodesById);
                    return treePathFromPoints(exitTarget, exitTarget);
                  })
              )
              .remove()
        )
        .attr("fill", "none")
        .attr("stroke", (link) => (link._isGuide ? palette.linkTreeMuted : palette.linkTree))
        .attr("stroke-width", (link) => (link._isGuide ? 1.2 : 1.8))
        .attr("marker-end", (link) => (link._isGuide ? null : "url(#arrowhead)"))
        .attr("stroke-dasharray", (link) => (link._isGuide ? "3,3" : null));

      const nodeSelection = layers.nodeLayer
        .selectAll("circle")
        .data(nodes, (node) => node.id)
        .join(
          (enter) =>
            enter
              .append("circle")
              .attr("cx", (node) => resolveAnchorPosition(node, currentParentMap, currentNodesById).x)
              .attr("cy", (node) => resolveAnchorPosition(node, currentParentMap, currentNodesById).y)
              .attr("r", 0)
              .attr("fill", (node) => getNodeFill(node))
              .attr("stroke", palette.nodeStroke)
              .attr("stroke-width", 2)
              .attr("data-node-id", (node) => node.id)
              .style("opacity", 0)
              .call((selection) =>
                selection
                  .transition(nodeTransition)
                  .attr("cx", (node) => node.x)
                  .attr("cy", (node) => node.y)
                  .attr("r", (node) => getNodeRadius(node))
                  .style("opacity", 1)
              ),
          (update) =>
            update.call((selection) =>
              selection
                .transition(nodeTransition)
                .attr("cx", (node) => node.x)
                .attr("cy", (node) => node.y)
            ),
          (exit) =>
            exit
              .transition(nodeTransition)
              .attr("cx", (node) => resolveExitPosition(node, currentParentMap, currentNodesById).x)
              .attr("cy", (node) => resolveExitPosition(node, currentParentMap, currentNodesById).y)
              .attr("r", 0)
              .style("opacity", 0)
              .remove()
        )
        .attr("r", (node) => getNodeRadius(node))
        .attr("fill", (node) => getNodeFill(node))
        .attr("stroke", palette.nodeStroke)
        .attr("stroke-width", 2)
        .attr("data-node-id", (node) => node.id);
      bindNodeInteractions(nodeSelection);

      const labelSelection = layers.labelLayer
        .selectAll("text")
        .data(nodes, (node) => node.id)
        .join(
          (enter) =>
            enter
              .append("text")
              .attr("x", (node) => resolveAnchorPosition(node, currentParentMap, currentNodesById).x + 10)
              .attr("y", (node) => resolveAnchorPosition(node, currentParentMap, currentNodesById).y + 4)
              .style("opacity", 0)
              .call((selection) =>
                selection
                  .transition(linkTransition)
                  .style("opacity", 1)
                  .attr("x", (node) => node.x + getNodeRadius(node) + 10)
                  .attr("y", (node) => node.y + 4)
              ),
          (update) =>
            update.call((selection) =>
              selection
                .transition(linkTransition)
                .attr("x", (node) => node.x + getNodeRadius(node) + 10)
                .attr("y", (node) => node.y + 4)
            ),
          (exit) =>
            exit
              .transition(linkTransition)
              .attr("x", (node) => resolveExitPosition(node, currentParentMap, currentNodesById).x + 10)
              .attr("y", (node) => resolveExitPosition(node, currentParentMap, currentNodesById).y + 4)
              .style("opacity", 0)
              .remove()
        )
        .text((node) => {
          const label = String(node.name || "");
          const readable = label.length > 34 ? `${label.slice(0, 31)}...` : label;
          return node.isCluster ? `${readable} (${node.memberCount})` : readable;
        })
        .attr("display", (node) => (visibleLabels.has(node.id) ? null : "none"))
        .attr("fill", palette.label)
        .attr("font-size", (node) => node.isCluster ? 12.5 : 12)
        .attr("font-weight", (node) => node.isCluster ? 700 : 500)
        .attr("text-anchor", "start")
        .attr("pointer-events", "none");

      const currentIds = new Set(nodes.map((node) => node.id));
      const shouldAutoFit =
        !previousVisibleNodeIds.size;

      previousNodePositions = new Map(nodes.map((node) => [node.id, { x: node.x, y: node.y }]));
      previousParentMap = new Map([...currentParentMap.entries()].filter(([childId, parentId]) => currentIds.has(childId) && currentIds.has(parentId)));
      previousVisibleNodeIds = currentIds;
      pinnedParentMap = new Map([...pinnedParentMap.entries()].filter(([childId, parentId]) => currentIds.has(childId) && currentIds.has(parentId)));
      if (shouldAutoFit) {
        fitTreeToViewport(layers, nodes, width, height);
      }

      return { linkSelection, nodeSelection, labelSelection, simulation: null };
    }

    function renderForceGraph(data, width, height, layers, bindNodeInteractions, dragHandlers) {
      layers.linkLayer.selectAll("path").interrupt().remove();
      const { nodes, links } = getGraphCollections(data);
      const palette = getGraphPalette();
      const linkTransition = d3.transition().duration(320).ease(d3.easeCubicOut);
      const nodeTransition = d3.transition().duration(420).ease(d3.easeBackOut.overshoot(1.1));
      const simulation = d3
        .forceSimulation(nodes)
        .force("link", d3.forceLink(links).id((node) => node.id).distance((link) => link.source.isCluster || link.target.isCluster ? 160 : 120))
        .force("charge", d3.forceManyBody().strength((node) => node.isCluster ? -520 : -300))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collide", d3.forceCollide().radius((node) => getNodeRadius(node) + 12))
        .alphaDecay(0.08)
        .velocityDecay(0.45);

      const linkSelection = layers.linkLayer
        .selectAll("line")
        .data(links, (link) => `${link.source.id}-${link.target.id}`)
        .join(
          (enter) =>
            enter
              .append("line")
              .style("opacity", 0)
              .call((selection) => selection.transition(linkTransition).style("opacity", 1)),
          (update) => update,
          (exit) => exit.transition(linkTransition).style("opacity", 0).remove()
        )
        .attr("stroke", palette.linkForce)
        .attr("stroke-width", (link) => Math.min(3, 1 + (link.weight || 1) * 0.16))
        .attr("marker-end", "url(#arrowhead)");

      const nodeSelection = layers.nodeLayer
        .selectAll("circle")
        .data(nodes, (node) => node.id)
        .join(
          (enter) =>
            enter
              .append("circle")
              .attr("r", 0)
              .style("opacity", 0)
              .call((selection) =>
                selection
                  .transition(nodeTransition)
                  .attr("r", (node) => getNodeRadius(node))
                  .style("opacity", 1)
              ),
          (update) => update,
          (exit) =>
            exit
              .transition(linkTransition)
              .attr("r", 0)
              .style("opacity", 0)
              .remove()
        )
        .attr("r", (node) => getNodeRadius(node))
        .attr("fill", (node) => getNodeFill(node))
        .attr("stroke", palette.nodeStroke)
        .attr("stroke-width", 2)
        .attr("data-node-id", (node) => node.id)
        .call(d3.drag().on("start", dragHandlers.dragStarted).on("drag", dragHandlers.dragged).on("end", dragHandlers.dragEnded));
      bindNodeInteractions(nodeSelection);

      const labelSelection = layers.labelLayer
        .selectAll("text")
        .data(nodes, (node) => node.id)
        .join(
          (enter) =>
            enter
              .append("text")
              .style("opacity", 0)
              .call((selection) => selection.transition(linkTransition).style("opacity", 1)),
          (update) => update,
          (exit) => exit.transition(linkTransition).style("opacity", 0).remove()
        )
        .text((node) => node.isCluster ? `${truncateLabel(node.name)} (${node.memberCount})` : truncateLabel(node.name))
        .attr("fill", palette.label)
        .attr("font-size", (node) => node.isCluster ? 12.5 : 12)
        .attr("font-weight", (node) => node.isCluster ? 700 : 500)
        .attr("text-anchor", "middle")
        .attr("pointer-events", "none");

      simulation.on("tick", () => {
        linkSelection
          .attr("x1", (link) => link.source.x)
          .attr("y1", (link) => link.source.y)
          .attr("x2", (link) => link.target.x)
          .attr("y2", (link) => link.target.y);
        nodeSelection.attr("cx", (node) => node.x).attr("cy", (node) => node.y);
        labelSelection.attr("x", (node) => node.x).attr("y", (node) => node.y + getNodeRadius(node) + 14);
      });

      return { linkSelection, nodeSelection, labelSelection, simulation };
    }

    return {
      buildSvgShell,
      renderForceGraph,
      renderTreeGraph,
    };
  }

  globalScope.CodeWeaveGraphRenderer = { createGraphRenderer };
})(window);
