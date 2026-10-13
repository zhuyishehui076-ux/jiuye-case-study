import axios from "axios";

// 同源相对路径，nginx 反代 /api 到后端；无任何外部 CDN/端点硬编码。
export const api = axios.create({ baseURL: "/api/v1" });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export interface Me {
  username: string;
  display_name: string;
  role: string;
  college_id: number | null;
  show_demo_badge: boolean;
  profile: string;
}

export async function login(username: string, password: string): Promise<string> {
  const form = new URLSearchParams();
  form.set("username", username);
  form.set("password", password);
  const { data } = await api.post("/auth/login", form);
  return data.access_token as string;
}

export async function fetchMe(): Promise<Me> {
  const { data } = await api.get<Me>("/auth/me");
  return data;
}

// ---- M1 驾驶舱（只读物化视图，SPEC §6.1）----
export interface DashboardSummary {
  total_students: number;
  settled_count: number;
  unsettled_count: number;
  settlement_rate: number;
  red_count: number;
  yellow_count: number;
  green_count: number;
  signed_count: number;
  further_study_count: number;
  abroad_count: number;
  military_count: number;
  flexible_count: number;
  not_seeking_count: number;
  total_jobs: number;
  supply_ratio: number;
  recent_activities: number;
  refreshed_at: string;
}
export interface CollegeRow {
  college_id: number;
  college_name: string;
  total: number;
  settled: number;
  settlement_rate: number;
  red: number;
  yellow: number;
  green: number;
  light: "red" | "yellow" | "green";
}
export interface TrendPoint {
  month: string;
  signings: number;
  actions: number;
}
export interface AlertRow {
  student_id: number;
  student_no: string;
  name: string;
  college_name: string;
  risk_level: "red" | "yellow";
  risk_reason: string | null;
  status: string;
}

export const fetchSummary = async () =>
  (await api.get<DashboardSummary>("/dashboard/summary")).data;
export const fetchColleges = async () =>
  (await api.get<CollegeRow[]>("/dashboard/colleges")).data;
export const fetchTrend = async () =>
  (await api.get<TrendPoint[]>("/dashboard/trend")).data;
export const fetchAlerts = async () =>
  (await api.get<AlertRow[]>("/dashboard/alerts")).data;

// ---- T4 岗位解析与智能匹配（SPEC §6.4）----
export interface JobRow {
  id: number;
  company: string;
  title: string;
  source: string;
  raw_text: string;
}
export interface ReviewQueueItem {
  parse_id: number;
  job_id: number;
  company: string;
  title: string;
  review_reason: string | null;
  parse_confidence: number | null;
}
export interface JobParseRow {
  id: number;
  job_id: number;
  degree_req: string | null;
  major_codes: string[];
  direction_keywords: string[];
  location: string | null;
  parse_confidence: number | null;
  needs_review: boolean;
  review_reason: string | null;
}
export interface MatchRow {
  id: number;
  job_id: number;
  student_id: number;
  student_no: string;
  student_name: string;
  college_id: number;
  major_id: number;
  score: number;
  reason_text: string;
  pushed_at: string | null;
}

export const pasteJob = async (payload: {
  company: string;
  title: string;
  raw_text: string;
}) => (await api.post<JobRow>("/jobs/paste", payload)).data;

export const importJobsXlsx = async (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return (await api.post<JobRow[]>("/jobs/import-xlsx", form)).data;
};

export const parseJob = async (jobId: number) =>
  (await api.post<JobParseRow>(`/jobs/${jobId}/parse`)).data;

export const fetchReviewQueue = async () =>
  (await api.get<ReviewQueueItem[]>("/jobs/review-queue")).data;

export const reviewJobParse = async (
  parseId: number,
  payload: Partial<{
    degree_req: string | null;
    major_codes: string[];
    direction_keywords: string[];
    location: string | null;
    needs_review: boolean;
    review_reason: string | null;
  }>,
) => (await api.put<JobParseRow>(`/jobs/parses/${parseId}/review`, payload)).data;

export const calculateMatches = async (jobId: number) =>
  (await api.post<MatchRow[]>(`/jobs/${jobId}/match`)).data;

export const fetchMatches = async (jobId: number) =>
  (await api.get<MatchRow[]>(`/jobs/${jobId}/matches`)).data;

export const pushMatches = async (jobId: number) =>
  (await api.post<{ pushed: number }>(`/jobs/${jobId}/push`)).data;

export const exportMatches = async (jobId: number) =>
  (await api.get(`/jobs/${jobId}/export`, { responseType: "blob" })).data as Blob;

// ---- T5 学生画像（SPEC §6.3）----
export interface ProfileListItem {
  student_id: number;
  student_no: string;
  name: string;
  college_id: number;
  college_name: string;
  major_name: string;
  risk_level: string;
  status: string;
  latest_term: string | null;
  score_avg: number | null;
}

export interface ProfileEvidenceSource {
  id: string;
  type: string;
  summary: string;
  object_id?: number | null;
  value?: unknown;
}

export interface ProfileEvidenceClaim {
  id: string;
  dimension: string;
  text: string;
  evidence_ids: string[];
}

export interface ProfileEvidence {
  sources: ProfileEvidenceSource[];
  claims: ProfileEvidenceClaim[];
  suggestions: string[];
}

export interface ProfileDetail {
  profile_id: number;
  student_id: number;
  student_no: string;
  name: string;
  college_id: number;
  college_name: string;
  major_name: string;
  term: string;
  terms: string[];
  six_dims: Record<string, number>;
  narrative_text: string;
  evidence: ProfileEvidence;
  llm_meta: Record<string, unknown>;
  updated_at: string;
}

export const rebuildProfiles = async (term: string) =>
  (await api.post<{ term: string; rebuilt: number; narrative_mode: string }>(
    `/profiles/rebuild?term=${encodeURIComponent(term)}`,
  )).data;

export const fetchProfileStudents = async () =>
  (await api.get<ProfileListItem[]>("/profiles/students")).data;

export const fetchProfileDetail = async (studentId: number, term?: string) =>
  (await api.get<ProfileDetail>(
    `/profiles/students/${studentId}${term ? `?term=${encodeURIComponent(term)}` : ""}`,
  )).data;

// ---- T6 一生一策帮扶台账（SPEC §6.2）----
export interface AssistCaseRow {
  id: number;
  student_id: number;
  student_no: string;
  student_name: string;
  college_id: number;
  college_name: string;
  major_id: number;
  risk_reason: string | null;
  opened_reason: string | null;
  assigned_to: number | null;
  status: string;
  opened_at: string;
  record_count: number;
  action_summary: string;
}

export interface AssistRecordRow {
  id: number;
  case_id: number;
  action_type: string;
  note: string | null;
  actor: string | null;
  occurred_at: string;
}

export const fetchAssistCases = async () =>
  (await api.get<AssistCaseRow[]>("/assist/cases")).data;

export const appendAssistRecord = async (
  caseId: number,
  payload: { action_type: string; note?: string | null },
) => (await api.post<AssistRecordRow>(`/assist/cases/${caseId}/records`, payload)).data;

export const exportAssistXlsx = async () =>
  (await api.get("/assist/export.xlsx", { responseType: "blob" })).data as Blob;

export const exportAssistDocx = async () =>
  (await api.get("/assist/export.docx", { responseType: "blob" })).data as Blob;
