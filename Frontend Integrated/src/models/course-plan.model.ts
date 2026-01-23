export interface Course {
  id: string;
  name: string;
  description: string;
  prerequisites: string[];
}

export interface Recommendation {
  name: string;
  reason: string;
}

export interface LlmFlowchart {
  mermaid: string;
  explanation: string;
}

export interface GraphNode {
  id: string;
  label: string;
  level?: number;
  status?: string;
}

export interface GraphEdge {
  from: string;
  to: string;
  label?: string;
  arrows?: string;
  dashes?: boolean;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface CoursePlan {
  dept: string;
  completed: string[];
  eligible: string[];
  graph: Graph;

  // Text fallback
  rag_response: string;

  // Structured recommendations
  recommendations: Recommendation[];

  // Mermaid diagram
  llm_flowchart: LlmFlowchart;
}
