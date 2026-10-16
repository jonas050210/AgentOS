import fs from 'node:fs';
import path from 'node:path';
import { CheckpointInfo, TrainingEpoch, TrainingRun, TrainingRunStatus } from '../../shared/types.js';
import { logger } from '../logger.js';

const MAX_RUNS = 120;
const MAX_EPOCHS_PER_RUN = 400;

function dataDir(): string {
  return process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data');
}

function runsFile(): string {
  return path.join(dataDir(), 'training', 'runs.json');
}

/**
 * Training-run registry. Runs are kept in memory for live progress and mirrored
 * to disk (bounded history) so the Training Lab survives a restart.
 */
class TrainingRunStore {
  private runs = new Map<string, TrainingRun>();
  private cancelRequests = new Set<string>();
  private persistTimer: NodeJS.Timeout | null = null;

  constructor() {
    this.load();
  }

  private load(): void {
    try {
      const file = runsFile();
      if (!fs.existsSync(file)) return;
      const parsed = JSON.parse(fs.readFileSync(file, 'utf-8')) as TrainingRun[];
      for (const run of parsed) {
        // A run that was mid-flight when the process stopped cannot be resumed:
        // report it honestly as cancelled instead of pretending it is running.
        if (run.status === 'running' || run.status === 'queued') {
          run.status = 'cancelled';
          run.error = 'Interrupted by a server restart before completion.';
          run.finishedAt = run.finishedAt ?? new Date().toISOString();
        }
        this.runs.set(run.id, run);
      }
      logger.info('training', `Loaded ${parsed.length} training runs from disk`);
    } catch (e) {
      logger.warn('training', 'Could not read training history', { error: String(e) });
    }
  }

  private schedulePersist(): void {
    if (this.persistTimer) return;
    this.persistTimer = setTimeout(() => {
      this.persistTimer = null;
      this.persist();
    }, 500);
    this.persistTimer.unref?.();
  }

  persist(): void {
    try {
      const file = runsFile();
      fs.mkdirSync(path.dirname(file), { recursive: true });
      const runs = [...this.runs.values()]
        .sort((a, b) => (b.startedAt ?? b.id).localeCompare(a.startedAt ?? a.id))
        .slice(0, MAX_RUNS);
      const tmp = `${file}.tmp`;
      fs.writeFileSync(tmp, JSON.stringify(runs, null, 2), 'utf-8');
      fs.renameSync(tmp, file);
    } catch (e) {
      logger.error('training', 'Failed to persist training runs', { error: String(e) });
    }
  }

  nextId(): string {
    return `run_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  }

  create(input: Omit<TrainingRun, 'id' | 'progress' | 'epochs' | 'checkpoints' | 'metrics' | 'promotion' | 'versionId' | 'scorecardId' | 'error' | 'artifactPath' | 'durationMs' | 'finishedAt'>): TrainingRun {
    const run: TrainingRun = {
      ...input,
      id: this.nextId(),
      progress: 0,
      epochs: [],
      checkpoints: [],
      metrics: null,
      promotion: null,
      versionId: null,
      scorecardId: null,
      error: null,
      artifactPath: null,
      durationMs: null,
      finishedAt: null,
    };
    this.runs.set(run.id, run);
    this.schedulePersist();
    return run;
  }

  get(id: string): TrainingRun | undefined {
    return this.runs.get(id);
  }

  list(filter: { specialistId?: string; status?: TrainingRunStatus; limit?: number } = {}): TrainingRun[] {
    const limit = filter.limit ?? 50;
    return [...this.runs.values()]
      .filter((run) => (filter.specialistId ? run.specialistId === filter.specialistId : true))
      .filter((run) => (filter.status ? run.status === filter.status : true))
      .sort((a, b) => (b.startedAt ?? b.id).localeCompare(a.startedAt ?? a.id))
      .slice(0, limit);
  }

  update(id: string, patch: Partial<TrainingRun>): TrainingRun | undefined {
    const run = this.runs.get(id);
    if (!run) return undefined;
    Object.assign(run, patch);
    this.schedulePersist();
    return run;
  }

  addEpoch(id: string, epoch: TrainingEpoch): TrainingRun | undefined {
    const run = this.runs.get(id);
    if (!run) return undefined;
    run.epochs.push(epoch);
    if (run.epochs.length > MAX_EPOCHS_PER_RUN) run.epochs.splice(0, run.epochs.length - MAX_EPOCHS_PER_RUN);
    this.schedulePersist();
    return run;
  }

  addCheckpoint(id: string, checkpoint: CheckpointInfo): TrainingRun | undefined {
    const run = this.runs.get(id);
    if (!run) return undefined;
    for (const existing of run.checkpoints) existing.best = false;
    run.checkpoints.push(checkpoint);
    this.schedulePersist();
    return run;
  }

  requestCancel(id: string): boolean {
    const run = this.runs.get(id);
    if (!run || (run.status !== 'running' && run.status !== 'queued')) return false;
    this.cancelRequests.add(id);
    logger.info('training', `Cancellation requested for ${id}`);
    return true;
  }

  isCancelRequested(id: string): boolean {
    return this.cancelRequests.has(id);
  }

  clearCancel(id: string): void {
    this.cancelRequests.delete(id);
  }

  counts(): { running: number; queued: number; completed: number; failed: number; cancelled: number; total: number } {
    const all = [...this.runs.values()];
    const count = (status: TrainingRunStatus) => all.filter((r) => r.status === status).length;
    return {
      running: count('running'),
      queued: count('queued'),
      completed: count('completed'),
      failed: count('failed'),
      cancelled: count('cancelled'),
      total: all.length,
    };
  }

  /** Removes finished runs (used by the settings danger zone). */
  clearFinished(): number {
    let removed = 0;
    for (const [id, run] of this.runs) {
      if (run.status !== 'running' && run.status !== 'queued') {
        this.runs.delete(id);
        removed++;
      }
    }
    this.persist();
    return removed;
  }
}

export const runStore = new TrainingRunStore();

export function checkpointsDir(runId: string): string {
  return path.join(dataDir(), 'training', runId, 'checkpoints');
}
