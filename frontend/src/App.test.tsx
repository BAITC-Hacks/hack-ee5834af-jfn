import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from './App';

describe('dashboard', () => {
  it('clearly labels demo values and both turbine series', async () => {
    render(<App />);
    expect(screen.getByText(/DEMO DATA · NOT LIVE/i)).toBeInTheDocument();
    expect(screen.getByText(/not measured data or an operational prediction/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/Forecast lines for Turbine 1 and Turbine 2/i)).toBeInTheDocument();
    expect(screen.getByText(/Actuals and confidence intervals are unavailable/i)).toBeInTheDocument();
  });
});
