import type { DashboardData } from '../types';

interface Props { data: DashboardData; onExport: (format: 'json' | 'csv') => void; }
export function AuditPanel({ data, onExport }: Props) {
  return <section className="card audit-card" aria-labelledby="audit-title"><div className="section-heading"><div><span className="eyebrow">Evidence & provenance</span><h2 id="audit-title">Run audit</h2></div><div className="export-actions"><button onClick={() => onExport('json')}>Export JSON</button><button onClick={() => onExport('csv')}>Export CSV</button></div></div>
    <div className="audit-grid">{data.audit.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value ?? 'Unavailable'}</dd></div>)}</div>
    <div className="audit-footer"><div><h3>Warnings</h3><ul>{data.warnings.map(w => <li key={w}>{w}</li>)}</ul></div><div className="revision"><span>Revision history</span><strong>{data.run.id}</strong><small>{data.run.parentRunId ? `Parent: ${data.run.parentRunId}` : 'First revision'}</small></div></div>
  </section>;
}
