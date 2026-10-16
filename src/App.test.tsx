import { describe, expect, it, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import { mockApi } from '@/test/server';

beforeEach(() => {
  mockApi();
  // The shell writes the current view into the hash; reset it between tests.
  window.location.hash = '';
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('application shell', () => {
  it('opens on the overview and reports the system state honestly', async () => {
    render(<App />);
    expect(await screen.findByRole('heading', { name: /specialists trained/ })).toBeInTheDocument();
    // One specialist has a trained version, the other is shown as untrained.
    expect(screen.getByText('Coding AI')).toBeInTheDocument();
    expect(screen.getByText('Research AI')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Overview/ })).toHaveAttribute('aria-current', 'page');
    expect(await screen.findByText(/subsystems nominal/)).toBeInTheDocument();
  });

  it('navigates every primary view from the sidebar', async () => {
    render(<App />);
    await screen.findByRole('heading', { name: /specialists trained/ });

    await userEvent.click(screen.getByRole('button', { name: /AI Assistant/ }));
    expect(await screen.findByLabelText('Message')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Training Lab/ }));
    expect(await screen.findByRole('tab', { name: 'Configure a run' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Benchmarks/ }));
    expect(await screen.findByRole('tab', { name: 'Run a benchmark' })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Add AI/ }));
    expect(await screen.findByText(/Choose a starting point/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Activity/ }));
    expect(await screen.findByText('Event stream')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Settings/ }));
    expect(await screen.findByLabelText(/API key/)).toBeInTheDocument();
  });

  it('opens a specialist workspace from the sidebar', async () => {
    render(<App />);
    await screen.findByRole('heading', { name: /specialists trained/ });
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    await userEvent.click(within(nav).getByRole('button', { name: /^Coding/ }));
    expect(await screen.findByRole('heading', { name: /Coding AI workspace/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Test bench' })).toBeInTheDocument();
  });

  it('shows the offline banner when the backend is unreachable', async () => {
    mockApi({}, { failEverything: true });
    render(<App />);
    expect(await screen.findByText(/Cannot reach the AgentOS backend/)).toBeInTheDocument();
  });
});
