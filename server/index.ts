// Loads .env before any module reads configuration from the environment.
import 'dotenv/config';
import { createApp } from './app.js';
import { logger } from './logger.js';
import { settingsStore } from './settingsStore.js';
import { memoryStore } from './memory/store.js';
import { providerRegistry } from './providers/registry.js';

const PORT = Number(process.env.AGENTOS_PORT ?? 4317);
const HOST = process.env.AGENTOS_HOST ?? '0.0.0.0';

async function main() {
  const app = createApp();

  const server = app.listen(PORT, HOST, () => {
    logger.info('server', `AgentOS backend listening on http://${HOST}:${PORT}`);
    logger.info('settings', `Settings file: ${settingsStore.filePath()}`);
    logger.info('memory', `Memory store: ${memoryStore.filePath()} (${memoryStore.count()} records)`);
  });

  server.on('error', (err: NodeJS.ErrnoException) => {
    if (err.code === 'EADDRINUSE') {
      logger.error('server', `Port ${PORT} is already in use. Set AGENTOS_PORT to choose another port.`);
    } else {
      logger.error('server', `Server error: ${err.message}`);
    }
    process.exitCode = 1;
  });

  // Report provider availability once at startup so the log tells the truth about the system state.
  const providers = await providerRegistry.describe().catch(() => []);
  for (const p of providers) {
    logger.info('providers', `${p.name}: ${p.available ? 'available' : 'unavailable'}${p.detail ? ` (${p.detail})` : ''}`);
  }

  const shutdown = (signal: string) => {
    logger.info('server', `Received ${signal}, shutting down`);
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 3000).unref();
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('unhandledRejection', (reason) => {
    logger.error('server', `Unhandled rejection: ${String(reason)}`);
  });
}

main().catch((err) => {
  logger.error('server', `Fatal startup error: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
