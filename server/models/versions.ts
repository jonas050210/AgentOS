import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { ModelVersionSummary, TrainingMethodId, VersionStatus } from '../../shared/types.js';
import { logger } from '../logger.js';

/**
 * Model version store.
 *
 * Every training run produces a version: the artefact that was learned plus the
 * metrics it achieved. Versions are immutable and individually promotable, so a
 * worse model can never silently replace a better one — it stays on disk as
 * evidence and can be inspected at any time.
 */

export type ArtifactKind = 'task_head_sgd' | 'retrieval_index' | 'lexicon_induction';

export interface VersionIndex {
  specialistId: string;
  activeVersionId: string | null;
  versions: ModelVersionSummary[];
}

export interface CreateVersionInput {
  specialistId: string;
  methodId: TrainingMethodId;
  methodName: string;
  artifactKind: ArtifactKind;
  artifact: unknown;
  metrics: Record<string, number>;
  primaryMetric: string | null;
  runId: string | null;
  datasetId: string | null;
  datasetSize: number | null;
  notes: string;
  baseModel: string;
  /** Optional human label; auto-generated (`v3`) when omitted. */
  label?: string;
}

export interface PromotionResult {
  promoted: ModelVersionSummary;
  previous: ModelVersionSummary | null;
}

function dataDir(): string {
  return process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data');
}

function modelsRoot(): string {
  return path.join(dataDir(), 'models');
}

function specialistDir(specialistId: string): string {
  return path.join(modelsRoot(), specialistId);
}

function artifactPath(specialistId: string, versionId: string): string {
  return path.join(specialistDir(specialistId), `${versionId.split('@')[1] ?? versionId}.json`);
}

function checksumOf(artifact: unknown): string {
  return crypto.createHash('sha256').update(JSON.stringify(artifact)).digest('hex').slice(0, 16);
}

function sizeOf(artifact: unknown): number {
  return Buffer.byteLength(JSON.stringify(artifact), 'utf-8');
}

export class VersionStore {
  private indexes = new Map<string, VersionIndex>();

  constructor(private readonly root: string = modelsRoot()) {}

  private dir(specialistId: string): string {
    return path.join(this.root, specialistId);
  }

  private readIndex(specialistId: string): VersionIndex {
    const cached = this.indexes.get(specialistId);
    if (cached) return cached;
    const empty: VersionIndex = { specialistId, activeVersionId: null, versions: [] };
    try {
      const file = path.join(this.dir(specialistId), 'index.json');
      if (!fs.existsSync(file)) {
        this.indexes.set(specialistId, empty);
        return empty;
      }
      const parsed = JSON.parse(fs.readFileSync(file, 'utf-8')) as VersionIndex;
      const index: VersionIndex = {
        specialistId,
        activeVersionId: parsed.activeVersionId ?? null,
        versions: Array.isArray(parsed.versions) ? parsed.versions : [],
      };
      this.indexes.set(specialistId, index);
      return index;
    } catch (e) {
      logger.warn('versions', `Could not read version index for ${specialistId}: ${String(e)}`);
      this.indexes.set(specialistId, empty);
      return empty;
    }
  }

  private writeIndex(index: VersionIndex): void {
    this.indexes.set(index.specialistId, index);
    try {
      fs.mkdirSync(this.dir(index.specialistId), { recursive: true });
      fs.writeFileSync(path.join(this.dir(index.specialistId), 'index.json'), JSON.stringify(index, null, 1), 'utf-8');
    } catch (e) {
      logger.error('versions', `Could not persist version index for ${index.specialistId}: ${String(e)}`);
    }
  }

