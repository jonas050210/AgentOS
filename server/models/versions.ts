import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { ModelVersionSummary, TrainingMethodId, VersionStatus } from '../../shared/types.js';
import { logger } from '../logger.js';

export type ArtifactKind = 'task_head_sgd' | 'retrieval_index' | 'lexicon_induction';

export interface StoredVersion {
  summary: ModelVersionSummary;
  artifact: unknown;
}

interface SpecialistIndex {
  specialistId: string;
  activeVersionId: string | null;
  counter: number;
  versions: ModelVersionSummary[];
}

function modelsRoot(): string {
  const dir = process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data');
  return path.join(dir, 'models');
}

function specialistDir(specialistId: string): string {
  return path.join(modelsRoot(), specialistId);
}

function indexPath(specialistId: string): string {
  return path.join(specialistDir(specialistId), 'index.json');
}

function versionPath(specialistId: string, versionId: string): string {
  return path.join(specialistDir(specialistId), `${versionId}.json`);
}

function readIndex(specialistId: string): SpecialistIndex {
  const file = indexPath(specialistId);
  try {
    if (fs.existsSync(file)) {
      const parsed = JSON.parse(fs.readFileSync(file, 'utf-8')) as SpecialistIndex;
      if (Array.isArray(parsed.versions)) {
        return {
          specialistId: parsed.specialistId ?? specialistId,
          activeVersionId: parsed.activeVersionId ?? null,
          counter: parsed.counter ?? parsed.versions.length,
          versions: parsed.versions,
        };
      }
    }
  } catch (e) {
    logger.warn('versions', `Could not read index for ${specialistId}`, { error: String(e) });
  }
  return { specialistId, activeVersionId: null, counter: 0, versions: [] };
}

function writeIndex(index: SpecialistIndex): void {
  const dir = specialistDir(index.specialistId);
  fs.mkdirSync(dir, { recursive: true });
  const file = indexPath(index.specialistId);
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(index, null, 2), 'utf-8');
  fs.renameSync(tmp, file);
}

function checksum(value: unknown): string {
  return crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex').slice(0, 12);
}

const artifactCache = new Map<string, unknown>();

export interface CreateVersionInput {
  specialistId: string;
  label?: string;
  methodId: TrainingMethodId;
  methodName: string;
  artifactKind: ArtifactKind;
  artifact: unknown;
  metrics: Record<string, number>;
  primaryMetric: string;
  runId: string | null;
  datasetId: string | null;
  datasetSize: number | null;
  notes: string;
  baseModel: string;
}

