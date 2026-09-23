import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { formatForecastTime } from './time';

describe('dashboard', () => {
  afterEach(() => vi.restoreAllMocks());
  it('clearly labels demo values and both turbine series', async () => {
    render(<App />);
    expect(screen.getByText(/^Sample data$/i)).toBeInTheDocument();
    expect(screen.getByText(/interface preview only/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Forecast lines for Turbine 1 and Turbine 2/i)).toBeInTheDocument();
    expect(screen.getByText(/Actuals and confidence intervals are unavailable/i)).toBeInTheDocument();
    expect(screen.getAllByRole('term')[0]).toHaveTextContent(/Replay as of/i);
  });

  it('shows an API error without restoring fixture results', async () => {
    render(<App />);
    await screen.findByText(/Power forecast/i);
    fireEvent.change(screen.getByLabelText(/Data source/i), { target: { value: 'api' } });
    fireEvent.click(screen.getByRole('button', { name: /Run forecast/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/No sample data was substituted/i);
    expect(screen.queryByText(/Power forecast/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Backend · error/i)).toBeInTheDocument();
  });

  it('clears an in-flight API run when the source changes', async () => {
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal('fetch', vi.fn((_url: string, init?: RequestInit) => { requestSignal = init?.signal ?? undefined; return new Promise(() => undefined); }));
    render(<App />);
    await screen.findByText(/Power forecast/i);
    const source = screen.getByLabelText(/Data source/i);
    fireEvent.change(source, { target: { value: 'api' } });
    fireEvent.click(screen.getByRole('button', { name: /Run forecast/i }));
    expect(screen.getByRole('button', { name: /Running/i })).toBeDisabled();
    fireEvent.change(source, { target: { value: 'demo' } });
    expect(requestSignal?.aborted).toBe(true);
    expect(screen.getByRole('button', { name: /Run forecast/i })).toBeEnabled();
    expect(screen.queryByText(/Power forecast/i)).not.toBeInTheDocument();
  });

  it('formats chart hours in the replay timezone with a date', () => {
    expect(formatForecastTime('2026-02-05T20:00:00.000Z')).toMatch(/06 Feb, 01:00/i);
  });
});
