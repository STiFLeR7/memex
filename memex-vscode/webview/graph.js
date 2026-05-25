const vscode = acquireVsCodeApi();

const svg = d3.select("#graph-svg");
const container = svg.append("g");

let width = window.innerWidth;
let height = window.innerHeight;
svg.attr("width", width).attr("height", height);

const zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on("zoom", (event) => {
        container.attr("transform", event.transform);
    });
svg.call(zoom);

let nodes = [];
let edges = [];
let selectedNode = null;
let activeFilePath = null;

const simulation = d3.forceSimulation()
    .force("link", d3.forceLink().id(d => d.id).distance(100))
    .force("charge", d3.forceManyBody().strength(-150))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collision", d3.forceCollide().radius(25));

window.addEventListener("resize", () => {
    width = window.innerWidth;
    height = window.innerHeight;
    svg.attr("width", width).attr("height", height);
    simulation.force("center", d3.forceCenter(width / 2, height / 2));
    simulation.alpha(0.3).restart();
});

vscode.postMessage({ type: 'ready' });

window.addEventListener('message', event => {
    const message = event.data;
    switch (message.type) {
        case 'updateGraph':
            document.getElementById("connection-status").textContent = "Connected";
            updateGraph(message.data);
            break;
        case 'highlightActiveFile':
            activeFilePath = message.path;
            highlightActiveFileNode();
            break;
        case 'error':
            document.getElementById("connection-status").textContent = "Offline/Error";
            console.error("memex extension error:", message.message);
            break;
    }
});

document.getElementById("btn-refresh").addEventListener("click", () => {
    vscode.postMessage({ type: 'ready' });
});

document.getElementById("btn-reset-zoom").addEventListener("click", () => {
    svg.transition().duration(750).call(
        zoom.transform,
        d3.zoomIdentity
    );
});

document.getElementById("panel-close-btn").addEventListener("click", () => {
    closeDetailPanel();
});

function closeDetailPanel() {
    document.getElementById("detail-panel").classList.add("hidden");
    selectedNode = null;
    resetHighlights();
}

function updateGraph(data) {
    nodes = data.nodes || [];
    edges = data.edges || [];
    
    container.selectAll("*").remove();

    container.append("defs").selectAll("marker")
        .data(["suit"])
        .enter().append("marker")
        .attr("id", d => `arrow-${d}`)
        .attr("viewBox", "0 -5 10 10")
        .attr("refX", 20)
        .attr("refY", 0)
        .attr("markerWidth", 6)
        .attr("markerHeight", 6)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-5L10,0L0,5")
        .attr("fill", "var(--vscode-descriptionForeground, #858585)");

    const link = container.append("g")
        .attr("class", "links")
        .selectAll("line")
        .data(edges)
        .enter().append("line")
        .attr("class", d => `edge ${d.type || 'default'}`)
        .attr("marker-end", "url(#arrow-suit)")
        .attr("stroke-opacity", 0.6);

    const node = container.append("g")
        .attr("class", "nodes")
        .selectAll("g")
        .data(nodes)
        .enter().append("g")
        .attr("class", "node-group")
        .call(d3.drag()
            .on("start", dragstarted)
            .on("drag", dragged)
            .on("end", dragended)
        );

    node.each(function(d) {
        const g = d3.select(this);
        
        if (d.type === 'Cluster') {
            g.append("circle")
                .attr("class", "node cluster")
                .attr("r", 18)
                .attr("fill", "var(--color-cluster)")
                .attr("stroke", "var(--bg-color)")
                .attr("stroke-width", "2px");
        } else if (d.type === 'Module') {
            g.append("circle")
                .attr("class", "node module")
                .attr("r", 12)
                .attr("fill", "var(--color-module)")
                .attr("stroke", "var(--bg-color)")
                .attr("stroke-width", "1.5px");
        } else if (d.type === 'Symbol') {
            g.append("rect")
                .attr("class", "node symbol")
                .attr("width", 14)
                .attr("height", 14)
                .attr("x", -7)
                .attr("y", -7)
                .attr("fill", "var(--color-symbol)")
                .attr("stroke", "var(--bg-color)")
                .attr("stroke-width", "1.5px");
        } else if (d.type === 'Decision') {
            g.append("polygon")
                .attr("class", "node decision")
                .attr("points", "0,-10 10,0 0,10 -10,0")
                .attr("fill", "var(--color-decision)")
                .attr("stroke", "var(--bg-color)")
                .attr("stroke-width", "1.5px");
        } else if (d.type === 'Problem') {
            g.append("polygon")
                .attr("class", "node problem")
                .attr("points", "0,-10 9,7 -9,7")
                .attr("fill", "var(--color-problem)")
                .attr("stroke", "var(--bg-color)")
                .attr("stroke-width", "1.5px");
        }
        
        g.append("circle")
            .attr("class", "active-ring hidden")
            .attr("r", d.type === 'Cluster' ? 22 : 16)
            .attr("fill", "none")
            .attr("stroke", "var(--color-cluster)")
            .attr("stroke-width", "3px");
    });

    node.append("text")
        .attr("class", "node-label")
        .attr("dx", d => d.type === 'Cluster' ? 22 : 16)
        .attr("dy", ".35em")
        .text(d => d.name)
        .style("display", d => (d.type === 'Cluster' || d.type === 'Module') ? 'block' : 'none');

    node.on("mouseover", function(event, d) {
        if (d.type !== 'Cluster' && d.type !== 'Module') {
            d3.select(this).select("text").style("display", "block");
        }
    }).on("mouseout", function(event, d) {
        if (d.type !== 'Cluster' && d.type !== 'Module' && selectedNode !== d) {
            d3.select(this).select("text").style("display", "none");
        }
    });

    node.on("click", function(event, d) {
        event.stopPropagation();
        selectedNode = d;
        highlightNodeConnections(d);
        showNodeDetails(d);
    });

    node.on("dblclick", function(event, d) {
        if (d.type === 'Module' || d.type === 'Symbol') {
            vscode.postMessage({
                type: 'openFile',
                path: d.name
            });
        }
    });

    svg.on("click", () => {
        closeDetailPanel();
    });

    simulation.nodes(nodes);
    simulation.force("link").links(edges);
    simulation.alpha(1).restart();

    simulation.on("tick", () => {
        link
            .attr("x1", d => d.source.x)
            .attr("y1", d => d.source.y)
            .attr("x2", d => d.target.x)
            .attr("y2", d => d.target.y);

        node
            .attr("transform", d => `translate(${d.x},${d.y})`);
    });

    highlightActiveFileNode();
}

