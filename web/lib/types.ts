/** Shapes defined by the FastAPI backend (app/models.py). */

export interface Feedback {
  summary: string;
  strengths: string[];
  gaps: string[];
  next: string[];
}

/** Question-selection reasoning, present only when the request used ?explain=1. */
export interface Trace {
  day: number;
  day_title?: string;
  tier?: string;
  pattern?: string;
  move?: string;
  reason?: string;
  is_follow_up?: boolean;
  questions_asked?: number;
  days_covered?: number[];
}

export interface InterviewResponse {
  reply: string;
  done: boolean;
  feedback?: Feedback;
  trace?: Trace;
}

export interface ChatMsg {
  id: number;
  role: "interviewer" | "candidate";
  text: string;
  trace?: Trace;
}

/** Candidate payload is free-form on the server; this mirrors candidates.json. */
export interface CandidatePayload {
  member?: {
    id?: string;
    name?: string;
    jobRole?: string;
    yearsExperience?: number;
    education?: string;
    status?: string;
  };
  missions?: Array<{
    day?: number;
    title?: string;
    passed?: boolean;
    attempts?: number;
    skipped?: boolean;
  }>;
  signals?: {
    commitDays?: number;
    missionsCompleted?: number;
    missionsFirstTry?: number;
  };
}