  list(specialistId: string): ModelVersionSummary[] {
    return [...this.readIndex(specialistId).versions].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  /** Every version of every specialist, newest first. */
  listAll(): ModelVersionSummary[] {
    return this.listSpecialistIds()
      .flatMap((specialistId) => this.list(specialistId))
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  listSpecialistIds(): string[] {
    try {
      if (!fs.existsSync(this.root)) return [];
      return fs.readdirSync(this.root, { withFileTypes: true }).filter((entry) => entry.isDirectory()).map((entry) => entry.name);
    } catch {
      return [];
    }
  }

  get(versionId: string): ModelVersionSummary | null {
    const specialistId = versionId.split('@')[0];
    return this.readIndex(specialistId).versions.find((version) => version.id === versionId) ?? null;
  }

  activeVersionId(specialistId: string): string | null {
    return this.readIndex(specialistId).activeVersionId;
  }

  activeVersion(specialistId: string): ModelVersionSummary | null {
    const id = this.activeVersionId(specialistId);
    return id ? (this.get(id) ?? null) : null;
  }

  artifact(versionId: string): unknown | null {
    const summary = this.get(versionId);
    if (!summary) return null;
    const file = path.join(this.dir(summary.specialistId), `${versionId.split('@')[1]}.json`);
    try {
      if (!fs.existsSync(file)) return null;
      return JSON.parse(fs.readFileSync(file, 'utf-8'));
    } catch (e) {
      logger.error('versions', `Could not read artefact ${versionId}: ${String(e)}`);
      return null;
    }
  }

  create(input: CreateVersionInput): ModelVersionSummary {
    const index = this.readIndex(input.specialistId);
    const nextNumber = index.versions.length + 1;
    const versionId = `${input.specialistId}@v${nextNumber}`;
    const summary: ModelVersionSummary = {
      id: versionId,
      specialistId: input.specialistId,
      label: input.label ?? `v${nextNumber}`,
      methodId: input.methodId,
      methodName: input.methodName,
      artifactKind: input.artifactKind,
      status: 'candidate',
      createdAt: new Date().toISOString(),
      runId: input.runId,
      metrics: input.metrics,
      primaryMetric: input.primaryMetric,
      sizeBytes: sizeOf(input.artifact),
      datasetId: input.datasetId,
      datasetSize: input.datasetSize,
      notes: input.notes,
      baseModel: input.baseModel,
      checksum: checksumOf(input.artifact),
    };
    try {
      fs.mkdirSync(this.dir(input.specialistId), { recursive: true });
      fs.writeFileSync(artifactPath(input.specialistId, versionId), JSON.stringify(input.artifact), 'utf-8');
    } catch (e) {
      logger.error('versions', `Could not persist artefact ${versionId}: ${String(e)}`);
    }
    this.writeIndex({ ...index, versions: [summary, ...index.versions] });
    logger.info('versions', `Created ${versionId} (${summary.artifactKind}, ${(summary.sizeBytes / 1024).toFixed(1)} kB)`, {
      metrics: summary.metrics,
    });
    return summary;
  }

  setStatus(versionId: string, status: VersionStatus, note?: string): ModelVersionSummary | null {
    const index = this.readIndex(versionId.split('@')[0]);
    const target = index.versions.find((version) => version.id === versionId);
    if (!target) return null;
    target.status = status;
    if (note !== undefined) target.notes = note;
    this.writeIndex(index);
    return target;
  }

  /**
   * Makes a version active and archives whatever was active before it. Returns
   * the promoted summary; `lastPromotion` keeps the displaced version for callers
   * that need to report the rollback path.
   */
  promote(versionId: string, note?: string): ModelVersionSummary | null {
    const index = this.readIndex(versionId.split('@')[0]);
    const target = index.versions.find((version) => version.id === versionId);
    if (!target) return null;
    const previous = index.activeVersionId ? (index.versions.find((v) => v.id === index.activeVersionId) ?? null) : null;
    if (previous && previous.id !== target.id) previous.status = 'archived';
    target.status = 'active';
    if (note) target.notes = note;
    index.activeVersionId = target.id;
    this.writeIndex(index);
    this.lastPromotion = { promoted: target, previous };
    logger.info('versions', `Promoted ${target.id} (${target.methodName}) for ${index.specialistId}`, {
      previous: previous?.id ?? null,
    });
    return target;
  }

  /** Details of the most recent promotion, including the displaced version. */
  lastPromotion: PromotionResult | null = null;

  /** Same as `promote`, but reports which version was archived. */
  promoteWithPrevious(versionId: string, note?: string): PromotionResult | null {
    const result = this.promote(versionId, note);
    return result ? this.lastPromotion : null;
  }

  /** Removes a version and its artefact. The active version cannot be deleted. */
  remove(versionId: string): boolean {
    const index = this.readIndex(versionId.split('@')[0]);
    const target = index.versions.find((version) => version.id === versionId);
    if (!target || index.activeVersionId === versionId) return false;
    index.versions = index.versions.filter((version) => version.id !== versionId);
    this.writeIndex(index);
    try {
      const file = artifactPath(target.specialistId, versionId);
      if (fs.existsSync(file)) fs.unlinkSync(file);
    } catch (e) {
      logger.warn('versions', `Could not delete artefact ${versionId}: ${String(e)}`);
    }
    return true;
  }

  /** Removes every version of a specialist (used when a custom specialist is deleted). */
  removeAll(specialistId: string): number {
    const index = this.readIndex(specialistId);
    const removed = index.versions.length;
    this.writeIndex({ specialistId, activeVersionId: null, versions: [] });
    try {
      const dir = this.dir(specialistId);
      if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
    } catch (e) {
      logger.warn('versions', `Could not remove versions of ${specialistId}: ${String(e)}`);
    }
    this.indexes.delete(specialistId);
    return removed;
  }

  count(): number {
    return this.listSpecialistIds().reduce((total, id) => total + this.list(id).length, 0);
  }

  totalBytes(): number {
    return this.listSpecialistIds().reduce(
      (total, id) => total + this.list(id).reduce((sum, version) => sum + version.sizeBytes, 0),
      0,
    );
  }
}

export const versionStore = new VersionStore();
