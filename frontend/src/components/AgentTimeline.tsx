import type { AgentEvent } from '../types';
import type { Copy } from '../i18n';

export function AgentTimeline({ events, copy }: { events: AgentEvent[]; copy: Copy }) {
  return <section className="card timeline-card" aria-labelledby="timeline-title"><div className="section-heading"><div><h2 id="timeline-title">{copy.agentActivity}</h2><p>{copy.toolsUsed}</p></div><span className="live-dot">{copy.complete}</span></div>
    <ol className="timeline">{events.map((event, index) => <li key={event.id} className={event.status}><span className="timeline-node">{index + 1}</span><div><div className="event-row"><code>{event.tool}</code><time>{event.time}</time></div><p>{event.detail}</p><span className="event-status">{event.status === 'done' ? copy.done : copy.warning}</span></div></li>)}</ol>
  </section>;
}
