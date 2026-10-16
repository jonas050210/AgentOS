import { describe, expect, it, afterEach, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SystemProvider } from '@/state/SystemContext';
import { SpecialistWorkspaceView } from './SpecialistWorkspaceView';
import { mockApi } from '@/test/server';

function renderWorkspace(specialistId = 'coding-ai') {
  return render(
    <SystemProvider>
      <SpecialistWorkspaceView specialistId={specialistId} onOpen={() => undefined} />
    </SystemProvider>,
  );
}

beforeEach(() => {
  mockApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('specialist workspace', () => {
  it('summarises what the specialist can actually do right now', async () => {
    renderWorkspace();
    expect(await screen.findByRole('heading', { name: /Coding AI/ })).toBeInTheDocument();
    // Active model and best measured quality come from the recorded data.
    expect(screen.getAllByText('v1').length).toBeGreaterThan(0);
    expect(screen.getByText('80.0%')).toBeInTheDocument();
    // The baseline it was compared against is stated, not implied.
    expect(screen.getByText(/rule baseline 52.2%/)).toBeInTheDocument();
    // The dataset that will train it is listed, with its size and provenance.
    expect(screen.getByText('Coding · tool routing')).toBeInTheDocument();
    expect(screen.getByText(/146 items · generated · 2 labels/)).toBeInTheDocument();
  });

  it('runs the test bench through the real endpoint and shows the decision', async () => {
    renderWorkspace();
    await screen.findByRole('heading', { name: /Coding AI/ });
    await userEvent.click(screen.getByRole('tab', { name: 'Test bench' }));

    await userEvent.type(screen.getByLabelText('Test input'), 'what is 128 * 4');
    await userEvent.click(screen.getByRole('button', { name: 'Run' }));

    // The decision, its confidence and the tool call that produced the answer.
    expect(await screen.findByText('Result: 128*4 = 512')).toBeInTheDocument();
    // The decision appears both as the predicted label and in the probability list.
    expect(screen.getAllByText('calc.evaluate').length).toBeGreaterThan(0);
    expect(screen.getAllByText('94.7%').length).toBeGreaterThan(0);
    expect(screen.getByText('tool:calc.evaluate')).toBeInTheDocument();
  });

  it('explains an untrained specialist instead of showing an empty model', async () => {
    renderWorkspace('research-ai');
    expect(await screen.findByRole('heading', { name: /Research AI/ })).toBeInTheDocument();
    expect(screen.getByText(/No trained model version yet/)).toBeInTheDocument();
    expect(screen.getAllByText('untrained').length).toBeGreaterThan(0);
  });

  it('tells the user when a specialist id does not exist', async () => {
    renderWorkspace('does-not-exist');
    expect(await screen.findByText('Specialist not found')).toBeInTheDocument();
  });
});
