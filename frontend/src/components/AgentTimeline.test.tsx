import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { AgentTimeline } from './AgentTimeline';
import { copy } from '../i18n';

const event = (tool: string, time: string, status: 'done' | 'warning' | 'failed' = 'done') => ({ id: `${tool}-${time}`, tool, time, detail: JSON.stringify({ tool: 'validate_inputs' }), status });
it('shows backend-started tools as running and uses the latest retry outcome', () => {
  render(<AgentTimeline copy={copy.en} events={[event('AI started · validate_inputs', '2026-02-06T06:00:00Z', 'warning'), event('AI · validate_inputs', '2026-02-06T06:00:01Z', 'failed'), event('AI started · validate_inputs', '2026-02-06T06:00:02Z', 'warning'), event('AI · validate_inputs', '2026-02-06T06:00:03Z')]} />);
  expect(screen.getByText('OpenAI Agent')).toBeInTheDocument();
  expect(screen.getByText('Live OpenAI · Running')).toBeInTheDocument();
  expect(screen.getAllByText('Inputs validated')).toHaveLength(3);
  expect(screen.getAllByText('Started')).toHaveLength(2);
});
it('marks completion only after the recorded live OpenAI decision', () => {
  render(<AgentTimeline copy={copy.en} events={[event('AI · live_openai', '2026-02-06T06:00:05Z')]} />);
  expect(screen.getByText('Live OpenAI · Completed')).toBeInTheDocument();
});
