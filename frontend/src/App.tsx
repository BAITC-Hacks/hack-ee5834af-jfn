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

export default function App() {
  const [source, setSource] = useState<SourceMode>('demo');
  const [request, setRequest] = useState<ReplayRequest>({ asOf: '2026-02-06T00:00:00+05:00', horizon: 48 });
  const [data, setData] = useState<DashboardData | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const cancelled = useRef(false); const adapter = useMemo(() => source === 'demo' ? demoAdapter : api, [source]);
  useEffect(() => { cancelled.current = false; return () => { cancelled.current = true; }; }, [source]);
  useEffect(() => { void run(); /* initial reviewable fixture */ }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    cancelled.current = false; setBusy(true); setError(''); setData(null);
    try {
      let summary = await adapter.start(request);
      while (!terminal.has(summary.status)) { await new Promise(r => window.setTimeout(r, 900)); if (cancelled.current) return; summary = await adapter.status(summary.id); }
      if (summary.status === 'failed') throw new Error(`Run ${summary.id} failed`);
      const result = await adapter.result(summary.id); if (!cancelled.current) setData(result);
    } catch (cause) { if (!cancelled.current) setError(cause instanceof Error ? cause.message : 'Unable to run forecast'); }
    finally { if (!cancelled.current) setBusy(false); }
  }
  function changeSource(next: SourceMode) { cancelled.current = true; setSource(next); setData(null); setError(''); setBusy(false); }
  function exportData(format: 'json' | 'csv') { if (!data) return; const content = format === 'json' ? JSON.stringify(data, null, 2) : ['target_start,turbine_1,turbine_2,is_demo', ...data.forecast.map(p => `${p.targetStart},${p.turbine1},${p.turbine2},${data.isDemo}`)].join('\n'); const url = URL.createObjectURL(new Blob([content], { type: format === 'json' ? 'application/json' : 'text/csv' })); const a = document.createElement('a'); a.href = url; a.download = `${data.run.id}.${format}`; a.click(); URL.revokeObjectURL(url); }
  const cards = [{ label: 'Model version', value: data?.modelVersion }, { label: 'Weather age', value: data?.weatherAge }, { label: 'SCADA freshness', value: data?.scadaFreshness }, { label: 'Temporal validation', value: data?.temporalValidation }];
  return <main className="page"><header className="topbar"><div className="brand"><span className="mark">W</span><div><strong>Wind Operator</strong><span>Forecast replay console</span></div></div><div className="statuses"><span><i />Backend {source === 'demo' ? 'fixture' : 'API'}</span><span><i />Agent {source === 'demo' ? 'fixture' : 'status pending'}</span>{source === 'demo' && <b>DEMO DATA · NOT LIVE</b>}</div></header>
    <div className="intro"><div><span className="eyebrow">Autonomous wind operations</span><h1>Forecast evidence,<br />from origin to output.</h1></div><p>Run a point-in-time replay, compare both turbines, and inspect every tool and input used to publish it.</p></div>
    <ReplayControls request={request} source={source} busy={busy} onRequest={setRequest} onSource={changeSource} onRun={() => void run()} />
    {source === 'demo' && <div className="demo-banner"><strong>Illustrative demo fixture</strong><span>These values demonstrate the interface and are not measured data or an operational prediction.</span></div>}
    {error && <div className="error-state" role="alert"><div><strong>Forecast could not be loaded</strong><span>{error}. No demo data has been substituted.</span></div><button onClick={() => void run()}>Retry</button></div>}
    <section className="metric-grid" aria-label="Run provenance summary">{cards.map((card, index) => <article className="metric" key={card.label}><div><span>{card.label}</span><small>0{index + 1}</small></div><strong>{busy ? 'Loading…' : card.value ?? 'Unavailable'}</strong></article>)}</section>
    {!data && !busy && !error && <div className="empty-state">Choose a source and run a forecast to inspect its evidence.</div>}
    {data && <><div className="main-grid"><ForecastChart points={data.forecast} /><AgentTimeline events={data.events} /></div><AuditPanel data={data} onExport={exportData} /></>}
    <footer><span>Wind Operator · point-in-time replay</span><span>{data?.run.id ?? 'No active run'}</span></footer>
  </main>;
}
