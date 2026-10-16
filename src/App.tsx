import React, { useEffect, useMemo, useState } from 'react';
import { Sidebar, type NavTarget } from '@/components/Sidebar';
import { TopBar } from '@/components/TopBar';
import { SystemProvider, useSystem } from '@/state/SystemContext';
import { AssistantView } from '@/views/AssistantView';
import { OverviewView } from '@/views/OverviewView';
import { SpecialistWorkspaceView } from '@/views/SpecialistWorkspaceView';
import { AddAIView } from '@/views/AddAIView';
import { TrainingLabView } from '@/views/TrainingLabView';
import { BenchmarksView } from '@/views/BenchmarksView';
import { ActivityView } from '@/views/ActivityView';
import { SettingsView } from '@/views/SettingsView';
import { ErrorBanner } from '@/components/ui';

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full items-center justify-center p-8">
          <div className="glass max-w-lg p-6 text-sm">
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

const VIEW_TITLES: Partial<Record<NavTarget['view'], { title: string; subtitle?: string }>> = {
  specialist: { title: 'Specialist workspace' },
  assistant: { title: 'AI Assistant' },
  training: { title: 'Training Lab' },
  benchmarks: { title: 'Benchmarks' },
};

function Shell() {
  const [target, setTarget] = useState<NavTarget>({ view: 'overview' });
  const { loading, connection, health, lastError, refresh, dismissError, specialists, trainingActive } = useSystem();

  // Deep links: #/specialist/coding-ai opens that workspace directly, which also
  // makes the views addressable from the activity log and from bookmarks.
  useEffect(() => {
    const applyHash = () => {
      const hash = window.location.hash.replace(/^#\/?/, '');
      if (!hash) return;
      const [view, id] = hash.split('/');
      if (
        ['overview', 'assistant', 'specialist', 'add-ai', 'training', 'benchmarks', 'activity', 'settings'].includes(view)
      ) {
        setTarget({ view: view as NavTarget['view'], specialistId: id });
      }
    };
    applyHash();
    window.addEventListener('hashchange', applyHash);
    return () => window.removeEventListener('hashchange', applyHash);
  }, []);

  function navigate(next: NavTarget) {
    setTarget(next);
    const hash = next.specialistId ? `#/${next.view}/${next.specialistId}` : `#/${next.view}`;
    if (window.location.hash !== hash) window.history.replaceState(null, '', hash);
  }

  const currentSpecialist = target.specialistId ? specialists.find((entry) => entry.id === target.specialistId) : undefined;
  const header = useMemo(() => {
    if (target.view === 'specialist') {
      return {
        title: currentSpecialist ? `${currentSpecialist.name} workspace` : 'Specialist workspace',
        subtitle: currentSpecialist
          ? `${currentSpecialist.tagline} · ${currentSpecialist.activeVersion ? `active model ${currentSpecialist.activeVersion.label}` : 'untrained'}`
          : 'Model, capabilities, training history and a test bench',
      };
    }
    return VIEW_TITLES[target.view] ?? undefined;
  }, [target.view, currentSpecialist]);

  return (
    <div className="flex h-full">
      <Sidebar target={target} onChange={navigate} />
      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar
          target={target}
          title={header?.title}
          subtitle={header?.subtitle}
          onRefresh={() => void refresh()}
        />
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {connection === 'offline' && (
            <div className="mb-4">
              <ErrorBanner
                message={lastError ?? 'The AgentOS backend is unreachable.'}
                onRetry={() => void refresh()}
                onDismiss={dismissError}
              />
            </div>
          )}
          {loading && connection === 'connecting' ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-500">Starting AgentOS…</div>
          ) : target.view === 'overview' ? (
            <OverviewView onOpen={navigate} />
          ) : target.view === 'assistant' ? (
            <AssistantView onOpen={navigate} />
          ) : target.view === 'specialist' ? (
            <SpecialistWorkspaceView specialistId={target.specialistId ?? ''} onOpen={navigate} />
          ) : target.view === 'add-ai' ? (
            <AddAIView onOpen={navigate} />
          ) : target.view === 'training' ? (
            <TrainingLabView onOpen={navigate} />
          ) : target.view === 'benchmarks' ? (
            <BenchmarksView onOpen={navigate} />
          ) : target.view === 'activity' ? (
            <ActivityView />
          ) : (
            <SettingsView />
          )}
        </div>
        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.05] bg-base-950/50 px-6 py-2 text-2xs text-slate-500 backdrop-blur-xl">
          <span>AgentOS v{health?.version ?? '0.2.0'} · local-first modular runtime</span>
          <span className="flex items-center gap-3">
            {trainingActive && <span className="text-signal-warn">training in progress</span>}
            <span>
              {health
                ? `${health.components.filter((c) => c.status === 'ok').length}/${health.components.length} subsystems nominal`
                : 'awaiting backend health check'}
            </span>
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
