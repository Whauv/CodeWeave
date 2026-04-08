(function attachCodeWeaveStore(globalScope){
  const GRAPH_CACHE_DB_NAME = "codeweave-graph-cache";
  const GRAPH_CACHE_STORE = "snapshots";
  let graphCacheDbPromise = null;

  function openGraphCacheDb() {
    if (graphCacheDbPromise) {
      return graphCacheDbPromise;
    }
    graphCacheDbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(GRAPH_CACHE_DB_NAME, 1);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(GRAPH_CACHE_STORE)) {
          db.createObjectStore(GRAPH_CACHE_STORE, { keyPath: "target" });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    return graphCacheDbPromise;
  }

  async function saveGraphSnapshot(target, data) {
    if (!target || !data) {
      return;
    }
    try {
      const db = await openGraphCacheDb();
      await new Promise((resolve, reject) => {
        const tx = db.transaction(GRAPH_CACHE_STORE, "readwrite");
        tx.objectStore(GRAPH_CACHE_STORE).put({ target, data, updatedAt: Date.now() });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    } catch (error) {
      console.error(error);
    }
  }

  async function getGraphSnapshot(target) {
    if (!target) {
      return null;
    }
    try {
      const db = await openGraphCacheDb();
      return await new Promise((resolve, reject) => {
        const request = db.transaction(GRAPH_CACHE_STORE, "readonly").objectStore(GRAPH_CACHE_STORE).get(target);
        request.onsuccess = () => resolve(request.result || null);
        request.onerror = () => reject(request.error);
      });
    } catch (error) {
      console.error(error);
      return null;
    }
  }

  function persistLocalList(key, value) {
    localStorage.setItem(key, JSON.stringify(value));
  }

  function loadLocalList(key) {
    try {
      const raw = localStorage.getItem(key);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      console.error(error);
      return [];
    }
  }

  globalScope.CodeWeaveStore = {
    getGraphSnapshot,
    loadLocalList,
    persistLocalList,
    saveGraphSnapshot,
  };
})(window);
