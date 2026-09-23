import type { DashboardAdapter, DashboardData, ReplayRequest } from './types';

const curve = (hour: number, offset: number) => Math.min(.86, Math.max(.08, .42 + Math.sin((hour + offset) / 4.2) * .24 + Math.cos(hour / 8) * .08));

export function createDemoData(request: ReplayRequest): DashboardData {
  const origin = new Date(request.asOf);
  const forecast = Array.from({ length: request.horizon }, (_, hour) => ({
    targetStart: new Date(origin.getTime() + (hour + 1) * 3_600_000).toISOString(),
    turbine1: Number(curve(hour, 0).toFixed(3)), turbine2: Number(curve(hour, 1.3).toFixed(3)),
  }));
  return {
    isDemo: true,
    run: { id: `sample-run-${request.horizon}h`, status: 'succeeded', parentRunId: 'sample-run-previous' },
    modelVersion: 'Preview model', weatherAge: '3 hours', scadaFreshness: 'Not provided', temporalValidation: 'Assumptions',
    forecast,
    audit: [
      { label: 'Replay as of', value: origin.toISOString() }, { label: 'Weather init', value: new Date(origin.getTime() - 3 * 3_600_000).toISOString() },
      { label: 'Weather available at', value: new Date(origin.getTime() - 2.5 * 3_600_000).toISOString() }, { label: 'SCADA cutoff', value: undefined },
      { label: 'Snapshot ID', value: 'sample-weather-snapshot' }, { label: 'Input hash', value: 'sample:8f2b…41c9' },
    ],
    warnings: ['Sample values are not an operational forecast.', 'Fresh SCADA is not provided in this preview.'],
    events: [
      { id: '1', time: '00:00', tool: 'create_run_context', detail: `Locked replay origin and ${request.horizon}h policy`, status: 'done' },
      { id: '2', time: '00:01', tool: 'resolve_weather', detail: 'Selected a sample weather snapshot', status: 'done' },
      { id: '3', time: '00:02', tool: 'validate_inputs', detail: 'SCADA freshness unavailable', status: 'warning' },
      { id: '4', time: '00:03', tool: 'publish_forecast', detail: 'Published sample output', status: 'done' },
    ],
  };
}

const demoRequests = new Map<string, ReplayRequest>();
export const demoAdapter: DashboardAdapter = {
  async start(request) { const run = createDemoData(request).run; demoRequests.set(run.id, request); return run; },
  async status(runId) { return { id: runId, status: 'succeeded' }; },
  async result(runId) { const saved = demoRequests.get(runId); if (!saved) throw new Error(`Unknown demo run ${runId}`); return createDemoData(saved); },
};
