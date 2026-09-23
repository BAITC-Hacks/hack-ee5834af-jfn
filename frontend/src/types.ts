export type SourceMode = 'demo' | 'api';
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export interface ReplayRequest { asOf: string; horizon: 24 | 48; }
export interface RunSummary { id: string; status: RunStatus; parentRunId?: string; error?: string; }
export interface ForecastPoint { targetStart: string; turbine1: number; turbine2: number; }
export interface AuditItem { label: string; value?: string; }
export interface AgentEvent { id: string; time: string; tool: string; detail: string; status: 'done' | 'warning' | 'failed'; }
export interface DashboardData {
  isDemo: boolean; run: RunSummary; modelVersion?: string; weatherAge?: string; scadaFreshness?: string;
  temporalValidation?: string; forecast: ForecastPoint[]; audit: AuditItem[]; warnings: string[]; events: AgentEvent[];
}
export interface DashboardAdapter {
  start(request: ReplayRequest, signal?: AbortSignal): Promise<RunSummary>;
  status(runId: string, signal?: AbortSignal): Promise<RunSummary>;
  events?(runId: string, signal?: AbortSignal): Promise<AgentEvent[]>;
  result(runId: string, signal?: AbortSignal): Promise<DashboardData>;
}
