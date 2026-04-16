(function attachGraphRenderer(globalScope) {
  function createGraphRenderer(deps) {
    const {
      getGraphCollections,
      getGraphPalette,
      getNodeFill,
      getNodeRadius,
      truncateLabel,
    } = deps;

    function applyTreeLayout(nodes, links, width, height) {
      const incoming = new Map(nodes.map((node) => [node.id, []]));
      const outgoing = new Map(nodes.map((node) => [node.id, []]));
      const nodeById = new Map(nodes.map((node) => [node.id, node]));
      links.forEach((link) => {
        incoming.get(link.target.id)?.push(link.source.id);
        outgoing.get(link.source.id)?.push(link.target.id);
      });

      let roots = nodes.filter((node) => (incoming.get(node.id) || []).length === 0);
      if (!roots.length && nodes.length) {
        roots = [...nodes].sort((a, b) => (b.outgoingCount || 0) - (a.outgoingCount || 0)).slice(0, 1);
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

      let fallback = depthMap.size ? Math.max(...depthMap.values()) + 1 : 0;
      nodes.forEach((node) => {
        if (!depthMap.has(node.id)) {
          depthMap.set(node.id, fallback);
          fallback += 1;
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

      const ordered = [...layers.keys()].sort((a, b) => a - b);
      const nodeCount = nodes.length;
      const left = 110;
      const right = 140;
      const top = 110;
      const bottom = 90;
      const xStep = ordered.length > 1 ? (width - left - right) / (ordered.length - 1) : 0;

      const layerOrderMap = new Map();
      ordered.forEach((depth) => {
        const rawLayer = layers.get(depth) || [];
        const seeded = rawLayer.slice().sort((a, b) => {
          const clusterA = String(a.clusterKey || a.file || a.name);
          const clusterB = String(b.clusterKey || b.file || b.name);
          return (
            clusterA.localeCompare(clusterB) ||
            (((b.outgoingCount || 0) + (b.incomingCount || 0)) -
              ((a.outgoingCount || 0) + (a.incomingCount || 0))) ||
            a.name.localeCompare(b.name)
          );
        });
        layers.set(depth, seeded);
        const seedOrder = new Map();
        seeded.forEach((node, index) => seedOrder.set(node.id, index));
        layerOrderMap.set(depth, seedOrder);
      });

      const sortLayerByBarycenter = (depth, direction) => {
        const rawLayer = layers.get(depth) || [];
        const neighborDepth = direction === "forward" ? depth - 1 : depth + 1;
        const neighborOrder = layerOrderMap.get(neighborDepth) || new Map();
        const neighborFn = direction === "forward" ? incoming : outgoing;
        const fallbackBarycenter = Number.MAX_SAFE_INTEGER;

        const sortedLayer = rawLayer.slice().sort((a, b) => {
          const neighborsA = neighborFn.get(a.id) || [];
          const neighborsB = neighborFn.get(b.id) || [];
          const barycenterA = neighborsA.length
            ? neighborsA.reduce((sum, id) => sum + (neighborOrder.get(id) ?? 0), 0) / neighborsA.length
            : fallbackBarycenter;
          const barycenterB = neighborsB.length
            ? neighborsB.reduce((sum, id) => sum + (neighborOrder.get(id) ?? 0), 0) / neighborsB.length
            : fallbackBarycenter;
          const clusterA = String(a.clusterKey || a.file || a.name);
          const clusterB = String(b.clusterKey || b.file || b.name);
          return (
            barycenterA - barycenterB ||
            clusterA.localeCompare(clusterB) ||
            (((b.outgoingCount || 0) + (b.incomingCount || 0)) -
              ((a.outgoingCount || 0) + (a.incomingCount || 0))) ||
            a.name.localeCompare(b.name)
          );
        });

        layers.set(depth, sortedLayer);
        const layerOrder = new Map();
        sortedLayer.forEach((node, index) => layerOrder.set(node.id, index));
        layerOrderMap.set(depth, layerOrder);
      };

      const sweepIterations = nodeCount > 320 ? 3 : nodeCount > 160 ? 2 : 1;
      for (let iteration = 0; iteration < sweepIterations; iteration += 1) {
        ordered.forEach((depth, index) => {
          if (index === 0) {
            return;
          }
          sortLayerByBarycenter(depth, "forward");
        });
        ordered
          .slice()
          .reverse()
          .forEach((depth, reversedIndex) => {
            if (reversedIndex === 0) {
              return;
            }
            sortLayerByBarycenter(depth, "backward");
          });
      }

      ordered.forEach((depth) => {
        const layer = layers.get(depth) || [];

        const grouped = [];
        let currentGroup = null;
        layer.forEach((node) => {
          const groupKey = String(node.clusterKey || node.file || node.name);
          if (!currentGroup || currentGroup.key !== groupKey) {
            currentGroup = { key: groupKey, nodes: [] };
            grouped.push(currentGroup);
          }
          currentGroup.nodes.push(node);
        });

        const usableHeight = Math.max(220, height - top - bottom);
        const layerDensity = layer.length / Math.max(1, ordered.length);
        const densityFactor = nodeCount > 320 ? 0.62 : nodeCount > 180 ? 0.74 : 0.88;
        const intraNodeGapBase = Math.max(
          11,
          Math.min(30, (usableHeight / Math.max(layer.length + grouped.length, 8)) * densityFactor * (layerDensity > 26 ? 0.86 : 1))
        );
        const interGroupGapBase = Math.max(6, Math.min(24, intraNodeGapBase * (nodeCount > 260 ? 0.55 : 0.8)));
        const maxLayerRows = nodeCount > 460 ? 34 : nodeCount > 320 ? 40 : nodeCount > 220 ? 48 : 60;
        const laneCount = Math.min(3, Math.max(1, Math.ceil(layer.length / maxLayerRows)));
        const laneSpread = laneCount > 1 ? Math.min(Math.max(26, xStep * 0.42), nodeCount > 320 ? 96 : 84) : 0;
        const laneBuckets = Array.from({ length: laneCount }, () => ({ groups: [], count: 0 }));

        if (laneCount === 1) {
          laneBuckets[0].groups = grouped.slice();
          laneBuckets[0].count = layer.length;
        } else {
          grouped.forEach((group) => {
            const targetLane = laneBuckets.reduce(
              (bestIndex, lane, laneIndex) =>
                lane.count < laneBuckets[bestIndex].count ? laneIndex : bestIndex,
              0
            );
            laneBuckets[targetLane].groups.push(group);
            laneBuckets[targetLane].count += group.nodes.length;
          });
        }

        laneBuckets.forEach((lane, laneIndex) => {
          const intraNodeGap = Math.max(10, intraNodeGapBase * (laneCount > 1 ? 0.92 : 1));
          const interGroupGap = Math.max(5, interGroupGapBase * (laneCount > 1 ? 0.9 : 1));
          const totalHeight =
            lane.groups.reduce((sum, group) => sum + group.nodes.length * intraNodeGap, 0) +
            Math.max(0, lane.groups.length - 1) * interGroupGap;
          let cursorY = Math.max(top, (height - totalHeight) / 2);
          const laneOffset =
            laneCount === 1
              ? 0
              : ((laneIndex - (laneCount - 1) / 2) / Math.max(1, laneCount - 1)) * laneSpread;

          lane.groups.forEach((group) => {
            group.nodes.forEach((node) => {
              node.x = left + depth * xStep + laneOffset;
              node.y = cursorY;
              cursorY += intraNodeGap;
            });
            cursorY += interGroupGap;
          });
        });
      });

      nodes.forEach((node) => {
        node.x = Math.max(left, Math.min(width - right, node.x || width / 2));
        node.y = Math.max(top, Math.min(height - bottom, node.y || height / 2));
      });

      return depthMap;
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
      if (nodes.length <= 140) {
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
        const keepTop = Math.max(10, Math.floor(layerNodes.length * (nodes.length > 320 ? 0.24 : 0.34)));
        ranked.slice(0, keepTop).forEach((node) => visible.add(node.id));
        ranked.forEach((node, index) => {
          if (index % stride === 0) {
            visible.add(node.id);
          }
        });
      });

      return visible;
    }

    function buildSvgShell(svg, currentZoomTransform, onZoomStart, onZoom, onZoomEnd) {
      svg.selectAll("*").remove();
      const defs = svg.append("defs");
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

      const zoomLayer = svg.append("g").attr("class", "zoom-layer");
      const linkLayer = zoomLayer.append("g").attr("class", "links");
      const nodeLayer = zoomLayer.append("g").attr("class", "nodes");
      const labelLayer = zoomLayer.append("g").attr("class", "labels");
      const zoomBehavior = d3.zoom()
        .scaleExtent([0.2, 3])
        .on("start", (event) => onZoomStart(event, zoomLayer))
        .on("zoom", (event) => onZoom(event, zoomLayer))
        .on("end", (event) => onZoomEnd(event, zoomLayer));
      svg.call(zoomBehavior);
      svg.call(zoomBehavior.transform, currentZoomTransform);
      return { zoomBehavior, zoomLayer, linkLayer, nodeLayer, labelLayer };
    }

    function renderTreeGraph(data, width, height, layers, bindNodeInteractions) {
      const { nodes, links } = getGraphCollections(data);
      const depthMap = applyTreeLayout(nodes, links, width, height);
      const renderLinks = getDeclutteredTreeLinks(nodes, links, depthMap);
      decorateTreeLinksForSplay(renderLinks);
      const visibleLabels = getTreeLabelVisibility(nodes, depthMap);
      const palette = getGraphPalette();

      const linkTransition = d3.transition().duration(420).ease(d3.easeCubicOut);
      const nodeTransition = d3.transition().duration(520).ease(d3.easeBackOut.overshoot(1.15));

      const linkSelection = layers.linkLayer
        .selectAll("path")
        .data(renderLinks, (link) => `${link.source.id}-${link.target.id}`)
        .join(
          (enter) =>
            enter
              .append("path")
              .attr("fill", "none")
              .attr("stroke", palette.linkTree)
              .attr("stroke-width", (link) => Math.min(3, 1.1 + (link.weight || 1) * 0.2))
              .attr("marker-end", "url(#arrowhead)")
              .attr("d", (link) => treePath(link))
              .style("opacity", 0)
              .call((selection) => selection.transition(linkTransition).style("opacity", 1)),
          (update) => update,
          (exit) => exit.transition(linkTransition).style("opacity", 0).remove()
        )
        .attr("d", (link) => treePath(link))
        .attr("fill", "none")
        .attr("stroke", palette.linkTree)
        .attr("stroke-width", (link) => Math.min(3, 1.1 + (link.weight || 1) * 0.2))
        .attr("marker-end", "url(#arrowhead)");

      const nodeSelection = layers.nodeLayer
        .selectAll("circle")
        .data(nodes, (node) => node.id)
        .join(
          (enter) =>
            enter
              .append("circle")
              .attr("cx", (node) => node.x)
              .attr("cy", (node) => node.y)
              .attr("r", 0)
              .attr("fill", (node) => getNodeFill(node))
              .attr("stroke", palette.nodeStroke)
              .attr("stroke-width", 2)
              .attr("data-node-id", (node) => node.id)
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
        .attr("cx", (node) => node.x)
        .attr("cy", (node) => node.y)
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
              .style("opacity", 0)
              .call((selection) => selection.transition(linkTransition).style("opacity", 1)),
          (update) => update,
          (exit) => exit.transition(linkTransition).style("opacity", 0).remove()
        )
        .text((node) => node.isCluster ? `${truncateLabel(node.name)} (${node.memberCount})` : truncateLabel(node.name))
        .attr("x", (node) => node.x + getNodeRadius(node) + 10)
        .attr("y", (node) => node.y + 4)
        .attr("display", (node) => (visibleLabels.has(node.id) ? null : "none"))
        .attr("fill", palette.label)
        .attr("font-size", (node) => node.isCluster ? 12.5 : 12)
        .attr("font-weight", (node) => node.isCluster ? 700 : 500)
        .attr("text-anchor", "start")
        .attr("pointer-events", "none");

      return { linkSelection, nodeSelection, labelSelection, simulation: null };
    }

    function renderForceGraph(data, width, height, layers, bindNodeInteractions, dragHandlers) {
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
