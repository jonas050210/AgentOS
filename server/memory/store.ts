import fs from 'node:fs';
import path from 'node:path';
import { logger } from '../logger.js';

export interface MemoryRecord {
  id: string;
  title: string;
  content: string;
  tags: string[];
  createdAt: string;
  updatedAt: string;
}

export function defaultMemoryFile(): string {
  const dir = process.env.AGENTOS_DATA_DIR || path.join(process.cwd(), 'data');
  return path.join(dir, 'memory.json');
}
const MAX_RECORDS = 1000;

/** Small persistent knowledge store backing the knowledge-management capability. */
export class MemoryStore {
  private records: MemoryRecord[] = [];

  constructor(file: string = defaultMemoryFile()) {
    this.file = file;
    this.records = this.load();
  }

  private file: string;

  private load(): MemoryRecord[] {
    try {
      if (!fs.existsSync(this.file)) return [];
      const parsed = JSON.parse(fs.readFileSync(this.file, 'utf-8'));
      return Array.isArray(parsed) ? (parsed as MemoryRecord[]) : [];
    } catch (e) {
      logger.warn('memory', 'Could not read memory store — starting empty', { error: String(e) });
      return [];
    }
  }

  private persist() {
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      fs.writeFileSync(this.file, JSON.stringify(this.records, null, 2), 'utf-8');
    } catch (e) {
      logger.error('memory', 'Failed to persist memory store', { error: String(e) });
    }
  }

  list(limit = 100): MemoryRecord[] {
    return this.records.slice(-limit).reverse();
  }

  add(entry: { title: string; content: string; tags?: string[] }): MemoryRecord {
    const now = new Date().toISOString();
    const record: MemoryRecord = {
      id: `mem_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
      title: entry.title.slice(0, 200),
      content: entry.content.slice(0, 20_000),
      tags: (entry.tags ?? []).slice(0, 12),
      createdAt: now,
      updatedAt: now,
    };
    this.records.push(record);
    if (this.records.length > MAX_RECORDS) this.records.splice(0, this.records.length - MAX_RECORDS);
    this.persist();
    return record;
  }

  search(query: string, limit = 20): MemoryRecord[] {
    const q = query.trim().toLowerCase();
    if (!q) return this.list(limit);
    const terms = q.split(/\s+/);
    return this.records
      .map((r) => {
        const haystack = `${r.title} ${r.content} ${r.tags.join(' ')}`.toLowerCase();
        const score = terms.reduce((acc, t) => acc + (haystack.includes(t) ? 1 : 0), 0);
        return { r, score };
      })
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, limit)
      .map((x) => x.r);
  }

  remove(id: string): boolean {
    const before = this.records.length;
    this.records = this.records.filter((r) => r.id !== id);
    if (this.records.length !== before) {
      this.persist();
      return true;
    }
    return false;
  }

  count(): number {
    return this.records.length;
  }

  filePath() {
    return this.file;
  }
}

export const memoryStore = new MemoryStore();
