import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatView } from './ChatView';
import { SystemProvider } from '@/state/SystemContext';
import { mockApi } from '@/test/server';
import { ApiError } from '@/lib/api';
import { settingsFixture } from '@/test/fixtures';

function renderChat() {
  return render(
    <SystemProvider>
      <ChatView />
    </SystemProvider>,
  );
}

beforeEach(() => {
  // jsdom does not implement the Web Speech API; the view must degrade gracefully.
  Object.defineProperty(window, 'speechSynthesis', { value: undefined, configurable: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ChatView', () => {
  it('sends a message and renders the routed reply with its trace', async () => {
    mockApi();
    renderChat();
    const input = await screen.findByLabelText('Message');
    await userEvent.type(input, 'hello agentos');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('Offline answer from the built-in engine.')).toBeInTheDocument();
    expect(screen.getByText(/Auto-routed to General assistant/)).toBeInTheDocument();
    expect(input).toHaveValue('');
  });

  it('shows a structured error when the chat request fails', async () => {
    mockApi({}, { chatError: { network: true } });
    renderChat();
    const input = await screen.findByLabelText('Message');
    await userEvent.type(input, 'boom');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText(/Cannot reach the AgentOS backend/)).toBeInTheDocument();
  });

  it('surfaces backend validation errors verbatim', async () => {
    mockApi({}, { chatError: { status: 400, message: 'Message exceeds 8000 characters', code: 'VALIDATION_ERROR' } });
    renderChat();
    const input = await screen.findByLabelText('Message');
    await userEvent.type(input, 'x');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText(/Message exceeds 8000 characters/)).toBeInTheDocument();
  });

  it('labels an unreachable provider instead of showing a model name', async () => {
    mockApi(
      {},
      {
        chatResponse: {
          conversationId: 'conv_test',
          message: {
            id: 'msg_a',
            role: 'assistant',
            content: 'The specialist could not complete this request: socket hang up',
            timestamp: new Date('2026-01-01T00:00:03.000Z').toISOString(),
            meta: {
              capabilityId: 'general.assistant',
              providerId: 'ollama',
              providerName: 'Ollama (local)',
              model: null,
              degraded: false,
              answeredBy: 'local',
              providerError: 'socket hang up',
            },
          },
          trace: [
            { step: 'route', detail: 'Auto-routed to Concierge AI', status: 'ok' },
            { step: 'escalate', detail: 'Provider failure: socket hang up', status: 'error' },
          ],
        },
      },
    );
    renderChat();
    const input = await screen.findByLabelText('Message');
    await userEvent.type(input, 'hello agentos');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByText('provider unreachable')).toBeInTheDocument();
    // The reply is never credited to a model that produced nothing.
    expect(screen.queryByText(/^model:/)).not.toBeInTheDocument();
  });

  it('disables the send button for empty input', async () => {
    mockApi();
    renderChat();
    const send = await screen.findByRole('button', { name: 'Send' });
    expect(send).toBeDisabled();
  });

  it('renders an offline banner when the whole backend is unreachable', async () => {
    mockApi({}, { failEverything: true });
    renderChat();
    expect(await screen.findByText(/The AgentOS backend is unreachable/)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('disables dictation when speech recognition is unavailable', async () => {
    mockApi();
    renderChat();
    const dictate = await screen.findByRole('button', { name: 'Dictate' });
    expect(dictate).toBeDisabled();
  });
});

describe('ApiError', () => {
  it('keeps status and code for callers', () => {
    const err = new ApiError('nope', 403, 'PERMISSION_DENIED');
    expect(err.status).toBe(403);
    expect(err.code).toBe('PERMISSION_DENIED');
    expect(err).toBeInstanceOf(Error);
  });
});

describe('ChatView voice controls', () => {
  it('respects the persisted speech settings', async () => {
    mockApi({
      '/api/settings': {
        ...settingsFixture,
        voice: { sttEnabled: false, ttsEnabled: false, voiceName: '' },
      },
    });
    renderChat();
    expect(await screen.findByRole('button', { name: 'Dictate' })).toBeDisabled();
    expect(await screen.findByRole('checkbox', { name: /speak replies/i })).toBeDisabled();
  });
});
