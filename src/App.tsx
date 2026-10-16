import React, { useState } from 'react';
import { Sidebar, type ViewId } from '@/components/Sidebar';
import { TopBar } from '@/components/TopBar';
import { SystemProvider, useSystem } from '@/state/SystemContext';
import { ChatView } from '@/views/ChatView';
import { ModelsView } from '@/views/ModelsView';
import { CapabilitiesView } from '@/views/CapabilitiesView';
import { ActivityView } from '@/views/ActivityView';
import { SettingsView } from '@/views/SettingsView';

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full items-center justify-center p-8">
          <div className="glass-panel max-w-lg p-6 text-sm">
            <h2 className="text-base font-semibold text-signal-error">The interface hit an unexpected error</h2>
            <p className="mt-2 text-slate-400">{this.state.error.message}</p>
            <button type="button" className="btn-primary mt-4 text-xs" onClick={() => this.setState({ error: null })}>
              Try again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

function Shell() {
  const [view, setView] = useState<ViewId>('chat');
  const { loading, connection, health } = useSystem();

  return (
    <div className="flex h-full">
      <Sidebar view={view} onChange={setView} />
      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar view={view} />
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {loading && connection === 'connecting' ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-500">Starting AgentOS…</div>
          ) : view === 'chat' ? (
            <ChatView />
          ) : view === 'models' ? (
            <ModelsView onOpenSettings={() => setView('settings')} />
          ) : view === 'capabilities' ? (
            <CapabilitiesView />
          ) : view === 'activity' ? (
            <ActivityView />
          ) : (
            <SettingsView />
          )}
        </div>
        <footer className="flex items-center justify-between border-t border-white/[0.05] bg-base-900/40 px-6 py-2 text-[11px] text-slate-500">
          <span>
            AgentOS v{health?.version ?? '0.1.0'} · local-first modular runtime
          </span>
          <span>
            {health
              ? `${health.components.filter((c) => c.status === 'ok').length}/${health.components.length} subsystems nominal`
              : 'awaiting backend health check'}
          </span>
        </footer>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <SystemProvider>
        <Shell />
      </SystemProvider>
    </ErrorBoundary>
  );
}
