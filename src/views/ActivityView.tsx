import {useMemo, useState} from 'react';
import { useSystem } from '@/state/SystemContext';
import { Badge, Card, EmptyState } from '@/components/ui';
import type { ActivityEntry, StatusLevel } from '@shared/types';

const levelTone: Record<ActivityEntry['level'], StatusLevel> = {
  debug: 'unknown',
  info: 'ok',
  warn: 'warn',
  error: 'error',
};

export function ActivityView() {
  const { activity, refresh, connection } = useSystem();
  const [levelFilter, setLevelFilter] = useState<'all' | ActivityEntry['level']>('all');
  const [query, setQuery] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);

  const filtered = useMemo(() => {
    return activity.filter((e) => {
      if (levelFilter !== 'all' && e.level !== levelFilter) return false;
      if (query && !`${e.source} ${e.message}`.toLowerCase().includes(query.toLowerCase())) return false;
      return true;
    });
  }, [activity, levelFilter, query]);

  const counts = useMemo(() => {
    return activity.reduce(
      (acc, e) => ({ ...acc, [e.level]: (acc[e.level] ?? 0) + 1 }),
      {} as Record<ActivityEntry['level'], number>,
    );
  }, [activity]);

  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-4">
        {(['debug', 'info', 'warn', 'error'] as const).map((lvl) => (
          <Card key={lvl} className="!p-4">
            <p className="text-[11px] uppercase tracking-wider text-slate-500">{lvl}</p>
            <p className="mt-1 text-xl font-semibold text-slate-100">{counts[lvl] ?? 0}</p>
          </Card>
        ))}
      </div>

      <Card
        title="Event stream"
        subtitle={connection === 'online' ? 'Live via server-sent events · newest first' : 'Backend offline — no live stream'}
        actions={
          <div className="flex items-center gap-2">
            <input className="input !w-40 !py-1.5 text-xs" placeholder="Filter text…" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select
              aria-label="Level filter"
              className="input !w-28 !py-1.5 text-xs"
              value={levelFilter}
              onChange={(e) => setLevelFilter(e.target.value as typeof levelFilter)}
            >
              <option value="all">all levels</option>
              <option value="debug">debug</option>
              <option value="info">info</option>
              <option value="warn">warn</option>
              <option value="error">error</option>
            </select>
            <label className="flex items-center gap-1.5 text-xs text-slate-400">
              <input type="checkbox" className="accent-accent-500" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
              follow
            </label>
            <button type="button" className="btn-ghost !py-1.5 text-xs" onClick={() => void refresh()}>
              Reload
            </button>
          </div>
        }
      >
        {filtered.length === 0 ? (
          <EmptyState
            title="No matching entries"
            description="Adjust the filters, or send a chat message to generate activity. Secrets are redacted before entries are stored."
          />
        ) : (
          <ol className={`max-h-[34rem] space-y-1 overflow-y-auto pr-1 ${autoScroll ? 'flex flex-col-reverse' : ''}`}>
            {(autoScroll ? [...filtered].reverse() : filtered).map((e) => (
              <li key={e.id} className="glass-subtle flex items-start gap-3 px-3 py-2 font-mono text-[11px]">
                <span className="w-20 shrink-0 text-slate-500">{new Date(e.timestamp).toLocaleTimeString()}</span>
                <Badge tone={levelTone[e.level]} className="shrink-0">
                  {e.level}
                </Badge>
                <span className="w-32 shrink-0 truncate text-slate-400" title={e.source}>
                  {e.source}
                </span>
                <span className="min-w-0 flex-1 break-words text-slate-300">
                  {e.message}
                  {e.meta && Object.keys(e.meta).length > 0 && (
                    <span className="ml-2 text-slate-500">{JSON.stringify(e.meta)}</span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}
