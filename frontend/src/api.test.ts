import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApiAdapter } from './api';
import { demoAdapter } from './demoFixture';

afterEach(() => vi.restoreAllMocks());
describe('data adapters', () => {
  it('keeps API errors as errors instead of substituting demo data', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    await expect(createApiAdapter('/api').start({ asOf: '2026-02-06T00:00:00+05:00', horizon: 24 })).rejects.toThrow('POST /forecast-runs failed (503)');
  });
  it('sends registered IDs and maps the real native API payload', async () => {
    const points = Array.from({length:24},(_,index)=>['T1','T2'].map((turbine_id, turbine)=>({turbine_id,target_start:new Date(Date.parse('2026-02-06T00:00:00Z')+index*3600000).toISOString(),normalized_power:.2+turbine/10}))).flat();
    const responses = [
      { id:'r1', status:'QUEUED' },
      { id:'r1', status:'SUCCEEDED', as_of:'2026-02-06T00:00:00Z', horizon:24, model_id:'brev-scada-pooled-lgbm-20260201' },
      { status:'SUCCEEDED', points,warnings:['experimental'] },
      { as_of:'2026-02-06T00:00:00+00:00',weather_snapshot_id:'gfs-feb6',snapshot_hash:'hash',details:{model_training_cutoff:'2026-01-31T19:00:00Z',model_sha256:'sha'} },
      [{seq:1,created_at:'2026-02-06T00:00:00Z',type:'FORECAST_READY',payload:{points:96}}],
    ];
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve({ ok:true, json:() => Promise.resolve(responses.shift()) })); vi.stubGlobal('fetch', fetchMock);
    const adapter=createApiAdapter('/api'); expect((await adapter.start({asOf:'2026-02-06T00:00:00Z',horizon:48})).status).toBe('queued');
    const result=await adapter.result('r1'); expect(result.forecast).toHaveLength(24); expect(result.warnings).toEqual(['experimental']);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({model_id:'brev-scada-pooled-lgbm-20260201',weather_snapshot_id:'gfs-feb6',mode:'replay'});
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
