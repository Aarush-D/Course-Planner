// Models that match the Flask /api/plan response

export interface GraphNode {
  id: string;
  label?: string;
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

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface LlmFlowchart {
  mermaid: string;
  explanation: string;
}

export interface CoursePlanResponse {
  dept: string;
  completed: string[];
  eligible: string[];
  graph: GraphPayload;
  rag_response: string;
  semantic_results: any[];
  search_results: any[];
  why_not_answer: string;
  llm_flowchart: LlmFlowchart;
}

// Backwards-compatible alias (older frontend code may import CoursePlan)
export type CoursePlan = CoursePlanResponse;

