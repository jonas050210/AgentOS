import { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import { useSpeech } from '@/hooks/useSpeech';
import { Badge, Card, Chip, EmptyState, ErrorBanner, Select, StatusBadge, StatusDot, Spinner, Toggle } from '@/components/ui';
import type { AgentTraceStep, StatusLevel } from '@shared/types';
import type { NavTarget } from '@/components/Sidebar';

/**
 * The central assistant.
 *
 * Two modes, both real:
 *   • auto — AgentOS routes by intent to whichever specialist should answer;
 *   • pinned — the user names the specialist and the request goes there.
 *
 * Every reply carries who answered it: a trained local head, the built-in rule
 * policy, the offline engine, or an external provider. When the provider was
 * unreachable that is stated, never smoothed over.
 */

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  specialistId?: string;
  specialistName?: string;
  engine?: string;
  model?: string;
  versionLabel?: string | null;
  predicted?: string | null;
  confidence?: number | null;
  answeredBy?: string;
  providerError?: string | null;
  escalated?: boolean;
  escalationReason?: string | null;
  tools?: Array<{ toolId: string; success: boolean }>;
  latencyMs?: number;
  trace?: AgentTraceStep[];
}

const traceTone: Record<AgentTraceStep['status'], StatusLevel> = {
  ok: 'ok',
  warn: 'warn',
  error: 'error',
  skipped: 'unknown',
};

function answeredByTone(answeredBy: string | undefined, providerError: string | null | undefined): StatusLevel {
  if (providerError) return 'error';
  if (answeredBy === 'provider') return 'ok';
  if (answeredBy === 'local') return 'ok';
  if (answeredBy === 'offline-engine') return 'warn';
  return 'unknown';
}

function answeredByLabel(message: ChatMessage): string {
  if (message.providerError) return 'provider unreachable';
  if (message.answeredBy === 'provider') return `answered by ${message.model ?? 'provider'}`;
  if (message.answeredBy === 'offline-engine') return 'offline engine';
  if (message.answeredBy === 'local') return 'local model';
  return 'unknown';
}

