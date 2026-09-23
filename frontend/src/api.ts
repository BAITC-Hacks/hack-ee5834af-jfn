import type { AuditItem, DashboardAdapter, DashboardData, ForecastPoint, ReplayRequest, RunSummary, AgentEvent } from './types';

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base.replace(/\/$/, '')}${path}`, { headers: { 'content-type': 'application/json' }, ...init });
  if (!response.ok) throw new Error(`${init?.method ?? 'GET'} ${path} failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function createApiAdapter(baseUrl: string): DashboardAdapter {
  return {
    start: (payload: ReplayRequest) => request<RunSummary>(baseUrl, '/forecast-runs', { method: 'POST', body: JSON.stringify({ as_of: payload.asOf, horizon: payload.horizon }) }),
    status: (id: string) => request<RunSummary>(baseUrl, `/forecast-runs/${id}`),
    async result(id: string) {
      const [run, forecast, audit, events] = await Promise.all([
        request<RunSummary>(baseUrl, `/forecast-runs/${id}`), request<ForecastPoint[]>(baseUrl, `/forecast-runs/${id}/forecast`),
        request<{ items: AuditItem[]; warnings?: string[]; modelVersion?: string; weatherAge?: string; scadaFreshness?: string; temporalValidation?: string }>(baseUrl, `/forecast-runs/${id}/audit`),
        request<AgentEvent[]>(baseUrl, `/forecast-runs/${id}/events`),
      ]);
      return { isDemo: false, run, forecast, events, audit: audit.items, warnings: audit.warnings ?? [], modelVersion: audit.modelVersion, weatherAge: audit.weatherAge, scadaFreshness: audit.scadaFreshness, temporalValidation: audit.temporalValidation } satisfies DashboardData;
    },
  };
}
