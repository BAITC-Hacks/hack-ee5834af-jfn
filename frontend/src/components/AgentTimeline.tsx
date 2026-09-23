import type { AgentEvent } from '../types';

export function AgentTimeline({ events }: { events: AgentEvent[] }) {
  return <section className="card timeline-card" aria-labelledby="timeline-title"><div className="section-heading"><div><span className="eyebrow">Tool trace</span><h2 id="timeline-title">Agent timeline</h2></div><span className="live-dot">Recorded</span></div>
    <ol className="timeline">{events.map((event, index) => <li key={event.id} className={event.status}><span className="timeline-node">{index + 1}</span><div><div className="event-row"><code>{event.tool}</code><time>{event.time}</time></div><p>{event.detail}</p><span className="event-status">{event.status}</span></div></li>)}</ol>
  </section>;
}
