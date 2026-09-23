import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApiAdapter } from './api';
import { demoAdapter } from './demoFixture';

afterEach(() => vi.restoreAllMocks());
describe('data adapters', () => {
  it('keeps API errors as errors instead of substituting demo data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    await expect(createApiAdapter('/api').start({ asOf: '2026-02-06T00:00:00+05:00', horizon: 24 })).rejects.toThrow('POST /forecast-runs failed (503)');
  });
  it('marks fixture results as demo data', async () => {
    const run = await demoAdapter.start({ asOf: '2026-02-08T12:00:00+05:00', horizon: 24 });
    const result = await demoAdapter.result(run.id);
    expect(result.isDemo).toBe(true);
    expect(result.forecast).toHaveLength(24);
    expect(result.audit[0].value).toBe('2026-02-08T07:00:00.000Z');
    expect(result.events[0].detail).toContain('24h policy');
  });
});
