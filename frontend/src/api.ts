import type { AuditItem, DashboardAdapter, DashboardData, ForecastPoint, ReplayRequest, RunSummary, AgentEvent } from './types';

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base.replace(/\/$/, '')}${path}`, { headers: { 'content-type': 'application/json' }, ...init });
  if (!response.ok) throw new Error(`${init?.method ?? 'GET'} ${path} failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function createApiAdapter(baseUrl: string): DashboardAdapter {
  return {
    start: (payload: ReplayRequest, signal?: AbortSignal) => request<RunSummary>(baseUrl, '/forecast-runs', { method: 'POST', signal, body: JSON.stringify({ as_of: payload.asOf, horizon: payload.horizon }) }),
    status: (id: string, signal?: AbortSignal) => request<RunSummary>(baseUrl, `/forecast-runs/${id}`, { signal }),
    async result(id: string, signal?: AbortSignal) {
      const [run, forecast, audit, events] = await Promise.all([
        request<RunSummary>(baseUrl, `/forecast-runs/${id}`, { signal }), request<ForecastPoint[]>(baseUrl, `/forecast-runs/${id}/forecast`, { signal }),
        request<{ items: AuditItem[]; warnings?: string[]; modelVersion?: string; weatherAge?: string; scadaFreshness?: string; temporalValidation?: string }>(baseUrl, `/forecast-runs/${id}/audit`, { signal }),
        request<AgentEvent[]>(baseUrl, `/forecast-runs/${id}/events`, { signal }),
      ]);
      return { isDemo: false, run, forecast, events, audit: audit.items, warnings: audit.warnings ?? [], modelVersion: audit.modelVersion, weatherAge: audit.weatherAge, scadaFreshness: audit.scadaFreshness, temporalValidation: audit.temporalValidation } satisfies DashboardData;
    },
  };
}
