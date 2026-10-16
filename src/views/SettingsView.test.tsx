import { describe, expect, it, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SettingsView } from './SettingsView';
import { SystemProvider } from '@/state/SystemContext';
import { mockApi } from '@/test/server';
import { settingsFixture } from '@/test/fixtures';

function renderSettings() {
  return render(
    <SystemProvider>
      <SettingsView />
    </SystemProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SettingsView', () => {
  it('renders persisted values from the backend', async () => {
    mockApi();
    renderSettings();
    expect(await screen.findByLabelText(/API key/)).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByLabelText('Base URL')[0]).toHaveValue(settingsFixture.providers.openai.baseUrl);
    });
    expect(screen.getByRole('switch', { name: /Write notes/ })).toHaveAttribute('aria-checked', 'false');
  });

  it('sends permission changes to the API', async () => {
    const handler = mockApi();
    renderSettings();
    const sw = await screen.findByRole('switch', { name: /Network requests/ });
    await userEvent.click(sw);
    await waitFor(() => {
      const put = handler.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PUT');
      expect(put).toBeTruthy();
      expect(String((put![1] as RequestInit).body)).toContain('"allowNetwork":true');
    });
  });

  it('marks a stored API key without revealing it', async () => {
    mockApi({
      '/api/settings': {
        ...settingsFixture,
        providers: { ...settingsFixture.providers, openai: { ...settingsFixture.providers.openai, hasApiKey: true } },
      },
    });
    renderSettings();
    expect(await screen.findByPlaceholderText('•••••• (unchanged)')).toBeInTheDocument();
    expect(screen.queryByDisplayValue(/sk-/)).not.toBeInTheDocument();
  });

  it('reports an unreachable backend instead of spinning forever', async () => {
    mockApi({}, { failEverything: true });
    renderSettings();
    expect(await screen.findByText(/Cannot reach the AgentOS backend/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});
