import type { DashboardData } from '../types';

interface Props { data: DashboardData; onExport: (format: 'json' | 'csv') => void; }
export function AuditPanel({ data, onExport }: Props) {
  return <section className="card audit-card" aria-labelledby="audit-title"><div className="section-heading"><div><h2 id="audit-title">Run details</h2><p>Inputs, validation, and revision history</p></div><div className="export-actions"><button onClick={() => onExport('json')}>JSON</button><button onClick={() => onExport('csv')}>CSV</button></div></div>
    <dl className="audit-grid">{data.audit.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value ?? 'Unavailable'}</dd></div>)}</dl>
    <div className="audit-footer"><div><h3>Warnings</h3><ul>{data.warnings.map(w => <li key={w}>{w}</li>)}</ul></div><div className="revision"><span>Revision history</span><strong>{data.run.id}</strong><small>{data.run.parentRunId ? `Parent: ${data.run.parentRunId}` : 'First revision'}</small></div></div>
  </section>;
}
