import { useEffect, useMemo, useRef, useState } from 'react';
import { createApiAdapter } from './api';
import { demoAdapter } from './demoFixture';
import { AgentTimeline } from './components/AgentTimeline';
import { AuditPanel } from './components/AuditPanel';
import { ForecastChart } from './components/ForecastChart';
import { ReplayControls } from './components/ReplayControls';
import type { DashboardData, ReplayRequest, SourceMode } from './types';
import { copy as translations, type Language } from './i18n';

const api = createApiAdapter(import.meta.env.VITE_API_BASE_URL ?? '/api');
const terminal = new Set(['succeeded', 'failed']);
function pollDelay(signal: AbortSignal) { return new Promise<void>((resolve, reject) => { const timer = window.setTimeout(resolve, 900); signal.addEventListener('abort', () => { window.clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); }, { once: true }); }); }

export default function App() {
  const [source, setSource] = useState<SourceMode>('api');
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem('wind-language') === 'kk' ? 'kk' : 'en');
  const [languageOpen, setLanguageOpen] = useState(false);
  const languageMenu = useRef<HTMLDivElement | null>(null);
  const [request, setRequest] = useState<ReplayRequest>({ asOf: '2026-02-06T11:00:00+05:00', horizon: 48 });
  const [data, setData] = useState<DashboardData | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null); const adapter = useMemo(() => source === 'demo' ? demoAdapter : api, [source]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { const close = (event: PointerEvent) => { if (!languageMenu.current?.contains(event.target as Node)) setLanguageOpen(false); }; document.addEventListener('pointerdown', close); return () => document.removeEventListener('pointerdown', close); }, []);
  useEffect(() => { void run(); /* initial reviewable fixture */ }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    controller.current?.abort(); const current = new AbortController(); controller.current = current; setBusy(true); setError(''); setData(null);
    let timedOut = false;
    const deadline = window.setTimeout(() => { if (!current.signal.aborted) { timedOut = true; current.abort(); if (controller.current === current) { setError('Forecast request timed out after 60 seconds'); setBusy(false); } } }, 60_000);
    try {
      let summary = await adapter.start(request, current.signal);
      while (!terminal.has(summary.status)) { await pollDelay(current.signal); summary = await adapter.status(summary.id, current.signal); }
      if (summary.status === 'failed') throw new Error(summary.error ?? `Run ${summary.id} failed`);
      const result = await adapter.result(summary.id, current.signal); if (!current.signal.aborted) setData(result);
    } catch (cause) { if (controller.current === current && (!current.signal.aborted || timedOut)) setError(timedOut ? 'Forecast request timed out after 60 seconds' : cause instanceof Error ? cause.message : 'Unable to run forecast'); }
    finally { window.clearTimeout(deadline); if (controller.current === current && (!current.signal.aborted || timedOut)) setBusy(false); }
  }
  function changeSource(next: SourceMode) { controller.current?.abort(); setSource(next); setData(null); setError(''); setBusy(false); }
  function exportData(format: 'json' | 'csv') { if (!data) return; const content = format === 'json' ? JSON.stringify(data, null, 2) : ['target_start,turbine_1,turbine_2,is_demo', ...data.forecast.map(p => `${p.targetStart},${p.turbine1},${p.turbine2},${data.isDemo}`)].join('\n'); const url = URL.createObjectURL(new Blob([content], { type: format === 'json' ? 'application/json' : 'text/csv' })); const a = document.createElement('a'); a.href = url; a.download = `${data.run.id}.${format}`; a.click(); URL.revokeObjectURL(url); }
  const text = translations[language];
  function selectLanguage(next: Language) { setLanguage(next); setLanguageOpen(false); localStorage.setItem('wind-language', next); }
  const displayData = useMemo(() => {
    if (!data?.isDemo || language === 'en') return data;
    const auditLabels = ['Қайта ойнату уақыты', 'Ауа райы болжамының басталуы', 'Ауа райы деректерінің қолжетімді уақыты', 'SCADA шегі', 'Снимок ID', 'Кіріс хэші'];
    const eventDetails = [`Басталу уақыты мен ${data.forecast.length} сағаттық саясат бекітілді`, 'Ауа райының үлгі деректері таңдалды', 'SCADA өзектілігі қолжетімсіз', 'Үлгі нәтиже жарияланды'];
    return { ...data, modelVersion: 'Алдын ала қарау моделі', weatherAge: '3 сағат', scadaFreshness: 'Берілмеген', temporalValidation: 'Болжамдар', audit: data.audit.map((item, index) => ({ ...item, label: auditLabels[index] ?? item.label })), warnings: ['Үлгі мәндер операциялық болжам болып табылмайды.', 'Бұл алдын ала қарауда жаңа SCADA деректері берілмеген.'], events: data.events.map((event, index) => ({ ...event, detail: eventDetails[index] ?? event.detail })) };
  }, [data, language]);
  const cards = [{ label: text.modelVersion, value: displayData?.modelVersion }, { label: text.weatherAge, value: displayData?.weatherAge }, { label: text.scadaFreshness, value: displayData?.scadaFreshness }, { label: text.temporalValidation, value: displayData?.temporalValidation }];
  return <div className="app-shell">
    <header className="app-header">
      <div className="brand"><img className="mark" src="/mangust-logo.png" alt="" /><div><strong>Mangust</strong><span>{text.operations}</span></div></div>
      <nav aria-label="Primary navigation">
        <a className="active" href="#dashboard">{text.dashboard}</a>
        <a href="#replay">{text.replay}</a>
        <a href="#runs">{text.runs}</a>
        <a href="#models">{text.models}</a>
        <a href="#audit">{text.auditLog}</a>
      </nav>
      <div className="language-picker" ref={languageMenu}><button className="language-trigger" aria-label={text.language} aria-haspopup="menu" aria-expanded={languageOpen} onClick={() => setLanguageOpen(open => !open)}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.4 4.2 9.3 0 18M12 3c-3 3.4-4.2 9.3 0 18"/></svg></button>{languageOpen && <div className="language-dropdown" role="menu"><button role="menuitem" onClick={() => selectLanguage('kk')}><span>Қазақша</span>{language === 'kk' && <b>✓</b>}</button><button role="menuitem" onClick={() => selectLanguage('en')}><span>English</span>{language === 'en' && <b>✓</b>}</button></div>}</div>
    </header>
    <main className="workspace" id="dashboard">
      <div className="dashboard-content">
        <ReplayControls request={request} source={source} busy={busy} copy={text} onRequest={setRequest} onSource={changeSource} onRun={() => void run()} />
        {source === 'demo' && <div className="demo-banner"><strong>{text.sampleData}</strong><span>{text.sampleNotice}</span></div>}
        {error && <div className="error-state" role="alert"><div><strong>{text.loadError}</strong><span>{error}. {text.noFallback}</span></div><button onClick={() => void run()}>{text.retry}</button></div>}
        <section className="metric-grid" aria-label="Run provenance summary">{cards.map(card => <article className="metric" key={card.label}><span>{card.label}</span><strong>{busy ? text.loading : card.value ?? text.unavailable}</strong></article>)}</section>
        {!data && !busy && !error && <div className="empty-state">{text.empty}</div>}
        {displayData && <><div className="main-grid"><ForecastChart points={displayData.forecast} isDemo={displayData.isDemo} copy={text} language={language} /><AgentTimeline events={displayData.events} copy={text} /></div><AuditPanel data={displayData} copy={text} onExport={exportData} /></>}
      </div>
    </main>
  </div>;
}
