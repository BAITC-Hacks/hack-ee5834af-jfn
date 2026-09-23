import type { ReplayRequest, SourceMode } from '../types';
import type { Copy } from '../i18n';

interface Props { request: ReplayRequest; source: SourceMode; busy: boolean; copy: Copy; onRequest: (value: ReplayRequest) => void; onSource: (value: SourceMode) => void; onRun: () => void; }

export function ReplayControls({ request, source, busy, copy, onRequest, onSource, onRun }: Props) {
  const local = request.asOf.slice(0, 16);
  return <section className="card replay-card" aria-labelledby="replay-title">
    <div className="section-heading"><div><h2 id="replay-title">{copy.newReplay}</h2><p>{copy.chooseWindow}</p></div></div>
    <div className="controls">
      <label><span>{copy.replayDate}</span><input type="datetime-local" value={local} onChange={e => onRequest({ ...request, asOf: `${e.target.value}:00+05:00` })} /></label>
      <fieldset><legend>{copy.horizon}</legend><div className="segments">{([24, 48] as const).map(h => <button type="button" className={request.horizon === h ? 'active' : ''} aria-pressed={request.horizon === h} onClick={() => onRequest({ ...request, horizon: h })} key={h}>{h} {copy.hours === 'hours' ? 'h' : 'сағ'}</button>)}</div></fieldset>
      <label><span>{copy.dataSource}</span><select value={source} onChange={e => onSource(e.target.value as SourceMode)}><option value="demo">{copy.sampleData}</option><option value="api">{copy.api}</option></select></label>
      <button className="primary" onClick={onRun} disabled={busy}>{busy ? <><span className="spinner" /> {copy.runningLabel}</> : copy.runForecast}<span aria-hidden="true">↗</span></button>
    </div>
  </section>;
}
