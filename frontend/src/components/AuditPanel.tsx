import type { DashboardData } from '../types';
import type { Copy } from '../i18n';

interface Props { data: DashboardData; copy: Copy; onExport: (format: 'json' | 'csv') => void; }
export function AuditPanel({ data, copy, onExport }: Props) {
  return <section className="card audit-card" aria-labelledby="audit-title"><div className="section-heading"><div><h2 id="audit-title">{copy.runDetails}</h2><p>{copy.detailsSubtitle}</p></div><div className="export-actions"><button onClick={() => onExport('json')}>JSON</button><button onClick={() => onExport('csv')}>CSV</button></div></div>
    <dl className="audit-grid">{data.audit.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value ?? copy.unavailable}</dd></div>)}</dl>
    <div className="audit-footer"><div><h3>{copy.warnings}</h3><ul>{data.warnings.map(w => <li key={w}>{w}</li>)}</ul></div><div className="revision"><span>{copy.revisionHistory}</span><strong>{data.run.id}</strong><small>{data.run.parentRunId ? `${copy.parent}: ${data.run.parentRunId}` : copy.firstRevision}</small></div></div>
  </section>;
}
