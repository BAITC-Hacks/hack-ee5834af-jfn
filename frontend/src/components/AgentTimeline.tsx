import type { AgentEvent } from '../types';

export function AgentTimeline({ events }: { events: AgentEvent[] }) {
  return <section className="card timeline-card" aria-labelledby="timeline-title"><div className="section-heading"><div><h2 id="timeline-title">Agent activity</h2><p>Tools used for this run</p></div><span className="live-dot">Complete</span></div>
    <ol className="timeline">{events.map((event, index) => <li key={event.id} className={event.status}><span className="timeline-node">{index + 1}</span><div><div className="event-row"><code>{event.tool}</code><time>{event.time}</time></div><p>{event.detail}</p><span className="event-status">{event.status}</span></div></li>)}</ol>
  </section>;
}
