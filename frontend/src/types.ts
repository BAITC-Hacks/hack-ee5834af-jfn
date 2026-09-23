export type SourceMode = 'demo' | 'api';
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export interface ReplayRequest { asOf: string; horizon: 24 | 48; }
export interface RunSummary { id: string; status: RunStatus; parentRunId?: string; }
export interface ForecastPoint { targetStart: string; turbine1: number; turbine2: number; }
export interface AuditItem { label: string; value?: string; }
export interface AgentEvent { id: string; time: string; tool: string; detail: string; status: 'done' | 'warning' | 'failed'; }
export interface DashboardData {
  isDemo: boolean; run: RunSummary; modelVersion?: string; weatherAge?: string; scadaFreshness?: string;
  temporalValidation?: string; forecast: ForecastPoint[]; audit: AuditItem[]; warnings: string[]; events: AgentEvent[];
}
export interface DashboardAdapter {
  start(request: ReplayRequest): Promise<RunSummary>;
  status(runId: string): Promise<RunSummary>;
  result(runId: string): Promise<DashboardData>;
}
