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
      const left = 110;
      const right = 140;
      const top = 110;
      const bottom = 90;
      const xStep = ordered.length > 1 ? (width - left - right) / (ordered.length - 1) : 0;

      ordered.forEach((depth) => {
        const layer = layers.get(depth).sort(
          (a, b) =>
            ((b.outgoingCount || 0) + (b.incomingCount || 0)) -
              ((a.outgoingCount || 0) + (a.incomingCount || 0)) ||
            a.name.localeCompare(b.name)
        );
        const yStep = layer.length > 1 ? (height - top - bottom) / (layer.length - 1) : 0;
        layer.forEach((node, index) => {
          node.x = left + depth * xStep;
          node.y = layer.length === 1 ? height / 2 : top + index * yStep;
        });
      });
    }

    function treePath(link) {
      const midX = (link.source.x + link.target.x) / 2;
      return `M${link.source.x},${link.source.y} C${midX},${link.source.y} ${midX},${link.target.y} ${link.target.x},${link.target.y}`;
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
      const palette = getGraphPalette();
      applyTreeLayout(nodes, links, width, height);

      const linkSelection = layers.linkLayer
        .selectAll("path")
        .data(links, (link) => `${link.source.id}-${link.target.id}`)
        .join("path")
        .attr("d", (link) => treePath(link))
        .attr("fill", "none")
        .attr("stroke", palette.linkTree)
        .attr("stroke-width", (link) => Math.min(3, 1.1 + (link.weight || 1) * 0.2))
        .attr("marker-end", "url(#arrowhead)");

      const nodeSelection = layers.nodeLayer
        .selectAll("circle")
        .data(nodes, (node) => node.id)
        .join("circle")
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
        .join("text")
        .text((node) => node.isCluster ? `${truncateLabel(node.name)} (${node.memberCount})` : truncateLabel(node.name))
        .attr("x", (node) => node.x + getNodeRadius(node) + 10)
        .attr("y", (node) => node.y + 4)
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
        .join("line")
        .attr("stroke", palette.linkForce)
        .attr("stroke-width", (link) => Math.min(3, 1 + (link.weight || 1) * 0.16))
        .attr("marker-end", "url(#arrowhead)");

      const nodeSelection = layers.nodeLayer
        .selectAll("circle")
        .data(nodes, (node) => node.id)
        .join("circle")
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
        .join("text")
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
