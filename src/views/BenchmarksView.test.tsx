import { describe, expect, it, afterEach, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SystemProvider } from '@/state/SystemContext';
import { BenchmarksView } from './BenchmarksView';
import { OverviewView } from './OverviewView';
import { mockApi } from '@/test/server';

function renderWith(target: React.ReactElement) {
  return render(<SystemProvider>{target}</SystemProvider>);
}

beforeEach(() => {
  mockApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('benchmarks view', () => {
  it('shows recorded scorecards with the numbers that were measured', async () => {
    renderWith(<BenchmarksView onOpen={() => undefined} />);
    // Wait for the stored cards rather than the suite picker.
    expect(await screen.findByText(/72\/90 tasks/)).toBeInTheDocument();
    // The baseline card is labelled as a baseline, not as a trained version.
    expect(screen.getByText('baseline')).toBeInTheDocument();
    expect(screen.getAllByText('Coding · decision routing').length).toBeGreaterThan(0);
  });

  it('pairs a measurement with its baseline and labels a regression as one', async () => {
    renderWith(<BenchmarksView onOpen={() => undefined} />);
    await screen.findAllByText('Coding · decision routing');
    await userEvent.click(screen.getAllByText('Coding · decision routing')[0]);
    // The comparison for this pair reports the latency regression it measured.
    expect(await screen.findByText(/is worse than untrained baseline/)).toBeInTheDocument();
    expect(screen.getByText('Latency p50 (ms)')).toBeInTheDocument();
  });

  it('runs a suite on a chosen version and reports the result', async () => {
    const handler = mockApi();
    renderWith(<BenchmarksView onOpen={() => undefined} />);
    await screen.findAllByText('Coding · decision routing');
    await userEvent.click(screen.getByRole('tab', { name: 'Run a benchmark' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Run benchmark' }));

    expect(await screen.findByText(/measured v1: 80\.0% quality/)).toBeInTheDocument();
    const post = handler.mock.calls.find(([url, init]) => String(url).includes('/api/benchmarks/run') && (init as RequestInit)?.method === 'POST');
    const body = JSON.parse((post![1] as RequestInit).body as string);
    expect(body.suiteId).toBe('coding.routing');
    expect(body.specialistId).toBe('coding-ai');
  });

  it('explains the promotion gate instead of implying a fixed score is enough', async () => {
    renderWith(<BenchmarksView onOpen={() => undefined} />);
    await screen.findAllByText('Coding · decision routing');
    await userEvent.click(screen.getByRole('tab', { name: 'Run a benchmark' }));
    expect(await screen.findByText(/How a promotion is decided/)).toBeInTheDocument();
    expect(screen.getByText(/2 quality points, 25% latency, 50% resource/)).toBeInTheDocument();
  });
});

describe('overview view', () => {
  it('never shows a benchmark score for a specialist that has none', async () => {
    renderWith(<OverviewView onOpen={() => undefined} />);
    expect(await screen.findByRole('heading', { name: /specialists trained/ })).toBeInTheDocument();
    // Research AI is untrained, so its card says so instead of showing 0%.
    expect(screen.getAllByText('untrained').length).toBeGreaterThan(0);
    expect(screen.getByText('not measured')).toBeInTheDocument();
  });

  it('states the training gain over the untrained baseline', async () => {
    renderWith(<OverviewView onOpen={() => undefined} />);
    // 80.0% trained against 52.2% baseline → +27.8 points.
    expect(await screen.findByText('+27.8 pts')).toBeInTheDocument();
  });

  it('explains the unavailable external fine-tuning path', async () => {
    renderWith(<OverviewView onOpen={() => undefined} />);
    expect(await screen.findByText('External fine-tuning')).toBeInTheDocument();
    expect(screen.getByText(/reported as unavailable rather than simulated/)).toBeInTheDocument();
  });
});
