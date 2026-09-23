import type { AgentEvent } from '../types';
import type { Copy } from '../i18n';
type Payload = { tool?: string; execution?: string };
const titles: Record<string, string> = { get_weather_forecast: 'Weather source selected', validate_inputs: 'Inputs validated', run_forecast: 'GFS LightGBM numerical forecast', validate_forecast: 'Forecast validated', publish_forecast: 'Publication approved' };
function payload(detail: string): Payload { try { return JSON.parse(detail) as Payload; } catch { return {}; } }
function label(event: AgentEvent) { const value = payload(event.detail); if (event.tool.startsWith('AI · ')) return titles[value.tool ?? ''] ?? event.tool; if (event.tool.includes('AGENT_DECISION')) return value.execution === 'live_openai' ? 'Live OpenAI · Completed' : `Execution · ${value.execution ?? 'recorded'}`; return event.tool; }
function isAgent(event: AgentEvent) { return event.tool.startsWith('AI · ') || event.tool.startsWith('AI started ·') || event.tool.includes('AGENT_DECISION'); }
export function AgentTimeline({ events, copy }: { events: AgentEvent[]; copy: Copy }) {
  const hasAgent = events.some(isAgent);
  const steps = ['get_weather_forecast', 'validate_inputs', 'run_forecast', 'validate_forecast', 'publish_forecast'].map(tool => {
    const started = events.filter(event => event.tool === `AI started · ${tool}`).at(-1); const finished = events.filter(event => event.tool === `AI · ${tool}`).filter(event => !started || Date.parse(event.time) >= Date.parse(started.time)).at(-1);
    return { tool, started, finished, status: finished?.status === 'failed' ? 'failed' : finished ? 'done' : started ? 'warning' : 'pending' };
  });
  const completed = events.some(event => event.tool === 'AI · live_openai');
  return <section className="card timeline-card" aria-labelledby="timeline-title"><div className="section-heading"><div><h2 id="timeline-title">{hasAgent ? 'OpenAI Agent' : copy.agentActivity}</h2><p>{hasAgent ? 'OpenAI orchestrates these tools; LightGBM supplies the numerical forecast.' : copy.toolsUsed}</p></div><span className={`agent-badge ${completed ? 'complete' : ''}`}>{hasAgent ? completed ? 'Live OpenAI · Completed' : 'Live OpenAI · Running' : 'Pipeline only'}</span></div>
    {hasAgent && <ol className="agent-stepper">{steps.map((step, index) => <li key={step.tool} className={step.status}><span>{index + 1}</span><div><strong>{titles[step.tool]}</strong><small>{step.status === 'done' ? `Completed ${step.finished ? new Date(step.finished.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''}` : step.status === 'warning' ? 'Running from backend event' : step.status === 'failed' ? 'Failed' : 'Waiting'}</small></div></li>)}</ol>}
    <ol className="timeline">{events.map((event, index) => { const started = event.tool.startsWith('AI started ·'); const tool = event.tool.replace('AI started · ', ''); const completed = started && events.some(next => next.tool === `AI · ${tool}` && Date.parse(next.time) >= Date.parse(event.time)); return <li key={event.id} className={event.status}><span className="timeline-node">{index + 1}</span><div className="event-content"><div className="event-row"><strong>{label(event)}</strong><time>{new Date(event.time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time></div><span className="event-status">{started ? completed ? 'Started' : 'Running' : event.status === 'done' ? copy.done : copy.warning}</span><details><summary>Event details</summary><pre>{event.detail}</pre></details></div></li>; })}</ol>
  </section>;
}
