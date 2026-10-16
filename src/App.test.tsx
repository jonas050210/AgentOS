import { describe, expect, it, afterEach, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import { mockApi } from '@/test/server';

beforeEach(() => {
  mockApi();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('application shell', () => {
  it('renders the shell with live status once the backend responds', async () => {
    render(<App />);
    expect(await screen.findByText(/Subsystems nominal|subsystems nominal/)).toBeInTheDocument();
    expect(screen.getByText('AgentOS')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Chat/ })).toHaveAttribute('aria-current', 'page');
  });

  it('navigates between all primary views', async () => {
    render(<App />);
    await screen.findByLabelText('Message');

    await userEvent.click(screen.getByRole('button', { name: /Models/ }));
    expect(await screen.findByText('Model catalog')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Capabilities/ }));
    expect(await screen.findByText('Capability modules')).toBeInTheDocument();
    expect(screen.getByText('Tools')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Activity/ }));
    expect(await screen.findByText('Event stream')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Settings/ }));
    expect(await screen.findByLabelText(/API key/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Chat/ }));
    expect(await screen.findByLabelText('Message')).toBeInTheDocument();
  });

  it('shows the offline banner when the backend is unreachable', async () => {
    mockApi({}, { failEverything: true });
    render(<App />);
    expect(await screen.findByText(/The AgentOS backend is unreachable/)).toBeInTheDocument();
  });
});
