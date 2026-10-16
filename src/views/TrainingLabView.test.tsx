import { describe, expect, it, afterEach, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SystemProvider } from '@/state/SystemContext';
import { TrainingLabView } from './TrainingLabView';
import { mockApi } from '@/test/server';

function renderLab() {
  return render(
    <SystemProvider>
      <TrainingLabView onOpen={() => undefined} />
    </SystemProvider>,
  );
}

beforeEach(() => {
  mockApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('training lab', () => {
  it('offers the datasets and methods the backend would accept', async () => {
    renderLab();
    // The method appears once the platform overview has loaded.
    expect(await screen.findByRole('button', { name: /Task head · softmax \+ SGD/ })).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: /specialists trained|Specialists/ }).catch(() => null)).toBeNull();
    expect(screen.getByText(/Methods usable/)).toBeInTheDocument();
    // The dataset it can consume is selected automatically.
    expect(await screen.findByLabelText('Dataset')).toHaveValue('coding.tool_routing');
  });

  it('states the artifact a method produces rather than calling everything fine-tuning', async () => {
    renderLab();
    await screen.findByLabelText('Dataset');
    // "Artifact:" is a nested span inside the paragraph, so match the paragraph.
    expect(
      await screen.findByText((_, element) => element?.tagName === 'P' && (element.textContent ?? '').includes('task_head_sgd · softmax weights')),
    ).toBeInTheDocument();
    expect(screen.getByText(/A softmax head over local features — not a fine-tuned base model\./)).toBeInTheDocument();
  });

  it('queues a run through the API and reports the before/after plan', async () => {
    const handler = mockApi();
    renderLab();
    await screen.findByRole('button', { name: /Task head · softmax \+ SGD/ });

    await userEvent.click(screen.getByRole('button', { name: 'Start training run' }));

    expect(await screen.findByText(/Run run_new queued/)).toBeInTheDocument();
    const post = handler.mock.calls.find(([url, init]) => String(url).includes('/api/training/runs') && (init as RequestInit)?.method === 'POST');
    expect(post).toBeTruthy();
    const body = JSON.parse((post![1] as RequestInit).body as string);
    expect(body.specialistId).toBe('coding-ai');
    expect(body.methodId).toBe('task_head_sgd');
    expect(body.datasetId).toBe('coding.tool_routing');
    expect(body.runBenchmarkAfter).toBe(true);
  });

  it('shows run history with the promotion decision that was actually made', async () => {
    renderLab();
    await screen.findByRole('button', { name: /Task head · softmax \+ SGD/ });
    await userEvent.click(screen.getByRole('tab', { name: /Runs/ }));
    expect(await screen.findByText(/quality 80\.0% vs untrained baseline 52\.2%/)).toBeInTheDocument();
    expect(screen.getByText('promoted')).toBeInTheDocument();
  });

  it('lists model versions with their status and evidence', async () => {
    renderLab();
    await screen.findByRole('button', { name: /Task head · softmax \+ SGD/ });
    await userEvent.click(screen.getByRole('tab', { name: 'Model versions' }));
    expect(await screen.findByText(/Coding · v1/)).toBeInTheDocument();
    expect(screen.getByText('Promote')).toBeDisabled();
    expect(screen.getByText('active')).toBeInTheDocument();
  });
});
