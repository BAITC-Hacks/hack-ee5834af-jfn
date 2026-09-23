import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

describe('dashboard', () => {
  afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
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
});
