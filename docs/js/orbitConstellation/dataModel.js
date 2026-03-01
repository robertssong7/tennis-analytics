// docs/js/orbitConstellation/dataModel.js
let cachedOrbitGraph = null;

export async function fetchGlobalOrbitGraph() {
    if (cachedOrbitGraph) return cachedOrbitGraph;
    try {
        const resp = await fetch('data/globalPatternGraph.json');
        if (!resp.ok) throw new Error('Global orbit graph not found.');
        const data = await resp.json();
        cachedOrbitGraph = data.nodes || [];
        return cachedOrbitGraph;
    } catch (err) {
        console.error("Orbit Map:", err);
        return [];
    }
}
