import { Router, Request, Response, NextFunction } from 'express';
import { z } from 'zod';
import { AgentChatRequest, CapabilityInfo, HealthStatus, Settings } from '../../shared/types.js';
import { settingsStore } from '../settingsStore.js';
import { logger } from '../logger.js';
import { providerRegistry } from '../providers/registry.js';
import { getCatalog } from '../models/catalog.js';
import { describeCapabilities } from '../capabilities/registry.js';
import { toolRegistry } from '../tools/registry.js';
import { handleChat, OrchestratorError } from '../orchestration/orchestrator.js';
import { conversationStore } from '../orchestration/conversations.js';
import { memoryStore } from '../memory/store.js';
import { speechStatus } from '../speech/engines.js';

const startedAt = Date.now();
export const APP_VERSION = '0.1.0';

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code = 'BAD_REQUEST') {
    super(message);
    this.name = 'ApiError';
  }
}

const asyncHandler =
  (fn: (req: Request, res: Response) => Promise<unknown>) =>
  (req: Request, res: Response, next: NextFunction) => {
    fn(req, res).catch(next);
  };

// Validation schemas — the API rejects malformed payloads instead of trusting them.
const settingsPatchSchema = z
  .object({
    theme: z.enum(['dark', 'light']).optional(),
    language: z.string().max(20).optional(),
    permissions: z
      .object({
        allowFileRead: z.boolean().optional(),
        allowFileWrite: z.boolean().optional(),
        allowNetwork: z.boolean().optional(),
        allowShell: z.boolean().optional(),
      })
      .optional(),
    providers: z
      .object({
        openai: z
          .object({
            apiKey: z.string().max(400).optional(),
            baseUrl: z.string().max(400).optional(),
            model: z.string().max(120).optional(),
          })
          .optional(),
        ollama: z
          .object({ baseUrl: z.string().max(400).optional(), model: z.string().max(120).optional() })
          .optional(),
      })
      .optional(),
    voice: z
      .object({
        sttEnabled: z.boolean().optional(),
        ttsEnabled: z.boolean().optional(),
        voiceName: z.string().max(120).optional(),
      })
      .optional(),
    logging: z.object({ level: z.enum(['debug', 'info', 'warn', 'error']).optional() }).optional(),
  })
  .strict();

const chatSchema = z.object({
  message: z.string().min(1).max(8000),
  conversationId: z.string().max(80).optional(),
  capabilityId: z.string().max(80).optional(),
});

const toolExecSchema = z.object({
  input: z.record(z.unknown()).default({}),
});

