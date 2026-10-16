import {useEffect, useRef, useState} from 'react';
import { api, ApiError } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import { useSpeech } from '@/hooks/useSpeech';
import { Badge, Card, EmptyState, ErrorBanner, Spinner, StatusBadge } from '@/components/ui';
import type { AgentTraceStep, StatusLevel } from '@shared/types';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  capabilityId?: string;
  providerId?: string;
  model?: string;
  degraded?: boolean;
  trace?: AgentTraceStep[];
}

const traceTone: Record<AgentTraceStep['status'], StatusLevel> = {
  ok: 'ok',
  warn: 'warn',
  error: 'error',
  skipped: 'unknown',
};

export function ChatView() {
  const { capabilities, settings, connection, activity, refresh } = useSystem();
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'system',
      content:
        'AgentOS is ready. Messages are routed to a capability and answered by the best available model provider. Without a configured provider, the built-in offline engine answers instead.',
      timestamp: new Date().toISOString(),
    },
  ]);
  const [input, setInput] = useState('');
  const [capabilityId, setCapabilityId] = useState<string>('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [speakReplies, setSpeakReplies] = useState(false);
  const [showTrace, setShowTrace] = useState(true);
  const speech = useSpeech();
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // scrollTo is not implemented in every environment (older browsers, jsdom).
    const el = listRef.current;
    if (!el || typeof el.scrollTo !== 'function') return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages, sending]);

  // Dictated text lands in the composer so the user reviews it before sending.
  useEffect(() => {
    if (speech.transcript) setInput(speech.transcript);
  }, [speech.transcript]);

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    setError(null);
    setSending(true);
    const userMessage: ChatMessage = {
      id: `local_${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    speech.stopListening();
    try {
      const response = await api.chat({ message: text, conversationId, capabilityId: capabilityId || undefined });
      setConversationId(response.conversationId);
      const meta = response.message.meta as Record<string, unknown> | undefined;
      setMessages((prev) => [
        ...prev,
        {
          id: response.message.id,
          role: 'assistant',
          content: response.message.content,
          timestamp: response.message.timestamp,
          capabilityId: meta?.capabilityId as string | undefined,
          providerId: meta?.providerId as string | undefined,
          model: meta?.model as string | undefined,
          degraded: Boolean(meta?.degraded),
          trace: response.trace,
        },
      ]);
      if (speakReplies) speech.speak(response.message.content);
    } catch (e) {
      // The error banner is the single place we report failures — no duplicate
      // system bubble, which would just add noise to the transcript.
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  const activeCapability = (capabilities ?? []).find((c) => c.id === capabilityId);
  const recentErrors = activity.filter((a) => a.level === 'error').slice(-1)[0];
  // Voice controls follow the persisted settings, not just browser support.
  const sttAllowed = Boolean(settings?.voice.sttEnabled) && speech.sttSupported;
  const ttsAllowed = Boolean(settings?.voice.ttsEnabled) && speech.ttsSupported;

  const cancelSpeech = speech.cancelSpeech;
  useEffect(() => {
    if (!ttsAllowed && speakReplies) {
      setSpeakReplies(false);
      cancelSpeech();
    }
  }, [ttsAllowed, speakReplies, cancelSpeech]);

  return (
    <div className="flex h-full min-h-0 gap-5">
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        {connection === 'offline' && (
          <ErrorBanner
            message="The AgentOS backend is unreachable. Start it with `npm run dev` (or `npm start`) and press Retry."
            onRetry={() => void refresh()}
          />
        )}
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

        <Card className="flex min-h-0 flex-1 flex-col !p-0">
          <div className="flex items-center justify-between gap-4 border-b border-white/[0.05] px-5 py-3">
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <StatusBadge level={connection === 'online' ? 'ok' : 'error'} label={connection === 'online' ? 'Connected' : 'Offline'} />
              {activeCapability ? (
                <span>
                  pinned to <span className="text-slate-200">{activeCapability.name}</span>
                </span>
              ) : (
                <span>capability auto-routed per message</span>
              )}
            </div>
            <div className="flex items-center gap-3 text-xs text-slate-400">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-accent-500"
                  checked={showTrace}
                  onChange={(e) => setShowTrace(e.target.checked)}
                />
                show reasoning trace
              </label>
              <label className="flex cursor-pointer items-center gap-2" title={ttsAllowed ? 'Speak replies aloud' : 'Text-to-speech is disabled in Settings → Voice or unavailable in this browser'}>
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-accent-500"
                  checked={speakReplies}
                  disabled={!ttsAllowed}
                  onChange={(e) => {
                    setSpeakReplies(e.target.checked);
                    if (!e.target.checked) speech.cancelSpeech();
                  }}
                />
                speak replies
              </label>
            </div>
          </div>

          <div ref={listRef} className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} showTrace={showTrace} canSpeak={speech.ttsSupported} onSpeak={() => speech.speak(m.content)} />
            ))}
            {sending && (
              <div className="flex items-center gap-3">
                <Spinner label="Routing request…" />
              </div>
            )}
            {messages.length <= 1 && (
              <EmptyState
                title="Start a conversation"
                description="Try: “calculate 12 * (4 + 3)”, “read server/index.ts”, “remember that my project uses Postgres 16”, or paste a URL to have the research capability fetch it."
              />
            )}
          </div>

          <div className="border-t border-white/[0.05] px-5 py-4">
            {speech.speechError && (
              <p className="mb-2 text-xs text-signal-warn">{speech.speechError}</p>
            )}
            <div className="flex items-end gap-3">
              <select
                aria-label="Capability"
                className="input !w-52 shrink-0"
                value={capabilityId}
                onChange={(e) => setCapabilityId(e.target.value)}
              >
                <option value="">Auto-route</option>
                {(capabilities ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                    {c.status === 'available' ? '' : ` (${c.status})`}
                  </option>
                ))}
              </select>
              <textarea
                aria-label="Message"
                className="input max-h-32 min-h-[42px] flex-1 resize-y"
                rows={2}
                placeholder={connection === 'online' ? 'Ask AgentOS anything…' : 'Backend offline — start the server to send messages'}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              <button
                type="button"
                className={`btn-ghost shrink-0 ${speech.listening ? '!border-signal-error/40 !text-signal-error' : ''}`}
                onClick={() => (speech.listening ? speech.stopListening() : speech.startListening())}
                disabled={!sttAllowed}
                title={sttAllowed ? 'Dictate with the microphone' : 'Speech-to-text is disabled in Settings → Voice or unavailable in this browser'}
              >
                {speech.listening ? 'Stop' : 'Dictate'}
              </button>
              <button type="button" className="btn-primary shrink-0" onClick={() => void send()} disabled={!input.trim() || sending}>
                {sending ? 'Sending…' : 'Send'}
              </button>
            </div>
          </div>
        </Card>
      </div>

      <aside className="hidden w-80 shrink-0 flex-col gap-4 xl:flex">
        <Card title="System state" subtitle="Live values from the backend">
          <ul className="space-y-2 text-xs text-slate-400">
            <li className="flex justify-between gap-3">
              <span>Model provider</span>
              <span className="text-slate-200">
                {settings?.providers.openai.hasApiKey ? 'OpenAI key set' : 'no external key'}
              </span>
            </li>
            <li className="flex justify-between gap-3">
              <span>Permissions</span>
              <span className="text-slate-200">
                {settings ? `${Object.values(settings.permissions).filter(Boolean).length}/4 granted` : '—'}
              </span>
            </li>
            <li className="flex justify-between gap-3">
              <span>Memory notes</span>
              <span className="text-slate-200">{activity.length ? 'tracked' : '—'}</span>
            </li>
            <li className="flex justify-between gap-3">
              <span>Capabilities</span>
              <span className="text-slate-200">{capabilities.length}</span>
            </li>
          </ul>
        </Card>
        <Card title="Last error" subtitle="Most recent error-level log entry">
          {recentErrors ? (
            <div className="space-y-1 text-xs">
              <Badge tone="error">{recentErrors.source}</Badge>
              <p className="text-slate-300">{recentErrors.message}</p>
              <p className="text-[11px] text-slate-500">{new Date(recentErrors.timestamp).toLocaleTimeString()}</p>
            </div>
          ) : (
            <p className="text-xs text-slate-500">No errors recorded in this session.</p>
          )}
        </Card>
      </aside>
    </div>
  );
}

function MessageBubble({
  message,
  showTrace,
  canSpeak,
  onSpeak,
}: {
  message: ChatMessage;
  showTrace: boolean;
  canSpeak: boolean;
  onSpeak: () => void;
}) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';
  return (
    <div className={`flex animate-fade-in ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[46rem] rounded-xl px-4 py-3 text-sm leading-relaxed ${isUser ? 'bg-accent-500/15 text-slate-100' : isSystem ? 'glass-subtle text-slate-400' : 'glass-panel text-slate-200'}`}>
        <p className="whitespace-pre-wrap">{message.content}</p>
        {!isUser && !isSystem && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/[0.05] pt-2 text-[11px] text-slate-500">
            {message.capabilityId && <span>{message.capabilityId}</span>}
            {message.model && (
              <Badge tone={message.degraded ? 'warn' : 'ok'}>
                {message.degraded ? 'offline engine' : 'model'}: {message.model}
              </Badge>
            )}
            <span>{new Date(message.timestamp).toLocaleTimeString()}</span>
            <button type="button" className="rounded px-1.5 py-0.5 hover:bg-white/10" onClick={onSpeak} disabled={!canSpeak} title={canSpeak ? 'Read aloud' : 'Speech synthesis unavailable'}>
              read aloud
            </button>
          </div>
        )}
        {showTrace && message.trace && message.trace.length > 0 && (
          <ul className="mt-3 space-y-1.5 border-t border-white/[0.05] pt-3">
            {message.trace.map((step, i) => (
              <li key={`${step.step}-${i}`} className="flex items-start gap-2 text-[11px]">
                <Badge tone={traceTone[step.status]}>{step.step}</Badge>
                <span className="text-slate-400">{step.detail}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