export const versionStore = {
  list(specialistId: string): ModelVersionSummary[] {
    return readIndex(specialistId)
      .versions.slice()
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  },

  listAll(): ModelVersionSummary[] {
    const root = modelsRoot();
    if (!fs.existsSync(root)) return [];
    return fs
      .readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .flatMap((entry) => this.list(entry.name))
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  },

  get(versionId: string): ModelVersionSummary | null {
    const { specialistId } = splitVersionId(versionId);
    if (!specialistId) return null;
    return readIndex(specialistId).versions.find((v) => v.id === versionId) ?? null;
  },

  artifact(versionId: string): unknown | null {
    if (artifactCache.has(versionId)) return artifactCache.get(versionId)!;
    const { specialistId } = splitVersionId(versionId);
    if (!specialistId) return null;
    const file = versionPath(specialistId, versionId);
    try {
      if (!fs.existsSync(file)) return null;
      const stored = JSON.parse(fs.readFileSync(file, 'utf-8')) as StoredVersion;
      artifactCache.set(versionId, stored.artifact);
      return stored.artifact;
    } catch (e) {
      logger.error('versions', `Failed to load artifact ${versionId}`, { error: String(e) });
      return null;
    }
  },

  activeVersionId(specialistId: string): string | null {
    return readIndex(specialistId).activeVersionId;
  },

  activeVersion(specialistId: string): ModelVersionSummary | null {
    const index = readIndex(specialistId);
    if (!index.activeVersionId) return null;
    return index.versions.find((v) => v.id === index.activeVersionId) ?? null;
  },

  create(input: CreateVersionInput): ModelVersionSummary {
    const index = readIndex(input.specialistId);
    index.counter += 1;
    const versionId = `${input.specialistId}@v${index.counter}`;
    const artifactJson = JSON.stringify(input.artifact);
    const summary: ModelVersionSummary = {
      id: versionId,
      specialistId: input.specialistId,
      label: input.label ?? `v${index.counter}`,
      methodId: input.methodId,
      methodName: input.methodName,
      artifactKind: input.artifactKind,
      status: 'candidate',
      createdAt: new Date().toISOString(),
      runId: input.runId,
      metrics: input.metrics,
      primaryMetric: input.primaryMetric,
      sizeBytes: Buffer.byteLength(artifactJson, 'utf-8'),
      datasetId: input.datasetId,
      datasetSize: input.datasetSize,
      notes: input.notes,
      baseModel: input.baseModel,
      checksum: checksum(input.artifact),
    };

    const dir = specialistDir(input.specialistId);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(versionPath(input.specialistId, versionId), JSON.stringify({ summary, artifact: input.artifact }), 'utf-8');

    index.versions.unshift(summary);
    writeIndex(index);
    artifactCache.set(versionId, input.artifact);
    logger.info('versions', `Created ${versionId}`, { method: input.methodId, sizeBytes: summary.sizeBytes });
    return summary;
  },

  setStatus(versionId: string, status: VersionStatus, note?: string): ModelVersionSummary | null {
    const { specialistId } = splitVersionId(versionId);
    if (!specialistId) return null;
    const index = readIndex(specialistId);
    const version = index.versions.find((v) => v.id === versionId);
    if (!version) return null;
    version.status = status;
    if (note) version.notes = note;
    writeIndex(index);
    const file = versionPath(specialistId, versionId);
    if (fs.existsSync(file)) {
      const stored = JSON.parse(fs.readFileSync(file, 'utf-8')) as StoredVersion;
      stored.summary = version;
      fs.writeFileSync(file, JSON.stringify(stored), 'utf-8');
    }
    return version;
  },

  /** Activates a version and archives whichever version was active before. */
  promote(versionId: string, note?: string): ModelVersionSummary | null {
    const { specialistId } = splitVersionId(versionId);
    if (!specialistId) return null;
    const index = readIndex(specialistId);
    const target = index.versions.find((v) => v.id === versionId);
    if (!target) return null;
    for (const version of index.versions) {
      if (version.id === versionId) {
        version.status = 'active';
        if (note) version.notes = note;
      } else if (version.status === 'active') {
        version.status = 'archived';
      }
    }
    index.activeVersionId = versionId;
    writeIndex(index);
    for (const version of [target]) {
      const file = versionPath(specialistId, version.id);
      if (fs.existsSync(file)) {
        const stored = JSON.parse(fs.readFileSync(file, 'utf-8')) as StoredVersion;
        stored.summary = index.versions.find((v) => v.id === version.id)!;
        fs.writeFileSync(file, JSON.stringify(stored), 'utf-8');
      }
    }
    logger.info('versions', `Promoted ${versionId} to active`);
    return index.versions.find((v) => v.id === versionId) ?? null;
  },

  remove(versionId: string): boolean {
    const { specialistId } = splitVersionId(versionId);
    if (!specialistId) return false;
    const index = readIndex(specialistId);
    if (index.activeVersionId === versionId) return false;
    const before = index.versions.length;
    index.versions = index.versions.filter((v) => v.id !== versionId);
    if (index.versions.length === before) return false;
    writeIndex(index);
    const file = versionPath(specialistId, versionId);
    if (fs.existsSync(file)) fs.rmSync(file, { force: true });
    artifactCache.delete(versionId);
    return true;
  },

  /** Total bytes of stored artifacts for a specialist (storage reporting). */
  storageBytes(specialistId: string): number {
    return this.list(specialistId).reduce((total, version) => total + version.sizeBytes, 0);
  },
};

export function splitVersionId(versionId: string): { specialistId: string | null; version: string | null } {
  const at = versionId.lastIndexOf('@');
  if (at <= 0) return { specialistId: null, version: null };
  return { specialistId: versionId.slice(0, at), version: versionId.slice(at + 1) };
}