export function createApiRouter(): Router {
  const router = Router();

  // ---------- health ----------
  router.get(
    '/health',
    asyncHandler(async (_req, res) => {
      const providers = await providerRegistry.describe();
      const external = providers.find((p) => p.id !== 'builtin' && p.available);
      const toolCount = toolRegistry.all().length;
      const settings = settingsStore.get();
      const granted = [
        settings.permissions.allowFileRead ? 'read' : null,
        settings.permissions.allowFileWrite ? 'write' : null,
        settings.permissions.allowNetwork ? 'network' : null,
        settings.permissions.allowShell ? 'shell' : null,
      ].filter(Boolean) as string[];

      const health: HealthStatus = {
        status: external ? 'healthy' : 'degraded',
        uptimeSeconds: Math.round((Date.now() - startedAt) / 1000),
        version: APP_VERSION,
        timestamp: new Date().toISOString(),
        components: [
          { id: 'orchestrator', name: 'Agent orchestrator', status: 'ok', detail: 'Router and planner active' },
          {
            id: 'models',
            name: 'Model providers',
            status: external ? 'ok' : 'warn',
            detail: external
              ? `${external.name} reachable`
              : 'No external model reachable — offline engine active',
          },
          {
            id: 'tools',
            name: 'Capability tools',
            status: 'ok',
            detail: `${toolCount} tools registered`,
          },
          {
            id: 'permissions',
            name: 'Permissions',
            status: granted.length === 4 ? 'warn' : 'ok',
            detail: granted.length ? `Granted: ${granted.join(', ')}` : 'All elevated permissions disabled',
          },
          {
            id: 'memory',
            name: 'Memory store',
            status: 'ok',
            detail: `${memoryStore.count()} records`,
          },
          {
            id: 'settings',
            name: 'Settings persistence',
            status: 'ok',
            detail: settingsStore.filePath(),
          },
        ],
      };
      res.json(health);
    }),
  );

  // ---------- providers & models ----------
  router.get(
    '/providers',
    asyncHandler(async (_req, res) => {
      res.json(await providerRegistry.describe());
    }),
  );

  router.get(
    '/models',
    asyncHandler(async (_req, res) => {
      res.json(await getCatalog());
    }),
  );

  // ---------- capabilities ----------
  router.get(
    '/capabilities',
    asyncHandler(async (_req, res) => {
      const settings = settingsStore.get();
      let hasExternal = false;
      try {
        const providers = await providerRegistry.describe();
        hasExternal = providers.some((p) => p.id !== 'builtin' && p.available);
      } catch {
        hasExternal = false;
      }
      const capabilities: CapabilityInfo[] = describeCapabilities(
        settings,
        (toolId) => {
          const tool = toolRegistry.get(toolId);
          return tool ? toolRegistry.isRunnable(tool, settings) : false;
        },
        hasExternal,
      );
      res.json({ capabilities, hasExternalModel: hasExternal });
    }),
  );

  // ---------- tools ----------
  router.get(
    '/tools',
    asyncHandler(async (_req, res) => {
      res.json({ tools: toolRegistry.describe() });
    }),
  );

  router.post(
    '/tools/:id/execute',
    asyncHandler(async (req, res) => {
      const parsed = toolExecSchema.safeParse(req.body ?? {});
      if (!parsed.success) throw new ApiError('Invalid execution payload', 400, 'VALIDATION_ERROR');
      const outcome = await toolRegistry.execute(req.params.id, parsed.data.input);
      res.status(outcome.success ? 200 : 200).json(outcome);
    }),
  );

  // ---------- chat ----------
  router.post(
    '/chat',
    asyncHandler(async (req, res) => {
      const parsed = chatSchema.safeParse(req.body);
      if (!parsed.success) {
        throw new ApiError(
          `Invalid chat request: ${parsed.error.issues.map((i) => `${i.path.join('.')} ${i.message}`).join('; ')}`,
          400,
          'VALIDATION_ERROR',
        );
      }
      const response = await handleChat(parsed.data as AgentChatRequest);
      res.json(response);
    }),
  );

  router.get(
    '/conversations',
    asyncHandler(async (_req, res) => {
      res.json({
        conversations: conversationStore.list().map((c) => ({
          id: c.id,
          title: c.title,
          capabilityId: c.capabilityId,
          createdAt: c.createdAt,
          messageCount: c.messages.length,
        })),
      });
    }),
  );

  router.get(
    '/conversations/:id',
    asyncHandler(async (req, res) => {
      const conv = conversationStore.get(req.params.id);
      if (!conv) throw new ApiError('Conversation not found', 404, 'NOT_FOUND');
      res.json(conv);
    }),
  );

  // ---------- settings ----------
  router.get(
    '/settings',
    asyncHandler(async (_req, res) => {
      const s = settingsStore.get();
      // Never return raw credentials to the client; report whether they exist.
      res.json({
        ...s,
        providers: {
          ...s.providers,
          openai: { ...s.providers.openai, apiKey: '', hasApiKey: Boolean(s.providers.openai.apiKey) },
        },
        settingsPath: settingsStore.filePath(),
      });
    }),
  );

  router.put(
    '/settings',
    asyncHandler(async (req, res) => {
      const parsed = settingsPatchSchema.safeParse(req.body ?? {});
      if (!parsed.success) {
        throw new ApiError(
          `Invalid settings: ${parsed.error.issues.map((i) => `${i.path.join('.')} ${i.message}`).join('; ')}`,
          400,
          'VALIDATION_ERROR',
        );
      }
      const patch = parsed.data;
      const current = settingsStore.get();
      const updated: Partial<Settings> = { ...patch } as Partial<Settings>;
      if (patch.providers) {
        // Merge provider fields with the stored values; an empty apiKey means
        // "leave unchanged" so the browser never has to round-trip the secret.
        const openai = {
          ...current.providers.openai,
          ...patch.providers.openai,
          apiKey:
            patch.providers.openai?.apiKey?.trim() === '' || patch.providers.openai?.apiKey === undefined
              ? current.providers.openai.apiKey
              : patch.providers.openai.apiKey,
        };
        updated.providers = {
          openai,
          ollama: { ...current.providers.ollama, ...patch.providers.ollama },
        };
      }
      const saved = settingsStore.update(updated);
      res.json({
        ...saved,
        providers: {
          ...saved.providers,
          openai: { ...saved.providers.openai, apiKey: '', hasApiKey: Boolean(saved.providers.openai.apiKey) },
        },
        settingsPath: settingsStore.filePath(),
      });
    }),
  );

  router.post(
    '/settings/reset',
    asyncHandler(async (_req, res) => {
      const saved = settingsStore.reset();
      logger.warn('settings', 'Settings reset to defaults');
      res.json({
        ...saved,
        providers: {
          ...saved.providers,
          openai: { ...saved.providers.openai, apiKey: '', hasApiKey: Boolean(saved.providers.openai.apiKey) },
        },
        settingsPath: settingsStore.filePath(),
      });
    }),
  );

  // ---------- activity ----------
  router.get(
    '/activity',
    asyncHandler(async (req, res) => {
      const limit = Math.min(Number(req.query.limit ?? 100) || 100, 500);
      res.json({
        entries: logger.recent(limit),
        level: logger.getLevel(),
      });
    }),
  );

  router.get('/activity/stream', (req, res) => {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.write(`event: ready\ndata: ${JSON.stringify({ level: logger.getLevel() })}\n\n`);
    const unsubscribe = logger.subscribe((entry) => {
      res.write(`event: activity\ndata: ${JSON.stringify(entry)}\n\n`);
    });
    const heartbeat = setInterval(() => res.write(': ping\n\n'), 25_000);
    req.on('close', () => {
      clearInterval(heartbeat);
      unsubscribe();
      res.end();
    });
  });

  // ---------- memory ----------
  router.get(
    '/memory',
    asyncHandler(async (req, res) => {
      const q = typeof req.query.q === 'string' ? req.query.q : '';
      const records = q ? memoryStore.search(q, 50) : memoryStore.list(50);
      res.json({ records, count: memoryStore.count(), file: memoryStore.filePath() });
    }),
  );

  router.post(
    '/memory',
    asyncHandler(async (req, res) => {
      const schema = z.object({ title: z.string().min(1).max(200), content: z.string().min(1).max(20000), tags: z.array(z.string()).optional() });
      const parsed = schema.safeParse(req.body ?? {});
      if (!parsed.success) throw new ApiError('Invalid memory entry', 400, 'VALIDATION_ERROR');
      res.status(201).json(memoryStore.add(parsed.data));
    }),
  );

  router.delete(
    '/memory/:id',
    asyncHandler(async (req, res) => {
      const removed = memoryStore.remove(req.params.id);
      if (!removed) throw new ApiError('Memory entry not found', 404, 'NOT_FOUND');
      res.json({ removed: true });
    }),
  );

  // ---------- speech ----------
  router.get(
    '/speech/engines',
    asyncHandler(async (_req, res) => {
      const s = settingsStore.get();
      res.json({
        engines: await speechStatus(),
        settings: s.voice,
      });
    }),
  );

  return router;
}

export function notFoundHandler(_req: Request, res: Response) {
  res.status(404).json({ error: 'Not found', code: 'NOT_FOUND' });
}

export function errorHandler(err: unknown, _req: Request, res: Response, _next: NextFunction) {
  if (err instanceof ApiError) {
    res.status(err.status).json({ error: err.message, code: err.code });
    return;
  }
  if (err instanceof OrchestratorError) {
    res.status(err.statusCode).json({ error: err.message, code: 'ORCHESTRATOR_ERROR' });
    return;
  }
  const message = err instanceof Error ? err.message : String(err);
  logger.error('api', `Unhandled error: ${message}`);
  res.status(500).json({ error: 'Internal server error', code: 'INTERNAL_ERROR' });
}
