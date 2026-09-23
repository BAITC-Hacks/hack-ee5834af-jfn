import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

describe('dashboard', () => {
  beforeEach(() => vi.stubEnv('VITE_MODEL_ID', 'linear-v1'));
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllEnvs(); localStorage.clear(); });
  it('defaults to the registered API replay origin and keeps errors honest', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422 }));
    render(<App />);
    expect(screen.getByLabelText(/Replay date/i)).toHaveValue('2026-02-06T05:00');
    expect(await screen.findByRole('alert')).toHaveTextContent(/No sample data was substituted/i);
  });
  it('only shows synthetic values after selecting Sample data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    render(<App />);
    await screen.findByRole('alert');
    fireEvent.change(screen.getByLabelText(/Data source/i), { target: { value: 'demo' } });
    fireEvent.click(screen.getByRole('button', { name: /Run forecast/i }));
    expect(await screen.findByText(/interface preview only/i)).toBeInTheDocument();
    expect(screen.getByText(/Sample values are not an operational forecast/i)).toBeInTheDocument();
  });
  it('times out a hung request and restores the run button', async () => {
    vi.useFakeTimers(); vi.stubGlobal('fetch', vi.fn((_url, init?:RequestInit) => new Promise((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))));
    render(<App />); await act(async () => { await vi.advanceTimersByTimeAsync(60_000); await Promise.resolve(); });
    expect(screen.getByRole('alert')).toHaveTextContent(/timed out after 60 seconds/i);
    expect(screen.getByRole('button', { name: /Run forecast/i })).toBeEnabled();
  });
  it('switches to Kazakh Pipeline wording without claiming an AI agent', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422 })); render(<App />); await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: 'Language' })); fireEvent.click(screen.getByRole('menuitem', { name: /Қазақша/i }));
    expect(screen.getByRole('link', { name: 'Бақылау тақтасы' })).toBeInTheDocument();
  });
  it('shows a BLOCKED backend reason without polling or forecast reads', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok:true, json:async () => ({id:'blocked',status:'BLOCKED',error:'model hash mismatch'}) }); vi.stubGlobal('fetch', fetchMock);
    render(<App />); expect(await screen.findByRole('alert')).toHaveTextContent(/model hash mismatch/i); expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('aborts an old request when switching source and remains usable', async () => {
    let signal:AbortSignal|undefined; vi.stubGlobal('fetch', vi.fn((_url, init?:RequestInit) => { signal=init?.signal ?? undefined; return new Promise(() => undefined); }));
    render(<App />); fireEvent.change(screen.getByLabelText(/Data source/i), { target:{value:'demo'} });
    expect(signal?.aborted).toBe(true); fireEvent.click(screen.getByRole('button',{name:/Run forecast/i})); expect(await screen.findByText(/interface preview only/i)).toBeInTheDocument();
  });
});
