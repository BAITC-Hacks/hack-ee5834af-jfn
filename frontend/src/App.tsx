import { useEffect, useMemo, useRef, useState } from 'react';
import { createApiAdapter } from './api';
import { demoAdapter } from './demoFixture';
import { AgentTimeline } from './components/AgentTimeline';
import { AuditPanel } from './components/AuditPanel';
import { ForecastChart } from './components/ForecastChart';
import { ReplayControls } from './components/ReplayControls';
import type { DashboardData, ReplayRequest, SourceMode } from './types';

const api = createApiAdapter(import.meta.env.VITE_API_BASE_URL ?? '/api');
const terminal = new Set(['succeeded', 'failed']);
function pollDelay(signal: AbortSignal) { return new Promise<void>((resolve, reject) => { const timer = window.setTimeout(resolve, 900); signal.addEventListener('abort', () => { window.clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); }, { once: true }); }); }

export default function App() {
  const [source, setSource] = useState<SourceMode>('demo');
  const [request, setRequest] = useState<ReplayRequest>({ asOf: '2026-02-06T00:00:00+05:00', horizon: 48 });
  const [data, setData] = useState<DashboardData | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null); const adapter = useMemo(() => source === 'demo' ? demoAdapter : api, [source]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { void run(); /* initial reviewable fixture */ }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    controller.current?.abort(); const current = new AbortController(); controller.current = current; setBusy(true); setError(''); setData(null);
    try {
      let summary = await adapter.start(request, current.signal);
      while (!terminal.has(summary.status)) { await pollDelay(current.signal); summary = await adapter.status(summary.id, current.signal); }
      if (summary.status === 'failed') throw new Error(`Run ${summary.id} failed`);
      const result = await adapter.result(summary.id, current.signal); if (!current.signal.aborted) setData(result);
    } catch (cause) { if (!current.signal.aborted) setError(cause instanceof Error ? cause.message : 'Unable to run forecast'); }
    finally { if (!current.signal.aborted) setBusy(false); }
  }
  function changeSource(next: SourceMode) { controller.current?.abort(); setSource(next); setData(null); setError(''); setBusy(false); }
  function exportData(format: 'json' | 'csv') { if (!data) return; const content = format === 'json' ? JSON.stringify(data, null, 2) : ['target_start,turbine_1,turbine_2,is_demo', ...data.forecast.map(p => `${p.targetStart},${p.turbine1},${p.turbine2},${data.isDemo}`)].join('\n'); const url = URL.createObjectURL(new Blob([content], { type: format === 'json' ? 'application/json' : 'text/csv' })); const a = document.createElement('a'); a.href = url; a.download = `${data.run.id}.${format}`; a.click(); URL.revokeObjectURL(url); }
  const cards = [{ label: 'Model version', value: data?.modelVersion }, { label: 'Weather age', value: data?.weatherAge }, { label: 'SCADA freshness', value: data?.scadaFreshness }, { label: 'Temporal validation', value: data?.temporalValidation }];
  const stateLabel = busy ? 'running' : error ? 'error' : data ? data.run.status : 'idle';
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="mark">W</span><div><strong>Wind Operator</strong><span>Operations</span></div></div>
      <nav aria-label="Primary navigation">
        <a className="active" href="#dashboard"><span>⌁</span>Dashboard</a>
        <a href="#replay"><span>↻</span>Replay</a>
        <a href="#runs"><span>▤</span>Runs</a>
        <a href="#models"><span>◇</span>Models</a>
        <a href="#audit"><span>✓</span>Audit log</a>
      </nav>
      <div className="sidebar-bottom"><div><span className="status-light" />System available</div><small>Asia/Almaty · UTC+5</small></div>
    </aside>
    <main className="workspace" id="dashboard">
      <header className="topbar"><div><h1>Dashboard</h1><p>Forecast replay and operational trace</p></div><div className={`statuses ${error ? 'has-error' : ''}`}><span><i />Backend · {source === 'demo' ? 'local' : stateLabel}</span><span><i />Agent · {source === 'demo' ? 'sample' : stateLabel}</span></div></header>
      <div className="dashboard-content">
        <ReplayControls request={request} source={source} busy={busy} onRequest={setRequest} onSource={changeSource} onRun={() => void run()} />
        {source === 'demo' && <div className="demo-banner"><strong>Sample data</strong><span>Displayed values are for interface preview only.</span></div>}
        {error && <div className="error-state" role="alert"><div><strong>Forecast could not be loaded</strong><span>{error}. No sample data was substituted.</span></div><button onClick={() => void run()}>Retry</button></div>}
        <section className="metric-grid" aria-label="Run provenance summary">{cards.map(card => <article className="metric" key={card.label}><span>{card.label}</span><strong>{busy ? 'Loading…' : card.value ?? 'Unavailable'}</strong></article>)}</section>
        {!data && !busy && !error && <div className="empty-state">Choose a source and run a forecast to inspect its evidence.</div>}
        {data && <><div className="main-grid"><ForecastChart points={data.forecast} isDemo={data.isDemo} /><AgentTimeline events={data.events} /></div><AuditPanel data={data} onExport={exportData} /></>}
      </div>
    </main>
  </div>;
}
