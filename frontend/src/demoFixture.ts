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
    run: { id: `demo-fixture-${request.horizon}h`, status: 'succeeded', parentRunId: 'demo-fixture-previous' },
    modelVersion: 'Illustrative model fixture', weatherAge: 'Illustrative · 3h', scadaFreshness: 'Unavailable in demo', temporalValidation: 'DEMO · assumptions',
    forecast,
    audit: [
      { label: 'Replay as of', value: origin.toISOString() }, { label: 'Weather init', value: new Date(origin.getTime() - 3 * 3_600_000).toISOString() },
      { label: 'Weather available at', value: new Date(origin.getTime() - 2.5 * 3_600_000).toISOString() }, { label: 'SCADA cutoff', value: undefined },
      { label: 'Snapshot ID', value: 'demo-weather-snapshot' }, { label: 'Input hash', value: 'demo:8f2b…41c9' },
    ],
    warnings: ['Illustrative fixture only — values are not an operational forecast.', 'Fresh SCADA is unavailable in this fixture.'],
    events: [
      { id: '1', time: '00:00', tool: 'create_run_context', detail: 'Locked replay origin and 48h policy', status: 'done' },
      { id: '2', time: '00:01', tool: 'resolve_weather', detail: 'Selected a demo weather snapshot', status: 'done' },
      { id: '3', time: '00:02', tool: 'validate_inputs', detail: 'SCADA freshness unavailable', status: 'warning' },
      { id: '4', time: '00:03', tool: 'publish_forecast', detail: 'Published illustrative fixture', status: 'done' },
    ],
  };
}

export const demoAdapter: DashboardAdapter = {
  async start(request) { return createDemoData(request).run; },
  async status(runId) { return { id: runId, status: 'succeeded' }; },
  async result(runId) { const horizon = runId.includes('24h') ? 24 : 48; return createDemoData({ asOf: '2026-02-06T00:00:00+05:00', horizon }); },
};
