import type { CandidatePayload } from "./types";

/**
 * Curated demo candidates, copied verbatim from candidates.json so the payloads
 * match what the backend's cohort-signal engine expects. Each one is chosen to
 * make the adaptive question selection visibly different in a live demo.
 */
export interface SampleCandidate {
  id: string;
  label: string;
  blurb: string;
  payload: CandidatePayload;
}

export const SAMPLE_CANDIDATES: SampleCandidate[] = [
  {
    id: "CAND-003",
    label: "Emily Chen - strong performer",
    blurb: "AI Engineer, 6 yrs. Everything first try, 31-day commit streak.",
    payload: {
      member: {
        id: "CAND-003",
        name: "Emily Chen",
        jobRole: "AI Engineer",
        yearsExperience: 6,
        education: "MS Artificial Intelligence",
        status: "COMPLETED",
      },
      missions: [
        { day: 7, title: "Embeddings Explained", passed: true, attempts: 1 },
        { day: 8, title: "Vector Databases Overview", passed: true, attempts: 1 },
        { day: 10, title: "Retrieval & Matching Engine", passed: true, attempts: 1 },
        { day: 11, title: "RAG End-to-End & LLM API Basics", passed: true, attempts: 1 },
        { day: 12, title: "Prompt Engineering Fundamentals", passed: true, attempts: 1 },
        { day: 13, title: "Function Calling & Structured Outputs", passed: true, attempts: 1 },
        { day: 21, title: "LangChain Agents", passed: true, attempts: 1 },
        { day: 22, title: "Multi-Agent Orchestration", passed: true, attempts: 1 },
        { day: 23, title: "Model Context Protocol (MCP)", passed: true, attempts: 1 },
        { day: 31, title: "Capstone Project & Final Demo", passed: true, attempts: 1 },
      ],
      signals: { commitDays: 31, missionsCompleted: 31, missionsFirstTry: 30 },
    },
  },
  {
    id: "CAND-010",
    label: "Gerald Combs - struggling learner",
    blurb: "IT Support, 20 yrs. Three failed missions, core days skipped.",
    payload: {
      member: {
        id: "CAND-010",
        name: "Gerald Combs",
        jobRole: "IT Support Specialist",
        yearsExperience: 20,
        education: "AAS Information Technology",
        status: "COMPLETED",
      },
      missions: [
        { day: 1, title: "VS Code & Python Environment Setup", passed: true, attempts: 2 },
        { day: 7, title: "Embeddings Explained", passed: true, attempts: 5 },
        { day: 8, title: "Vector Databases Overview", passed: false, attempts: 4 },
        { day: 10, title: "Retrieval & Matching Engine", passed: false, attempts: 3 },
        { day: 12, title: "Prompt Engineering Fundamentals", passed: true, attempts: 5 },
        { day: 16, title: "Chatbot Backend & API Integration", passed: true, attempts: 4 },
        { day: 22, title: "Multi-Agent Orchestration", passed: false, attempts: 3 },
        { day: 27, title: "Security, Privacy & Guardrails", skipped: true },
        { day: 28, title: "Docker & Kubernetes Deployment", skipped: true },
        { day: 31, title: "Capstone Project & Final Demo", passed: true, attempts: 3 },
      ],
      signals: { commitDays: 22, missionsCompleted: 23, missionsFirstTry: 1 },
    },
  },
  {
    id: "CAND-008",
    label: "Harold Whitfield - veteran, new stack",
    blurb: "Distinguished Engineer, 28 yrs. Skipped fine-tuning, fought the agent stack.",
    payload: {
      member: {
        id: "CAND-008",
        name: "Harold Whitfield",
        jobRole: "Distinguished Engineer",
        yearsExperience: 28,
        education: "BS Computer Science",
        status: "COMPLETED",
      },
      missions: [
        { day: 1, title: "VS Code & Python Environment Setup", passed: true, attempts: 1 },
        { day: 4, title: "Reading & Processing Structured Data", passed: true, attempts: 1 },
        { day: 5, title: "Reading & Processing Unstructured Data", passed: true, attempts: 1 },
        { day: 14, title: "Fine-Tuning: Concepts & When to Use It", skipped: true },
        { day: 15, title: "Fine-Tuning: Hands-On with LoRA & QLoRA", skipped: true },
        { day: 21, title: "LangChain Agents", passed: true, attempts: 5 },
        { day: 22, title: "Multi-Agent Orchestration", passed: true, attempts: 4 },
        { day: 23, title: "Model Context Protocol (MCP)", passed: true, attempts: 5 },
        { day: 27, title: "Security, Privacy & Guardrails", passed: true, attempts: 1 },
        { day: 28, title: "Docker & Kubernetes Deployment", passed: true, attempts: 1 },
        { day: 31, title: "Capstone Project & Final Demo", passed: true, attempts: 2 },
      ],
      signals: { commitDays: 25, missionsCompleted: 27, missionsFirstTry: 15 },
    },
  },
  {
    id: "CAND-017",
    label: "Tyler Brooks - persistent junior",
    blurb: "Bootcamp grad. Passed everything, almost nothing first try.",
    payload: {
      member: {
        id: "CAND-017",
        name: "Tyler Brooks",
        jobRole: "Junior Developer",
        yearsExperience: 0,
        education: "GED + Coding Bootcamp Certificate",
        status: "COMPLETED",
      },
      missions: [
        { day: 1, title: "VS Code & Python Environment Setup", passed: true, attempts: 3 },
        { day: 3, title: "First AI Project, React Frontend & GitHub", passed: true, attempts: 5 },
        { day: 7, title: "Embeddings Explained", passed: true, attempts: 5 },
        { day: 8, title: "Vector Databases Overview", passed: true, attempts: 5 },
        { day: 10, title: "Retrieval & Matching Engine", passed: true, attempts: 5 },
        { day: 12, title: "Prompt Engineering Fundamentals", passed: true, attempts: 5 },
        { day: 16, title: "Chatbot Backend & API Integration", passed: true, attempts: 4 },
        { day: 22, title: "Multi-Agent Orchestration", passed: true, attempts: 5 },
        { day: 28, title: "Docker & Kubernetes Deployment", passed: true, attempts: 4 },
        { day: 31, title: "Capstone Project & Final Demo", passed: true, attempts: 3 },
      ],
      signals: { commitDays: 30, missionsCompleted: 31, missionsFirstTry: 1 },
    },
  },
  {
    id: "CAND-011",
    label: "Mia Alvarez - skipped the core",
    blurb: "UX Researcher, 6 yrs. Strong start, then skipped embeddings through agents.",
    payload: {
      member: {
        id: "CAND-011",
        name: "Mia Alvarez",
        jobRole: "UX Researcher",
        yearsExperience: 6,
        education: "MA Human-Computer Interaction",
        status: "COMPLETED",
      },
      missions: [
        { day: 1, title: "VS Code & Python Environment Setup", passed: true, attempts: 2 },
        { day: 2, title: "Local LLM & AI Coding Assistant Setup", passed: true, attempts: 1 },
        { day: 3, title: "First AI Project, React Frontend & GitHub", passed: true, attempts: 3 },
        { day: 4, title: "Reading & Processing Structured Data", passed: true, attempts: 2 },
        { day: 7, title: "Embeddings Explained", skipped: true },
        { day: 8, title: "Vector Databases Overview", skipped: true },
        { day: 12, title: "Prompt Engineering Fundamentals", skipped: true },
        { day: 16, title: "Chatbot Backend & API Integration", skipped: true },
        { day: 22, title: "Multi-Agent Orchestration", skipped: true },
        { day: 31, title: "Capstone Project & Final Demo", passed: true, attempts: 4 },
      ],
      signals: { commitDays: 9, missionsCompleted: 14, missionsFirstTry: 5 },
    },
  },
];
