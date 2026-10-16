import { ActivityEntry } from '../shared/types.js';

type Level = 'debug' | 'info' | 'warn' | 'error';
const ORDER: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };

export class Logger {
  // Startup default; overridden by settings.json once it is loaded.
  private level: Level = ((): Level => {
    const env = process.env.AGENTOS_LOG_LEVEL as Level | undefined;
    return env && ['debug', 'info', 'warn', 'error'].includes(env) ? env : 'info';
  })();
  private buffer: ActivityEntry[] = [];
  private max = 500;
  private listeners: Array<(e: ActivityEntry) => void> = [];

  setLevel(level: Level) {
    this.level = level;
  }

  getLevel() {
    return this.level;
  }

  log(level: Level, source: string, message: string, meta?: Record<string, unknown>) {
    if (ORDER[level] < ORDER[this.level]) return;
    const entry: ActivityEntry = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      timestamp: new Date().toISOString(),
      level,
      source,
      message,
      meta: this.sanitize(meta),
    };
    this.buffer.push(entry);
    if (this.buffer.length > this.max) this.buffer.shift();
    for (const l of this.listeners) l(entry);
    // console mirror
    const fn = level === 'error' ? console.error : level === 'warn' ? console.warn : console.log;
    fn(`[${level.toUpperCase()}] ${source}: ${message}`);
  }

  debug(source: string, message: string, meta?: Record<string, unknown>) {
    this.log('debug', source, message, meta);
  }
  info(source: string, message: string, meta?: Record<string, unknown>) {
    this.log('info', source, message, meta);
  }
  warn(source: string, message: string, meta?: Record<string, unknown>) {
    this.log('warn', source, message, meta);
  }
  error(source: string, message: string, meta?: Record<string, unknown>) {
    this.log('error', source, message, meta);
  }

  recent(limit = 100): ActivityEntry[] {
    return this.buffer.slice(-limit);
  }

  subscribe(cb: (e: ActivityEntry) => void) {
    this.listeners.push(cb);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== cb);
    };
  }

  private sanitize(meta?: Record<string, unknown>) {
    if (!meta) return undefined;
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(meta)) {
      if (/key|token|secret|password/i.test(k)) {
        out[k] = '[redacted]';
      } else if (typeof v === 'string' && v.length > 200) {
        out[k] = v.slice(0, 200) + '…';
      } else {
        out[k] = v;
      }
    }
    return out;
  }
}

export const logger = new Logger();
