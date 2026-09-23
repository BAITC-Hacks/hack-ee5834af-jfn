import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

describe('dashboard', () => {
  afterEach(() => vi.restoreAllMocks());
  it('clearly labels demo values and both turbine series', async () => {
    render(<App />);
    expect(screen.getByText(/DEMO DATA · NOT LIVE/i)).toBeInTheDocument();
    expect(screen.getByText(/not measured data or an operational prediction/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Forecast lines for Turbine 1 and Turbine 2/i)).toBeInTheDocument();
    expect(screen.getByText(/Actuals and confidence intervals are unavailable/i)).toBeInTheDocument();
  });

  it('shows an API error without restoring fixture results', async () => {
    render(<App />);
    await screen.findByText(/Two-turbine forecast/i);
    fireEvent.change(screen.getByLabelText(/Data source/i), { target: { value: 'api' } });
    fireEvent.click(screen.getByRole('button', { name: /Run forecast/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/No demo data has been substituted/i);
    expect(screen.queryByText(/Two-turbine forecast/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Backend error/i)).toBeInTheDocument();
  });

  it('clears an in-flight API run when the source changes', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)));
    render(<App />);
    await screen.findByText(/Two-turbine forecast/i);
    const source = screen.getByLabelText(/Data source/i);
    fireEvent.change(source, { target: { value: 'api' } });
    fireEvent.click(screen.getByRole('button', { name: /Run forecast/i }));
    expect(screen.getByRole('button', { name: /Running/i })).toBeDisabled();
    fireEvent.change(source, { target: { value: 'demo' } });
    expect(screen.getByRole('button', { name: /Run forecast/i })).toBeEnabled();
    expect(screen.queryByText(/Two-turbine forecast/i)).not.toBeInTheDocument();
  });
});
