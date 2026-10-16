import express, { Express } from 'express';
import cors from 'cors';
import path from 'node:path';
import fs from 'node:fs';
import { createApiRouter, errorHandler, notFoundHandler } from './routes/api.js';
import { logger } from './logger.js';

const ALLOWED_ORIGINS = (process.env.AGENTOS_ALLOWED_ORIGINS ?? '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

export function createApp(): Express {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '1mb' }));

  // In development the Vite dev server proxies /api, so same-origin requests
  // arrive without an Origin header. Browser clients are additionally restricted
  // to explicitly allowed origins.
  app.use(
    cors({
      origin(origin, callback) {
        if (!origin) return callback(null, true);
        const isLocalhost = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
        const isPreviewHost = /^https:\/\/[\w-]+\.e2b\.app$/.test(origin);
        if (isLocalhost || isPreviewHost || ALLOWED_ORIGINS.includes(origin)) {
          return callback(null, true);
        }
        logger.warn('cors', `Blocked origin ${origin}`);
        return callback(null, false);
      },
      credentials: false,
    }),
  );

  // Lightweight request log — never logs request bodies, so no secrets are recorded.
  app.use((req, _res, next) => {
    if (req.path.startsWith('/api')) logger.debug('http', `${req.method} ${req.path}`);
    next();
  });

  app.use('/api', createApiRouter());

  // Serve the production build when it exists.
  const distDir = path.join(process.cwd(), 'dist');
  if (fs.existsSync(path.join(distDir, 'index.html'))) {
    app.use(express.static(distDir));
    app.get(/^(?!\/api).*/, (_req, res) => res.sendFile(path.join(distDir, 'index.html')));
  } else {
    app.get('/', (_req, res) => {
      res
        .status(200)
        .type('text/plain')
        .send('AgentOS API server is running. Run `npm run dev` for the desktop UI, or `npm run build && npm start` for a production bundle.');
    });
  }

  app.use(notFoundHandler);
  app.use(errorHandler);
  return app;
}
