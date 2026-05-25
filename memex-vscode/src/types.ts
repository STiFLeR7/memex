export interface GraphNode {
  id: string;
  name: string;
  type: 'Module' | 'Symbol' | 'Decision' | 'Problem';
  summary: string;
  created_at: string;
  status?: string;
  scope?: string;
  source_commit?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
  created_at: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
