import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { ForecastPoint } from '../types';

export function ForecastChart({ points, isDemo }: { points: ForecastPoint[]; isDemo: boolean }) {
  const data = points.map(p => ({ ...p, hour: new Date(p.targetStart).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }));
  return <section className="card chart-card" aria-labelledby="chart-title">
    <div className="section-heading"><div><span className="eyebrow">Normalized power · 0–1</span><h2 id="chart-title">Two-turbine forecast</h2></div><span className="chart-range">{points.length} hours</span></div>
    <p className="muted">{isDemo ? 'Illustrative fixture series only. ' : ''}Actuals and confidence intervals are unavailable.</p>
    <div className="chart-wrap" aria-label="Forecast lines for Turbine 1 and Turbine 2">
      <ResponsiveContainer width="100%" height="100%"><LineChart data={data} margin={{ top: 20, right: 12, left: -18, bottom: 5 }}>
        <CartesianGrid stroke="#e7eeeb" vertical={false} strokeDasharray="3 6" /><XAxis dataKey="hour" minTickGap={38} tickLine={false} axisLine={false} tick={{ fill: '#75817b', fontSize: 11 }} />
        <YAxis domain={[0, 1]} ticks={[0, .25, .5, .75, 1]} tickLine={false} axisLine={false} tick={{ fill: '#75817b', fontSize: 11 }} />
        <Tooltip formatter={(value) => [Number(value).toFixed(3), 'Normalized power']} labelStyle={{ color: '#17211d' }} contentStyle={{ border: '1px solid #dfe8e4', borderRadius: 12, boxShadow: '0 10px 30px rgba(15,23,42,.08)' }} />
        <Legend iconType="plainline" /><Line type="monotone" dataKey="turbine1" name="Turbine 1" stroke="#068466" strokeWidth={3} dot={false} activeDot={{ r: 5 }} />
        <Line type="monotone" dataKey="turbine2" name="Turbine 2" stroke="#0c9aa0" strokeWidth={2.5} strokeDasharray="7 5" dot={false} activeDot={{ r: 5 }} />
      </LineChart></ResponsiveContainer>
    </div>
  </section>;
}
