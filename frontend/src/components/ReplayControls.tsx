import type { ReplayRequest, SourceMode } from '../types';

interface Props { request: ReplayRequest; source: SourceMode; busy: boolean; onRequest: (value: ReplayRequest) => void; onSource: (value: SourceMode) => void; onRun: () => void; }

export function ReplayControls({ request, source, busy, onRequest, onSource, onRun }: Props) {
  const local = request.asOf.slice(0, 16);
  return <section className="card replay-card" aria-labelledby="replay-title">
    <div className="section-heading"><div><h2 id="replay-title">New replay</h2><p>Choose the forecast origin and horizon.</p></div><span className="timezone">Almaty · UTC+5</span></div>
    <div className="controls">
      <label><span>Replay date & time</span><input type="datetime-local" value={local} onChange={e => onRequest({ ...request, asOf: `${e.target.value}:00+05:00` })} /></label>
      <fieldset><legend>Horizon</legend><div className="segments">{([24, 48] as const).map(h => <button type="button" className={request.horizon === h ? 'active' : ''} aria-pressed={request.horizon === h} onClick={() => onRequest({ ...request, horizon: h })} key={h}>{h}h</button>)}</div></fieldset>
      <label><span>Data source</span><select value={source} onChange={e => onSource(e.target.value as SourceMode)}><option value="demo">Sample</option><option value="api">API</option></select></label>
      <button className="primary" onClick={onRun} disabled={busy}>{busy ? <><span className="spinner" /> Running…</> : 'Run forecast'}<span aria-hidden="true">↗</span></button>
    </div>
  </section>;
}
