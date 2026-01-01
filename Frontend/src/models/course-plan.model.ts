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

export interface CoursePlan {
  flowchart: Course[];
  recommendations: Recommendation[];
}
