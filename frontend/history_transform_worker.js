(function attachHistoryTransformWorker(globalScope) {
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

  function computeSnapshotTransform(previousNodes, nextNodes, liveNodes) {
    const beforeByIdentity = new Map(previousNodes.map((node) => [getNodeIdentity(node), node]));
    const afterByIdentity = new Map(nextNodes.map((node) => [getNodeIdentity(node), node]));
    const addedIds = new Set();
    const updatedIds = new Set();
    const removed = [];

    afterByIdentity.forEach((afterNode, identity) => {
      const beforeNode = beforeByIdentity.get(identity);
      if (!beforeNode) {
        addedIds.add(afterNode.id);
        return;
      }
      const changed =
        (beforeNode.line || 0) !== (afterNode.line || 0) ||
        String(beforeNode.source_code || "") !== String(afterNode.source_code || "");
      if (changed) {
        updatedIds.add(afterNode.id);
      }
    });

    beforeByIdentity.forEach((_beforeNode, identity) => {
      if (!afterByIdentity.has(identity)) {
        removed.push(identity);
      }
    });

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

    const decoratedNodes = nextNodes.map((node) => {
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

    return {
      transition: {
        added: Array.from(addedIds),
        removed,
        updated: Array.from(updatedIds),
      },
      decoratedNodes,
    };
  }

  globalScope.onmessage = (event) => {
    const payload = event?.data || {};
    if (payload.type !== "compute_snapshot_transform") {
      return;
    }
    try {
      const previousNodes = Array.isArray(payload.previousNodes) ? payload.previousNodes : [];
      const nextNodes = Array.isArray(payload.nextNodes) ? payload.nextNodes : [];
      const liveNodes = Array.isArray(payload.liveNodes) ? payload.liveNodes : [];
      const result = computeSnapshotTransform(previousNodes, nextNodes, liveNodes);
      globalScope.postMessage({ requestId: payload.requestId, ok: true, result });
    } catch (error) {
      globalScope.postMessage({
        requestId: payload.requestId,
        ok: false,
        error: String(error?.message || error || "transform failed"),
      });
    }
  };
})(self);