function MessageBubble({
  message,
  showTrace,
  onSpeak,
  canSpeak,
  onOpenSpecialist,
}: {
  message: ChatMessage;
  showTrace: boolean;
  onSpeak: () => void;
  canSpeak: boolean;
  onOpenSpecialist: (target: NavTarget) => void;
}) {
  if (message.role === 'system') {
    return (
      <div className="glass-subtle px-4 py-3 text-xs leading-relaxed text-slate-400">{message.content}</div>
    );
  }
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-br-md border border-accent-400/25 bg-accent-500/[0.13] px-4 py-2.5 text-sm leading-relaxed text-slate-100 shadow-rise">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2 animate-fade-in">
      <div className="glass max-w-[92%] px-4 py-3.5">
        <div className="mb-2 flex flex-wrap items-center gap-2 text-2xs">
          <Badge tone="unknown">
            <StatusDot level="ok" />
            {message.specialistName ?? 'Specialist'}
          </Badge>
          {message.versionLabel && <Badge tone="ok">{message.versionLabel}</Badge>}
          {!message.versionLabel && <Badge tone="unknown">untrained</Badge>}
          <Badge tone={answeredByTone(message.answeredBy, message.providerError)}>{answeredByLabel(message)}</Badge>
          {message.predicted && (
            <span className="font-mono text-slate-500">
              {message.predicted}
              {typeof message.confidence === 'number' ? ` @ ${(message.confidence * 100).toFixed(1)}%` : ''}
            </span>
          )}
          {message.tools?.map((tool) => (
            <span key={tool.toolId} className={`font-mono ${tool.success ? 'text-slate-500' : 'text-signal-warn'}`}>
              {tool.toolId}
              {tool.success ? '' : ' failed'}
            </span>
          ))}
          {typeof message.latencyMs === 'number' && <span className="text-slate-600">{message.latencyMs.toFixed(1)} ms</span>}
        </div>

        <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-slate-200">{message.content}</pre>

        {message.providerError && (
          <p className="mt-2 rounded-lg border border-signal-error/25 bg-signal-error/[0.08] px-3 py-2 text-2xs leading-relaxed text-signal-error">
            The external provider could not be reached ({message.providerError}), so no model answered this request.
          </p>
        )}
        {message.escalated && message.escalationReason && !message.providerError && (
          <p className="mt-2 text-2xs leading-relaxed text-slate-500">Escalated: {message.escalationReason}</p>
        )}

        <div className="mt-3 flex items-center gap-3 text-2xs text-slate-500">
          {canSpeak && (
            <button type="button" onClick={onSpeak} className="hover:text-accent-300">
              speak
            </button>
          )}
          {message.specialistId && (
            <button
              type="button"
              onClick={() => onOpenSpecialist({ view: 'specialist', specialistId: message.specialistId })}
              className="hover:text-accent-300"
            >
              open workspace
            </button>
          )}
          <span className="font-mono">{message.engine}</span>
        </div>
      </div>

      {showTrace && message.trace && message.trace.length > 0 && (
        <div className="ml-4 space-y-1 border-l border-white/[0.07] pl-3">
          {message.trace.map((step, index) => (
            <div key={`${message.id}_${index}`} className="flex items-start gap-2 text-2xs">
              <span className="mt-1">
                <StatusDot level={traceTone[step.status]} />
              </span>
              <span className="font-mono text-slate-500">{step.step}</span>
              <span className={`flex-1 ${step.status === 'error' ? 'text-signal-error' : step.status === 'warn' ? 'text-signal-warn' : 'text-slate-400'}`}>
                {step.detail}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const SUGGESTIONS = [
  'what is 128 * 4',
  'read package.json',
  'remember that the staging database is Postgres 16',
  'find our note about promotion gates',
  'summarise https://arxiv.org/abs/1706.03762',
];

export function AssistantView({ onOpen }: { onOpen: (target: NavTarget) => void }) {
  const { settings, connection, specialists, refresh, refreshPlatform } = useSystem();
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'system',
      content:
        'AgentOS is ready. With auto-routing on, your message goes to the specialist that matches the intent. Pin one below to keep every request in the same place. Replies state which mechanism answered them — a trained local model, the built-in rule policy, the offline engine, or an external provider.',
      timestamp: new Date().toISOString(),
    },
  ]);
  const [input, setInput] = useState('');
  const [target, setTarget] = useState<string>('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [speakReplies, setSpeakReplies] = useState(false);
  const [showTrace, setShowTrace] = useState(true);
  const speech = useSpeech();
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = listRef.current;
    if (!el || typeof el.scrollTo !== 'function') return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages, sending]);

  useEffect(() => {
    if (speech.transcript) setInput(speech.transcript);
  }, [speech.transcript]);

  const pinned = useMemo(() => specialists.find((specialist) => specialist.id === target) ?? null, [specialists, target]);

  async function send(textOverride?: string) {
    const text = (textOverride ?? input).trim();
    if (!text || sending) return;
    setError(null);
    setSending(true);
    setMessages((prev) => [
      ...prev,
      { id: `local_${Date.now()}`, role: 'user', content: text, timestamp: new Date().toISOString() },
    ]);
    setInput('');
    speech.stopListening();
    try {
      const response = await api.chat({
        message: text,
        conversationId,
        // The platform contract takes a specialist id; legacy capability ids are
        // still accepted by the backend but the UI speaks specialist ids only.
        specialistId: target || undefined,
      });
      setConversationId(response.conversationId);
      const meta = (response.message.meta ?? {}) as Record<string, unknown>;
      setMessages((prev) => [
        ...prev,
        {
          id: response.message.id,
          role: 'assistant',
          content: response.message.content,
          timestamp: response.message.timestamp,
          specialistId: meta.specialistId as string | undefined,
          specialistName: meta.specialistName as string | undefined,
          engine: meta.engine as string | undefined,
          model: meta.model as string | undefined,
          versionLabel: (meta.versionLabel as string | null | undefined) ?? null,
          predicted: (meta.predicted as string | null | undefined) ?? null,
          confidence: (meta.confidence as number | null | undefined) ?? null,
          answeredBy: meta.answeredBy as string | undefined,
          providerError: (meta.providerError as string | null | undefined) ?? null,
          escalated: Boolean(meta.escalated),
          escalationReason: (meta.escalationReason as string | null | undefined) ?? null,
          tools: (meta.tools as Array<{ toolId: string; success: boolean }> | undefined) ?? [],
          latencyMs: meta.latencyMs as number | undefined,
          trace: response.trace,
        },
      ]);
      if (speakReplies) speech.speak(response.message.content);
      void refreshPlatform();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }

  const sttAllowed = Boolean(settings?.voice.sttEnabled) && speech.sttSupported;
  const ttsAllowed = Boolean(settings?.voice.ttsEnabled) && speech.ttsSupported;
  const cancelSpeech = speech.cancelSpeech;
  useEffect(() => {
    if (!ttsAllowed && speakReplies) {
      setSpeakReplies(false);
      cancelSpeech();
    }
  }, [ttsAllowed, speakReplies, cancelSpeech]);

  const routingHint = target
    ? `${pinned?.name ?? target} answers every message in this conversation`
    : settings?.routing.autoRoute === false
      ? 'Auto-routing is off — General AI takes everything until you pin a specialist'
      : 'Auto-routing on — matched by intent, and the specialist is named in each reply';

  return (
    <div className="flex h-full min-h-0 gap-5">
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        {connection === 'offline' && (
          <ErrorBanner
            message="The AgentOS backend is unreachable. Start it with `npm run dev` and press Retry."
            onRetry={() => void refresh()}
          />
        )}
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

        <Card className="flex min-h-0 flex-1 flex-col !p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.05] px-5 py-3">
            <div className="flex items-center gap-3 text-xs text-slate-400">
              <StatusBadge level={connection === 'online' ? 'ok' : 'error'} label={connection === 'online' ? 'Connected' : 'Offline'} />
              <span className="hidden sm:inline">{routingHint}</span>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5 accent-accent-500"
                  checked={showTrace}
                  onChange={(e) => setShowTrace(e.target.checked)}
                />
                trace
              </label>
              <label
                className="flex cursor-pointer items-center gap-2"
                title={ttsAllowed ? 'Speak replies aloud' : 'Text-to-speech is disabled in Settings → Voice, or unavailable in this browser'}
              >
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
            {messages.map((message) => (
              <MessageBubble
                key={message.id}
                message={message}
                showTrace={showTrace}
                canSpeak={speech.ttsSupported}
                onSpeak={() => speech.speak(message.content)}
                onOpenSpecialist={onOpen}
              />
            ))}
            {sending && (
              <div className="flex items-center gap-3">
                <Spinner label="Routing and answering…" />
              </div>
            )}
            {messages.length <= 1 && (
              <div className="space-y-3">
                <EmptyState
                  title="Start a conversation"
                  description="Ask in plain language. AgentOS decides which specialist and which tool resolves it, then tells you what answered."
                />
                <div className="flex flex-wrap gap-2">
                  {SUGGESTIONS.map((suggestion) => (
                    <Chip key={suggestion} onClick={() => void send(suggestion)}>
                      {suggestion}
                    </Chip>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="border-t border-white/[0.05] px-5 py-4">
            <div className="flex items-end gap-3">
              <textarea
                className="input min-h-[46px] flex-1 resize-none"
                rows={2}
                value={input}
                placeholder={pinned ? `Message ${pinned.name}…` : 'Ask AgentOS anything…'}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    void send();
                  }
                }}
                aria-label="Message"
              />
              <div className="flex flex-col gap-2">
                <button type="button" className="btn-primary" onClick={() => void send()} disabled={sending || input.trim().length === 0}>
                  {sending ? 'Sending…' : 'Send'}
                </button>
                {sttAllowed && (
                  <button
                    type="button"
                    className={`btn-ghost ${speech.listening ? 'text-signal-ok' : ''}`}
                    onClick={() => (speech.listening ? speech.stopListening() : speech.startListening())}
                    title={speech.listening ? 'Stop dictation' : 'Dictate a message'}
                  >
                    {speech.listening ? 'Stop' : 'Dictate'}
                  </button>
                )}
              </div>
            </div>
            <p className="mt-2 text-2xs text-slate-500">
              Enter sends · Shift+Enter adds a line ·{' '}
              {settings?.routing.preferTrainedHeads
                ? `trained heads preferred, escalate below ${(settings.routing.confidenceThreshold * 100).toFixed(0)}% confidence`
                : 'trained heads disabled in Settings → Routing'}
            </p>
          </div>
        </Card>
      </div>

      <aside className="hidden w-72 shrink-0 flex-col gap-4 overflow-y-auto xl:flex">
        <Card title="Who answers this" subtitle="Pin a specialist or leave routing to AgentOS.">
          <Select
            ariaLabel="Specialist for this conversation"
            value={target}
            onChange={setTarget}
            options={[
              { value: '', label: settings?.routing.autoRoute === false ? 'Auto (General AI — routing off)' : 'Auto-route by intent' },
              ...specialists.map((specialist) => ({
                value: specialist.id,
                label: `${specialist.shortName || specialist.name}${specialist.activeVersion ? ` · ${specialist.activeVersion.label}` : ' · untrained'}`,
              })),
            ]}
          />
          {pinned ? (
            <div className="mt-3 space-y-2 text-2xs text-slate-400">
              <p className="leading-relaxed">{pinned.stateDetail}</p>
              <div className="glass-subtle px-3 py-2">
                <p className="flex items-center justify-between">
                  <span>Local model</span>
                  <span className="font-mono text-slate-200">{pinned.activeVersion?.label ?? 'untrained'}</span>
                </p>
                <p className="mt-1 flex items-center justify-between">
                  <span>Benchmark</span>
                  <span className="font-mono text-slate-200">
                    {pinned.benchmarkScore === null ? 'not measured' : `${(pinned.benchmarkScore * 100).toFixed(1)}%`}
                  </span>
                </p>
                <p className="mt-1 flex items-center justify-between">
                  <span>Tools</span>
                  <span className="text-slate-200">{pinned.toolIds.length}</span>
                </p>
              </div>
              <button
                type="button"
                className="btn-ghost w-full"
                onClick={() => onOpen({ view: 'specialist', specialistId: pinned.id })}
              >
                Open workspace
              </button>
            </div>
          ) : (
            <p className="mt-3 text-2xs leading-relaxed text-slate-500">
              {specialists.length} specialist{specialists.length === 1 ? '' : 's'} available. Routing uses intent keywords, then the
              specialist's trained model decides which tool to call.
            </p>
          )}
        </Card>

        <Card title="Escalation chain" subtitle="How a request is resolved, in order.">
          <ol className="space-y-2 text-2xs leading-relaxed text-slate-400">
            <li className="glass-subtle px-3 py-2">
              <span className="text-slate-200">1 · Trained local model</span> — a task head or retrieval index answers with a measured
              confidence.
            </li>
            <li className="glass-subtle px-3 py-2">
              <span className="text-slate-200">2 · Rule policy</span> — deterministic planner,{' '}
              {settings?.routing.allowRuleFallback ? 'enabled' : 'disabled in Settings → Routing'}. It reports a fixed confidence, not a
              model score.
            </li>
            <li className="glass-subtle px-3 py-2">
              <span className="text-slate-200">3 · Model provider</span> — only if one is reachable; otherwise the offline engine answers
              and the reply says so.
            </li>
          </ol>
        </Card>

        <Card title="Composer options">
          <Toggle
            label="Prefer trained heads"
            description="Uses the routing setting. When off, every request escalates."
            checked={Boolean(settings?.routing.preferTrainedHeads)}
            onChange={() => onOpen({ view: 'settings' })}
          />
          <Toggle
            label="Allow rule fallback"
            description="Lets the deterministic planner answer when the model is unsure."
            checked={Boolean(settings?.routing.allowRuleFallback)}
            onChange={() => onOpen({ view: 'settings' })}
          />
        </Card>
      </aside>
    </div>
  );
}
