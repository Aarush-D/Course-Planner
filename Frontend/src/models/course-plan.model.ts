export interface Course {
  id: string;
  name: string;
  description: string;
  prerequisites: string[];
  // Planner metadata (present on cards produced by the planning engine)
  credits?: number | null;
  reason?: string;
  flowchartSemester?: number;
  type?: 'course' | 'slot';
  etm?: boolean;
  unlocks?: number;
}

export interface Recommendation {
  name: string;
  reason: string;
  credits?: number | null;
  type?: 'course' | 'slot';
  flowchartSemester?: number;
  /** Weighted priority score computed by the deterministic engine */
  score?: number;
  /** "Official Advising Flowchart" | "Course Catalog" */
  source?: string;
  /** Course title, when known */
  title?: string;
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

export interface MatchedCourse {
  code: string;
  name: string;
  credits: number | null;
  mention: string;
}

export interface MatchedInfo {
  courses: MatchedCourse[];
  removed?: MatchedCourse[];
  summerUnavailable?: MatchedCourse[];
  unmatched: string[];
  treatedAsCompleted: boolean;
}

export interface BlockedCourse {
  code: string;
  name: string;
  flowchart_semester: number;
  missing: string[];
}

export interface NextSemester {
  label?: string;
  isSummer?: boolean;
  totalCredits: number;
  courses: Course[];
  blocked: BlockedCourse[];
}

export interface PlanTerm {
  index: number;
  label?: string;
  isSummer?: boolean;
  withinGoal?: boolean;
  totalCredits: number;
  courses: Course[];
}

export interface GraduationGoal {
  startYear: number;
  gradYears: number;
  deadline: string;
  allowSummer: boolean;
  met: boolean;
}

export interface FullPlan {
  terms: PlanTerm[];
  warnings: string[];
  goal?: GraduationGoal;
}

export interface PlannerStateInfo {
  dept: string;
  completed: string[];
  startYear?: number;
  gradYears?: number;
  allowSummer?: boolean;
  summerUnavailable?: string[];
}

export interface CategoryProgress {
  doneItems: number;
  totalItems: number;
  creditsDone: number;
  totalCredits: number;
  percent: number;
}

export interface Progress {
  doneItems: number;
  totalItems: number;
  creditsDone: number;
  totalCredits: number;
  extraCourses: string[];
  byCategory?: Record<string, CategoryProgress>;
}

export interface DegreePlanInfo {
  major: string;
  catalog_year: number;
  title: string;
}

export interface CoursePlan {
  major: string;
  catalogYear?: number;
  dept: string;
  completed: string[];
  eligible: string[];
  graph: Graph;

  // Advisor reply text
  rag_response: string;

  // Structured recommendations (weighted ranking)
  recommendations: Recommendation[];

  // Planning tips shown under the recommendations
  tips?: string[];

  // Course cards (completed + recommended) for the center panel
  flowchart: Course[];

  // Mermaid diagram
  llm_flowchart?: LlmFlowchart;

  // Unlock progression map: completed -> unlocked -> future, ETM in red
  unlockMap?: LlmFlowchart;

  // Full semester-by-semester path: completed (green) -> next term (red) ->
  // future terms (grey), an alternative view of fullPlan's card grid
  semesterFlowchart?: LlmFlowchart;

  // Authoritative student state echoed by the backend
  state?: PlannerStateInfo;

  // Deterministic planner payloads
  matched?: MatchedInfo;
  nextSemester?: NextSemester;
  fullPlan?: FullPlan;
  progress?: Progress;
}