function showNodeDetails(node) {
    document.getElementById("detail-panel").classList.remove("hidden");
    document.getElementById("detail-title").textContent = node.name;
    document.getElementById("detail-type").textContent = node.type;
    
    const statusContainer = document.getElementById("detail-status-container");
    if (node.status) {
        statusContainer.classList.remove("hidden");
        document.getElementById("detail-status").textContent = node.status;
    } else {
        statusContainer.classList.add("hidden");
    }

    const scopeContainer = document.getElementById("detail-scope-container");
    if (node.scope) {
        scopeContainer.classList.remove("hidden");
        document.getElementById("detail-scope").textContent = node.scope;
    } else {
        scopeContainer.classList.add("hidden");
    }

    const commitContainer = document.getElementById("detail-commit-container");
    if (node.source_commit) {
        commitContainer.classList.remove("hidden");
        document.getElementById("detail-commit").textContent = node.source_commit.substring(0, 7);
    } else {
        commitContainer.classList.add("hidden");
    }

    document.getElementById("detail-created").textContent = node.created_at ? new Date(node.created_at).toLocaleString() : 'N/A';
    document.getElementById("detail-summary").textContent = node.summary || 'No summary available.';
}

function highlightNodeConnections(centerNode) {
    const connectedNodeIds = new Set([centerNode.id]);
    edges.forEach(edge => {
        if (edge.source.id === centerNode.id) {
            connectedNodeIds.add(edge.target.id);
        } else if (edge.target.id === centerNode.id) {
            connectedNodeIds.add(edge.source.id);
        }
    });

    svg.selectAll(".node-group").style("opacity", d => connectedNodeIds.has(d.id) ? 1.0 : 0.15);
    svg.selectAll(".edge").style("opacity", d => 
        (d.source.id === centerNode.id || d.target.id === centerNode.id) ? 1.0 : 0.1
    );

    svg.selectAll(".node-group").select("text").style("display", d => 
        (d.type === 'Cluster' || d.type === 'Module' || d.id === centerNode.id) ? 'block' : 'none'
    );
}

function resetHighlights() {
    svg.selectAll(".node-group").style("opacity", 1.0);
    svg.selectAll(".edge").style("opacity", 0.6);
    svg.selectAll(".node-group").select("text").style("display", d => 
        (d.type === 'Cluster' || d.type === 'Module') ? 'block' : 'none'
    );
    highlightActiveFileNode();
}

function highlightActiveFileNode() {
    if (!activeFilePath) return;

    svg.selectAll(".node-group").each(function(d) {
        const ring = d3.select(this).select(".active-ring");
        if (d.type === 'Module' && d.name === activeFilePath) {
            ring.classed("hidden", false);
        } else {
            ring.classed("hidden", true);
        }
    });
}

function dragstarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
}

function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
}

function dragended(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
}
