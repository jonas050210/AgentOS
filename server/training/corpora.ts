/**
 * Curated seed corpora shipped with AgentOS.
 *
 * These are hand-written, deterministic pools used for BOTH training and
 * benchmarking. `server/training/datasets.ts` derives disjoint train/eval
 * partitions from each pool, so benchmark scores always report generalisation
 * on examples the model never saw — never training-set performance.
 *
 * Provenance is stated explicitly in the UI: these pools are curated by the
 * AgentOS project, not scraped from user data.
 */

export interface RoutingItem {
  text: string;
  /** Expected decision label: a tool id or `answer.direct`. */
  label: string;
}

export interface ExpressionItem {
  text: string;
  /** Expected numeric result as a string. */
  expected: string;
}

export interface ExtractionItem {
  text: string;
  /** Expected `host/path` (no scheme, no trailing slash). */
  expected: string;
}

export interface NormalizationItem {
  text: string;
  expected: string;
}

export interface LexiconItem {
  term: string;
  spoken: string;
  context: string;
}

export interface SeedDoc {
  id: string;
  title: string;
  text: string;
}

export interface SeedQuery {
  query: string;
  relevantIds: string[];
}

// ---------------------------------------------------------------------------
// Coding specialist: which tool should handle this request?
// ---------------------------------------------------------------------------

export const CODING_ROUTING_POOL: RoutingItem[] = [
  { text: 'what is 128 * 4', label: 'calc.evaluate' },
  { text: 'calculate 17 + 26 - 4', label: 'calc.evaluate' },
  { text: 'compute (12 + 8) * 3', label: 'calc.evaluate' },
  { text: 'evaluate 2 ^ 10', label: 'calc.evaluate' },
  { text: 'how much is 144 / 12', label: 'calc.evaluate' },
  { text: 'what is 15 % 4', label: 'calc.evaluate' },
  { text: 'sum 99 + 101', label: 'calc.evaluate' },
  { text: 'calculate the average of 4 and 6 (4 + 6) / 2', label: 'calc.evaluate' },
  { text: 'what is 7 * 8 + 9', label: 'calc.evaluate' },
  { text: 'please compute 1000 / 8', label: 'calc.evaluate' },
  { text: 'read the file server/index.ts', label: 'workspace.read' },
  { text: 'open package.json and show it', label: 'workspace.read' },
  { text: 'cat README.md', label: 'workspace.read' },
  { text: 'show me src/App.tsx', label: 'workspace.read' },
  { text: 'read server/routes/api.ts please', label: 'workspace.read' },
  { text: 'what is inside vite.config.ts', label: 'workspace.read' },
  { text: 'display docs/ARCHITECTURE.md', label: 'workspace.read' },
  { text: 'list the files in this repository', label: 'workspace.list' },
  { text: 'what files are in the workspace', label: 'workspace.list' },
  { text: 'show the directory listing', label: 'workspace.list' },
  { text: 'list everything under src', label: 'workspace.list' },
  { text: 'which files exist in the project root', label: 'workspace.list' },
  { text: 'enumerate the workspace files', label: 'workspace.list' },
  { text: 'how much memory does this machine have', label: 'system.info' },
  { text: 'report the host platform and cpu count', label: 'system.info' },
  { text: 'which node version is running', label: 'system.info' },
  { text: 'give me the system information', label: 'system.info' },
  { text: 'what is the current load average', label: 'system.info' },
  { text: 'tell me about this machine', label: 'system.info' },
  { text: 'search your notes for postgres', label: 'memory.search' },
  { text: 'do you remember anything about deployment', label: 'memory.search' },
  { text: 'look up the stored note about caching', label: 'memory.search' },
  { text: 'what did we save about vite', label: 'memory.search' },
  { text: 'find my note on database migrations', label: 'memory.search' },
  { text: 'explain how the training pipeline works', label: 'answer.direct' },
  { text: 'write a typescript function that debounces', label: 'answer.direct' },
  { text: 'why would this code throw a null pointer', label: 'answer.direct' },
  { text: 'describe the difference between let and const', label: 'answer.direct' },
  { text: 'review my architecture approach', label: 'answer.direct' },
  { text: 'what is a monorepo', label: 'answer.direct' },
  { text: 'explain dependency injection briefly', label: 'answer.direct' },
  { text: 'draft a commit message for a refactor', label: 'answer.direct' },
  { text: 'how should I structure tests', label: 'answer.direct' },
  { text: 'what is the difference between unit and integration tests', label: 'answer.direct' },
  { text: 'suggest a name for this module', label: 'answer.direct' },
  { text: 'what is 250 + 250', label: 'calc.evaluate' },
  { text: 'multiply 36 by 4', label: 'calc.evaluate' },
  { text: 'divide 100 by 8', label: 'calc.evaluate' },
  { text: 'what is 13 * 13', label: 'calc.evaluate' },
  { text: 'how much is 88 - 19', label: 'calc.evaluate' },
  { text: 'evaluate 5 + 6 + 7', label: 'calc.evaluate' },
  { text: 'what is 200 % 7', label: 'calc.evaluate' },
  { text: 'compute 9 ^ 3', label: 'calc.evaluate' },
  { text: 'calculate 64 / 4', label: 'calc.evaluate' },
  { text: 'what is 21 * 2', label: 'calc.evaluate' },
  { text: 'add 128 and 256', label: 'calc.evaluate' },
  { text: 'open src/main.tsx', label: 'workspace.read' },
  { text: 'read tsconfig.json', label: 'workspace.read' },
  { text: 'show me the contents of docs/ARCHITECTURE.md', label: 'workspace.read' },
  { text: 'inspect server/app.ts', label: 'workspace.read' },
  { text: 'read tailwind.config.js', label: 'workspace.read' },
  { text: 'print the file README.md', label: 'workspace.read' },
  { text: 'what is in src/index.css', label: 'workspace.read' },
  { text: 'cat scripts/smoke.mjs', label: 'workspace.read' },
  { text: 'show me vite.config.ts', label: 'workspace.read' },
  { text: 'read server/orchestration/orchestrator.ts', label: 'workspace.read' },
  { text: 'what are the files in the repo', label: 'workspace.list' },
  { text: 'list the src folder', label: 'workspace.list' },
  { text: 'give me a directory overview', label: 'workspace.list' },
  { text: 'show me everything in the project', label: 'workspace.list' },
  { text: 'what folders exist here', label: 'workspace.list' },
  { text: 'enumerate files in the root', label: 'workspace.list' },
  { text: 'list the workspace contents', label: 'workspace.list' },
  { text: 'how many cpus does the host have', label: 'system.info' },
  { text: 'what is the free memory on this box', label: 'system.info' },
  { text: 'describe the runtime environment', label: 'system.info' },
  { text: 'show system information', label: 'system.info' },
  { text: 'which platform am I on', label: 'system.info' },
  { text: 'what is the uptime of the machine', label: 'system.info' },
  { text: 'report memory and load average', label: 'system.info' },
  { text: 'recall the note about migrations', label: 'memory.search' },
  { text: 'do we have notes about caching', label: 'memory.search' },
  { text: 'find my stored note about vite', label: 'memory.search' },
  { text: 'search memory for deployment steps', label: 'memory.search' },
  { text: 'what do you know about our database conventions', label: 'memory.search' },
  { text: 'look up the knowledge base for benchmarks', label: 'memory.search' },
  { text: 'what have you saved about permissions', label: 'memory.search' },
  { text: 'remember that the release is on friday', label: 'memory.save' },
  { text: 'save a note about the migration plan', label: 'memory.save' },
  { text: 'write this down: caching keyed by prompt hash', label: 'memory.save' },
  { text: 'store this for later: postgres 16', label: 'memory.save' },
  { text: 'keep a note that tests run with vitest', label: 'memory.save' },
  { text: 'note that the api port is 4317', label: 'memory.save' },
  { text: 'explain how the retrieval index works', label: 'answer.direct' },
  { text: 'write a debounce helper in typescript', label: 'answer.direct' },
  { text: 'why might this promise reject', label: 'answer.direct' },
  { text: 'compare let and var', label: 'answer.direct' },
  { text: 'review my module boundaries', label: 'answer.direct' },
  { text: 'what is a monorepo setup', label: 'answer.direct' },
  { text: 'explain dependency injection', label: 'answer.direct' },
  { text: 'draft a commit message for a refactor', label: 'answer.direct' },
  { text: 'how should I organise tests', label: 'answer.direct' },
  { text: 'difference between unit and integration tests', label: 'answer.direct' },
  { text: 'suggest a name for this module', label: 'answer.direct' },
  { text: 'walk me through the training pipeline', label: 'answer.direct' },
  { text: 'what does this architecture decision imply', label: 'answer.direct' },
  { text: 'give me a plan to reduce bundle size', label: 'answer.direct' },
  { text: 'how do I structure error handling', label: 'answer.direct' },
  { text: 'what are common causes of memory leaks', label: 'answer.direct' },
  { text: 'explain the tradeoff of caching', label: 'answer.direct' },
  { text: 'summarize the tradeoffs of the design', label: 'answer.direct' },
  { text: 'write documentation for this module', label: 'answer.direct' },
  { text: 'what is the best way to version an api', label: 'answer.direct' },
  { text: 'how would you test this edge case', label: 'answer.direct' },
  { text: 'describe a rollback plan', label: 'answer.direct' },
  { text: 'what is 4 * 25', label: 'calc.evaluate' },
  { text: 'what is 11 ^ 2', label: 'calc.evaluate' },
  { text: 'compute 300 - 175', label: 'calc.evaluate' },
  { text: 'show the file src/App.tsx', label: 'workspace.read' },
  { text: 'read the changelog', label: 'answer.direct' },
  { text: 'what files are in docs', label: 'workspace.list' },
  { text: 'how much ram is free right now', label: 'system.info' },
  { text: 'look up my note about backups', label: 'memory.search' },
  { text: 'remember the deployment checklist', label: 'memory.save' },
  { text: 'explain how the router picks a tool', label: 'answer.direct' },
  { text: 'what is a feature flag', label: 'answer.direct' },
  { text: 'calculate 1024 / 32', label: 'calc.evaluate' },
  { text: 'open the file server/routes/platform.ts', label: 'workspace.read' },
  { text: 'list every file in shared', label: 'workspace.list' },
  { text: 'what is the node runtime version', label: 'system.info' },
  { text: 'recall anything about vector indexes', label: 'memory.search' },
  { text: 'save this thought about latency budgets', label: 'memory.save' },
];

export const CODING_EXPRESSION_POOL: ExpressionItem[] = [
  { text: 'what is 12 * 4', expected: '48' },
  { text: 'calculate 17 + 26', expected: '43' },
  { text: 'compute (12 + 8) * 3', expected: '60' },
  { text: 'evaluate 2 ^ 8', expected: '256' },
  { text: 'how much is 144 / 12', expected: '12' },
  { text: 'what is 15 % 4', expected: '3' },
  { text: 'sum 99 + 101', expected: '200' },
  { text: 'what is 7 * 8 + 9', expected: '65' },
  { text: 'please compute 1000 / 8', expected: '125' },
  { text: 'what is 128 * 4', expected: '512' },
  { text: 'calculate 45 - 17', expected: '28' },
  { text: 'what is 6 ^ 3', expected: '216' },
  { text: 'compute 81 / 9 + 4', expected: '13' },
  { text: 'what is 3 * (7 + 5)', expected: '36' },
  { text: 'evaluate 100 - 42', expected: '58' },
  { text: 'what is 19 + 23 + 8', expected: '50' },
  { text: 'compute 250 * 4', expected: '1000' },
  { text: 'what is 96 / 3', expected: '32' },
  { text: 'calculate 12 ^ 2', expected: '144' },
  { text: 'what is 55 % 7', expected: '6' },
  { text: 'compute 2 * 3 * 4 * 5', expected: '120' },
  { text: 'what is 1000 - 999', expected: '1' },
  { text: 'evaluate 14 * 14', expected: '196' },
  { text: 'what is 72 / 8 + 11', expected: '20' },
  { text: 'compute (100 - 40) / 4', expected: '15' },
  { text: 'what is 33 * 3', expected: '99' },
  { text: 'calculate 5 ^ 4', expected: '625' },
  { text: 'what is 84 / 7', expected: '12' },
  { text: 'evaluate 9 * 9 - 1', expected: '80' },
  { text: 'compute 121 / 11', expected: '11' },
  { text: 'what is 21 * 2', expected: '42' },
  { text: 'calculate 250 + 250', expected: '500' },
  { text: 'compute 36 * 4', expected: '144' },
  { text: 'what is 100 / 8', expected: '12.5' },
  { text: 'evaluate 13 * 13', expected: '169' },
  { text: 'what is 88 - 19', expected: '69' },
  { text: 'compute 5 + 6 + 7', expected: '18' },
  { text: 'what is 200 % 7', expected: '4' },
  { text: 'evaluate 9 ^ 3', expected: '729' },
  { text: 'what is 64 / 4', expected: '16' },
  { text: 'calculate 128 + 256', expected: '384' },
  { text: 'what is 4 * 25', expected: '100' },
  { text: 'compute 11 ^ 2', expected: '121' },
  { text: 'what is 300 - 175', expected: '125' },
  { text: 'evaluate 1024 / 32', expected: '32' },
  { text: 'what is 17 * 3', expected: '51' },
  { text: 'compute 999 + 1', expected: '1000' },
  { text: 'what is 48 / 6', expected: '8' },
  { text: 'calculate 7 ^ 3', expected: '343' },
  { text: 'what is 90 - 45', expected: '45' },
  { text: 'evaluate 6 * 7', expected: '42' },
  { text: 'what is 2 ^ 12', expected: '4096' },
  { text: 'compute 144 / 12 + 3', expected: '15' },
  { text: 'what is 35 % 8', expected: '3' },
  { text: 'calculate 25 * 5', expected: '125' },
  { text: 'what is 81 - 27', expected: '54' },
  { text: 'evaluate 3 * 3 * 3', expected: '27' },
  { text: 'what is 120 / 5', expected: '24' },
  { text: 'compute 47 + 53', expected: '100' },
  { text: 'what is 16 ^ 2', expected: '256' },
];

// ---------------------------------------------------------------------------
// Research specialist
// ---------------------------------------------------------------------------

export const RESEARCH_ROUTING_POOL: RoutingItem[] = [
  { text: 'summarize https://arxiv.org/abs/2401.00001', label: 'extract.url' },
  { text: 'fetch https://example.com/papers/transformers', label: 'extract.url' },
  { text: 'what does https://docs.agentos.dev/training say', label: 'extract.url' },
  { text: 'read https://github.com/jonas050210/AgentOS/issues', label: 'extract.url' },
  { text: 'look at http://localhost:4317/api/health', label: 'extract.url' },
  { text: 'open https://en.wikipedia.org/wiki/Retrieval-augmented_generation', label: 'extract.url' },
  { text: 'get the page https://news.ycombinator.com/item?id=1', label: 'extract.url' },
  { text: 'can you check https://api.github.com/repos/jonas050210/AgentOS', label: 'extract.url' },
  { text: 'extract information from https://standards.ieee.org/standard/754-2019.html', label: 'extract.url' },
  { text: 'summarise https://papers.nips.cc/paper/2023', label: 'extract.url' },
  { text: 'what do we know about retrieval augmentation', label: 'memory.search' },
  { text: 'search the knowledge base for benchmarks', label: 'memory.search' },
  { text: 'do we have notes about evaluation metrics', label: 'memory.search' },
  { text: 'find stored research about prompt tuning', label: 'memory.search' },
  { text: 'what did we record about moe architectures', label: 'memory.search' },
  { text: 'compare bm25 and dense retrieval', label: 'answer.direct' },
  { text: 'explain the difference between recall and precision', label: 'answer.direct' },
  { text: 'write a summary of the current landscape', label: 'answer.direct' },
  { text: 'what are the tradeoffs of fine tuning', label: 'answer.direct' },
  { text: 'draft research questions for evaluation', label: 'answer.direct' },
  { text: 'why does quantization help latency', label: 'answer.direct' },
  { text: 'describe how to measure model drift', label: 'answer.direct' },
  { text: 'explain ngram hashing in one paragraph', label: 'answer.direct' },
  { text: 'look up https://www.nature.com/articles/s41586-021-03819-2', label: 'extract.url' },
  { text: 'check https://blog.cloudflare.com/workers-ai', label: 'extract.url' },
  { text: 'retrieve https://aclanthology.org/2023.acl-long.1/', label: 'extract.url' },
  { text: 'summarise https://openai.com/research/index', label: 'extract.url' },
  { text: 'grab https://huggingface.co/papers/2024', label: 'extract.url' },
  { text: 'read https://developer.mozilla.org/en-US/docs/Web/API', label: 'extract.url' },
  { text: 'see https://www.npmjs.com/package/vitest', label: 'extract.url' },
  { text: 'check https://arxiv.org/list/cs.CL/recent', label: 'extract.url' },
  { text: 'fetch http://127.0.0.1:4318/api/health', label: 'extract.url' },
  { text: 'look at https://bair.berkeley.edu/blog/2024', label: 'extract.url' },
  { text: 'search memory for dataset provenance', label: 'memory.search' },
  { text: 'what do we know about held-out splits', label: 'memory.search' },
  { text: 'find stored notes about recall at k', label: 'memory.search' },
  { text: 'do we have research on quantisation', label: 'memory.search' },
  { text: 'look up our notes on promotion gates', label: 'memory.search' },
  { text: 'what did we save about latency percentiles', label: 'memory.search' },
  { text: 'summarize the differences between sparse and dense search', label: 'answer.direct' },
  { text: 'explain reciprocal rank fusion', label: 'answer.direct' },
  { text: 'draft an evaluation plan for retrieval', label: 'answer.direct' },
  { text: 'what are the limits of lexical matching', label: 'answer.direct' },
  { text: 'compare two adaptation strategies', label: 'answer.direct' },
  { text: 'explain how bm25 trades off length', label: 'answer.direct' },
  { text: 'write a research question about drift', label: 'answer.direct' },
  { text: 'why does quantisation change quality', label: 'answer.direct' },
  { text: 'summarise the state of retrieval research', label: 'answer.direct' },
  { text: 'explain the difference between mrr and recall', label: 'answer.direct' },
  { text: 'collect https://paperswithcode.com/sota', label: 'extract.url' },
  { text: 'what do we know about token routing', label: 'memory.search' },
  { text: 'how do we measure resource usage', label: 'answer.direct' },
  { text: 'outline a literature review structure', label: 'answer.direct' },
];

export const RESEARCH_EXTRACTION_POOL: ExtractionItem[] = [
  { text: 'summarize https://arxiv.org/abs/2401.00001', expected: 'arxiv.org/abs/2401.00001' },
  { text: 'fetch https://example.com/papers/transformers', expected: 'example.com/papers/transformers' },
  { text: 'what does https://docs.agentos.dev/training say', expected: 'docs.agentos.dev/training' },
  { text: 'read https://github.com/jonas050210/AgentOS/issues', expected: 'github.com/jonas050210/AgentOS/issues' },
  { text: 'look at http://localhost:4317/api/health', expected: 'localhost:4317/api/health' },
  { text: 'open https://en.wikipedia.org/wiki/Retrieval-augmented_generation', expected: 'en.wikipedia.org/wiki/Retrieval-augmented_generation' },
  { text: 'get the page https://news.ycombinator.com/item?id=1', expected: 'news.ycombinator.com/item' },
  { text: 'can you check https://api.github.com/repos/jonas050210/AgentOS', expected: 'api.github.com/repos/jonas050210/AgentOS' },
  { text: 'extract information from https://standards.ieee.org/standard/754-2019.html', expected: 'standards.ieee.org/standard/754-2019.html' },
  { text: 'summarise https://papers.nips.cc/paper/2023', expected: 'papers.nips.cc/paper/2023' },
  { text: 'see https://www.nature.com/articles/s41586-021-03819-2', expected: 'nature.com/articles/s41586-021-03819-2' },
  { text: 'check https://blog.cloudflare.com/workers-ai', expected: 'blog.cloudflare.com/workers-ai' },
  { text: 'source: https://aclanthology.org/2023.acl-long.1/', expected: 'aclanthology.org/2023.acl-long.1' },
  { text: 'look up https://www.nature.com/articles/s41586-021-03819-2', expected: 'nature.com/articles/s41586-021-03819-2' },
  { text: 'check https://blog.cloudflare.com/workers-ai', expected: 'blog.cloudflare.com/workers-ai' },
  { text: 'retrieve https://aclanthology.org/2023.acl-long.1/', expected: 'aclanthology.org/2023.acl-long.1' },
  { text: 'summarise https://openai.com/research/index', expected: 'openai.com/research/index' },
  { text: 'grab https://huggingface.co/papers/2024', expected: 'huggingface.co/papers/2024' },
  { text: 'read https://developer.mozilla.org/en-US/docs/Web/API', expected: 'developer.mozilla.org/en-US/docs/Web/API' },
  { text: 'see https://www.npmjs.com/package/vitest', expected: 'npmjs.com/package/vitest' },
  { text: 'check https://arxiv.org/list/cs.CL/recent', expected: 'arxiv.org/list/cs.CL/recent' },
  { text: 'fetch http://127.0.0.1:4318/api/health', expected: '127.0.0.1:4318/api/health' },
  { text: 'look at https://bair.berkeley.edu/blog/2024', expected: 'bair.berkeley.edu/blog/2024' },
  { text: 'collect https://paperswithcode.com/sota', expected: 'paperswithcode.com/sota' },
  { text: 'open https://www.rfc-editor.org/rfc/rfc9110.html', expected: 'rfc-editor.org/rfc/rfc9110.html' },
  { text: 'review https://www.postgresql.org/docs/16/index.html', expected: 'postgresql.org/docs/16/index.html' },
  { text: 'fetch https://tailwindcss.com/docs/configuration', expected: 'tailwindcss.com/docs/configuration' },
  { text: 'study https://vitejs.dev/guide/build.html', expected: 'vitejs.dev/guide/build.html' },
  { text: 'open https://react.dev/reference/react/useMemo', expected: 'react.dev/reference/react/useMemo' },
  { text: 'see https://nodejs.org/api/process.html', expected: 'nodejs.org/api/process.html' },
  { text: 'check https://expressjs.com/en/4x/api.html', expected: 'expressjs.com/en/4x/api.html' },
  { text: 'read https://zod.dev/?id=basic-usage', expected: 'zod.dev' },
  { text: 'look up https://vitest.dev/config/', expected: 'vitest.dev/config' },
  { text: 'summarise https://www.w3.org/TR/wai-aria-1.2/', expected: 'w3.org/TR/wai-aria-1.2' },
  { text: 'grab https://doi.org/10.1145/3292500', expected: 'doi.org/10.1145/3292500' },
];

export const RESEARCH_CORPUS: SeedDoc[] = [
  { id: 'res:bm25', title: 'BM25 ranking function', text: 'BM25 scores documents with term frequency saturation and document length normalisation. It is a sparse lexical retrieval baseline that remains competitive for keyword-heavy queries.' },
  { id: 'res:dense', title: 'Dense retrieval with embeddings', text: 'Dense retrieval encodes queries and documents into a shared vector space and retrieves by cosine similarity, capturing semantic matches that lexical search misses.' },
  { id: 'res:hybrid', title: 'Hybrid retrieval', text: 'Hybrid retrieval fuses lexical and dense scores, typically with reciprocal rank fusion, to combine exact term matching with semantic recall.' },
  { id: 'res:recall', title: 'Recall and precision', text: 'Recall measures how many relevant documents were retrieved; precision measures how many retrieved documents were relevant. Recall@k and MRR are common retrieval metrics.' },
  { id: 'res:lora', title: 'LoRA adapters', text: 'Low-rank adaptation freezes the base model and trains small rank-decomposed matrices, cutting fine-tuning memory while keeping most of the quality.' },
  { id: 'res:peft', title: 'Parameter efficient fine tuning', text: 'PEFT methods such as LoRA, prefix tuning and adapters update a fraction of parameters so a single GPU can specialise a model.' },
  { id: 'res:quant', title: 'Quantisation', text: 'Quantisation stores weights in lower precision such as int8 or 4-bit, reducing memory footprint and increasing throughput at a small quality cost.' },
  { id: 'res:drift', title: 'Model drift', text: 'Model drift describes degradation of predictive performance as the input distribution shifts away from the training distribution; it is detected with periodic evaluation.' },
  { id: 'res:regression', title: 'Regression testing for models', text: 'A regression gate compares a candidate model against the active model on a held-out benchmark and blocks promotion when quality drops beyond a tolerance.' },
  { id: 'res:moe', title: 'Mixture of experts', text: 'Mixture of experts routes each token to a subset of expert networks, increasing capacity without proportionally increasing compute.' },
  { id: 'res:prompttune', title: 'Prompt tuning', text: 'Prompt tuning learns continuous prompt embeddings while leaving model weights frozen, a lightweight adaptation for large frozen models.' },
  { id: 'res:rlhf', title: 'Preference optimisation', text: 'Preference optimisation methods such as RLHF and DPO align a model with human judgments using pairwise comparisons instead of supervised labels.' },
  { id: 'res:eval', title: 'Benchmark design', text: 'Good benchmarks separate train and evaluation data, fix the scoring rule, report variance, and measure latency and resource cost alongside quality.' },
  { id: 'res:latency', title: 'Latency measurement', text: 'Latency should be reported as a distribution: median for typical behaviour and p95 or p99 to expose tail latency that users actually notice.' },
];

export const RESEARCH_QUERIES: SeedQuery[] = [
  { query: 'how does bm25 score documents', relevantIds: ['res:bm25'] },
  { query: 'lexical ranking with term frequency saturation', relevantIds: ['res:bm25'] },
  { query: 'embedding based retrieval', relevantIds: ['res:dense'] },
  { query: 'semantic similarity search', relevantIds: ['res:dense'] },
  { query: 'combine sparse and dense retrieval', relevantIds: ['res:hybrid', 'res:bm25', 'res:dense'] },
  { query: 'recall at k and mrr metrics', relevantIds: ['res:recall'] },
  { query: 'precision versus recall definition', relevantIds: ['res:recall'] },
  { query: 'efficient fine tuning with low rank matrices', relevantIds: ['res:lora'] },
  { query: 'parameter efficient methods', relevantIds: ['res:peft', 'res:lora'] },
  { query: 'int8 and 4 bit weights', relevantIds: ['res:quant'] },
  { query: 'detecting performance degradation over time', relevantIds: ['res:drift', 'res:regression'] },
  { query: 'promotion gate for candidate models', relevantIds: ['res:regression'] },
  { query: 'routing tokens to expert networks', relevantIds: ['res:moe'] },
  { query: 'frozen weights with learned prompts', relevantIds: ['res:prompttune'] },
  { query: 'alignment using pairwise comparisons', relevantIds: ['res:rlhf'] },
  { query: 'how to design an evaluation suite', relevantIds: ['res:eval'] },
  { query: 'p95 tail latency measurement', relevantIds: ['res:latency'] },
  { query: 'measuring the cost of inference', relevantIds: ['res:latency', 'res:quant'] },
];

// ---------------------------------------------------------------------------
// Knowledge specialist
// ---------------------------------------------------------------------------

export const KNOWLEDGE_CORPUS: SeedDoc[] = [
  { id: 'kb:vault', title: 'Operating a local knowledge vault', text: 'AgentOS keeps notes in data/memory.json and note files under data/notes. Notes are indexed by title, body and tags so retrieval works with partial phrases.' },
  { id: 'kb:telemetry', title: 'Telemetry policy', text: 'AgentOS sends no telemetry. Activity entries stay on the local machine and secret-shaped metadata is redacted before it is stored.' },
  { id: 'kb:permissions', title: 'Permission model', text: 'Tools declare read, write, network or execute permissions. Every switch defaults to off except workspace read access, and each tool reports why it is blocked.' },
  { id: 'kb:deployment', title: 'Deployment checklist', text: 'Build with npm run build, start with npm start, set AGENTOS_DATA_DIR outside the repository, and never expose the API port to untrusted networks.' },
  { id: 'kb:postgres', title: 'Database conventions', text: 'Project databases use PostgreSQL 16 with numbered migration files. Migrations are additive; destructive changes require a separate cleanup migration.' },
  { id: 'kb:backup', title: 'Backup strategy', text: 'The data directory is the only stateful location: settings.json, memory.json and notes. Back it up before upgrades; the repository itself is disposable.' },
  { id: 'kb:routing', title: 'Routing preferences', text: 'Local trained heads answer first when their confidence clears the threshold; anything else escalates to the configured provider so quality never silently drops.' },
  { id: 'kb:training', title: 'Training workflow', text: 'Pick a specialist, choose a method and dataset, configure hyperparameters, then review the loss curve and promotion decision before accepting a new version.' },
  { id: 'kb:caching', title: 'Cache invalidation', text: 'Cached provider responses are keyed by model, prompt hash and parameters. Any parameter change invalidates the entry immediately.' },
  { id: 'kb:secrets', title: 'Secret handling', text: 'API keys live in the server-side settings file, are never returned to the browser and are replaced by an hasApiKey flag in API responses.' },
  { id: 'kb:vite', title: 'Frontend tooling', text: 'The UI is React with Vite and Tailwind. The dev server proxies /api to the backend on port 4317 so browser requests stay same-origin.' },
  { id: 'kb:testing', title: 'Test strategy', text: 'Unit and integration tests run with vitest across server and UI, complemented by an end-to-end smoke script that boots the real backend.' },
];

export const KNOWLEDGE_QUERIES: SeedQuery[] = [
  { query: 'where are my notes stored', relevantIds: ['kb:vault'] },
  { query: 'does agentos phone home', relevantIds: ['kb:telemetry'] },
  { query: 'which permissions gate tools', relevantIds: ['kb:permissions'] },
  { query: 'how do I deploy safely', relevantIds: ['kb:deployment', 'kb:permissions'] },
  { query: 'database versions and migrations', relevantIds: ['kb:postgres'] },
  { query: 'what should be backed up', relevantIds: ['kb:backup'] },
  { query: 'when does a request escalate to the provider', relevantIds: ['kb:routing'] },
  { query: 'how do I train a specialist', relevantIds: ['kb:training'] },
  { query: 'cache keys and invalidation', relevantIds: ['kb:caching'] },
  { query: 'api key storage policy', relevantIds: ['kb:secrets'] },
  { query: 'which dev server port proxies the api', relevantIds: ['kb:vite'] },
  { query: 'how are tests organised', relevantIds: ['kb:testing'] },
];

// ---------------------------------------------------------------------------
// Voice specialist
// ---------------------------------------------------------------------------

export const VOICE_LEXICON_POOL: LexiconItem[] = [
  // Each term appears in several sentences. Training learns the expansion from
  // the primary sentence; the extra sentences form the held-out split, so the
  // reported accuracy measures generalisation to unseen contexts.
  { term: 'Dr.', spoken: 'Doctor', context: 'Dr. Müller will review the release notes tomorrow.' },
  { term: 'Dr.', spoken: 'Doctor', context: 'Ask Dr. Weber about the benchmark results.' },
  { term: 'Prof.', spoken: 'Professor', context: 'Prof. Chen teaches the retrieval course in winter.' },
  { term: 'Prof.', spoken: 'Professor', context: 'Prof. Novak published the evaluation study.' },
  { term: 'approx.', spoken: 'approximately', context: 'The run took approx. twelve seconds to finish.' },
  { term: 'approx.', spoken: 'approximately', context: 'approx. half the requests hit the cache.' },
  { term: 'approx.', spoken: 'approximately', context: 'Training needs approx. forty epochs here.' },
  { term: 'e.g.', spoken: 'for example', context: 'Use a sparse index, e.g. BM25, for keyword queries.' },
  { term: 'e.g.', spoken: 'for example', context: 'Prefer cheaper methods, e.g. linear heads.' },
  { term: 'i.e.', spoken: 'that is', context: 'The gate is strict, i.e. no regression is allowed.' },
  { term: 'i.e.', spoken: 'that is', context: 'Keep the artefact, i.e. the trained weights.' },
  { term: 'vs.', spoken: 'versus', context: 'Compare lexical vs. dense retrieval before deciding.' },
  { term: 'vs.', spoken: 'versus', context: 'Head vs. rules is measured every run.' },
  { term: 'etc.', spoken: 'et cetera', context: 'Store weights, biases, metrics, etc. with the run.' },
  { term: 'etc.', spoken: 'et cetera', context: 'Notes, tags, metadata, etc. live on disk.' },
  { term: 'min', spoken: 'minutes', context: 'The training finished in under 3 min on this laptop.' },
  { term: 'min', spoken: 'minutes', context: 'Give it 5 min before judging the rollout.' },
  { term: 'sec', spoken: 'seconds', context: 'Latency p95 stayed below 0.4 sec for the suite.' },
  { term: 'sec', spoken: 'seconds', context: 'Wait 2 sec between probes.' },
  { term: 'no.', spoken: 'number', context: 'Open issue no. 42 for the full discussion.' },
  { term: 'no.', spoken: 'number', context: 'Release no. 7 fixes the regression.' },
  { term: 'API', spoken: 'A P I', context: 'The API key is stored server side only.' },
  { term: 'API', spoken: 'A P I', context: 'Expose the API through the proxy.' },
  { term: 'GPU', spoken: 'G P U', context: 'A GPU is recommended for adapter fine tuning.' },
  { term: 'GPU', spoken: 'G P U', context: 'The GPU stays idle during head training.' },
  { term: 'CPU', spoken: 'C P U', context: 'The baseline runs on CPU in milliseconds.' },
  { term: 'CPU', spoken: 'C P U', context: 'CPU time is reported per benchmark run.' },
  { term: 'JSON', spoken: 'J S O N', context: 'Settings are persisted as JSON with atomic writes.' },
  { term: 'JSON', spoken: 'J S O N', context: 'Export the dataset as JSON lines.' },
  { term: 'HTTP', spoken: 'H T T P', context: 'The HTTP layer validates every payload with zod.' },
  { term: 'HTTP', spoken: 'H T T P', context: 'HTTP failures are surfaced as warnings.' },
  { term: 'LLM', spoken: 'L L M', context: 'An LLM can be attached as an escalation target.' },
  { term: 'LLM', spoken: 'L L M', context: 'The LLM answers when the head is unsure.' },
  { term: 'SDK', spoken: 'S D K', context: 'Install the SDK before calling the provider.' },
  { term: 'SDK', spoken: 'S D K', context: 'The SDK ships with typed helpers.' },
  { term: 'UR', spoken: 'you are', context: 'UR going to like the new benchmark view.' },
  { term: 'UR', spoken: 'you are', context: 'UR welcome to rerun the suite.' },
  { term: 'w/', spoken: 'with', context: 'Ship the release w/ the migration guide.' },
  { term: 'w/', spoken: 'with', context: 'Test it w/ the offline engine first.' },
  { term: 'b/c', spoken: 'because', context: 'Retry the probe b/c the service restarted.' },
  { term: 'b/c', spoken: 'because', context: 'Marked rejected b/c quality dropped.' },
  { term: 'AKA', spoken: 'also known as', context: 'BM25, AKA Okapi BM25, remains a strong baseline.' },
  { term: 'AKA', spoken: 'also known as', context: 'The head, AKA task head, is linear.' },
  { term: 'ASAP', spoken: 'as soon as possible', context: 'Please review the change ASAP.' },
  { term: 'ASAP', spoken: 'as soon as possible', context: 'Ship the fix ASAP.' },
  { term: 'FAQ', spoken: 'F A Q', context: 'Add the question to the FAQ section.' },
  { term: 'FAQ', spoken: 'F A Q', context: 'The FAQ lists the setup steps.' },
  { term: 'SQL', spoken: 'sequel', context: 'Run the SQL migration before starting the server.' },
  { term: 'SQL', spoken: 'sequel', context: 'The SQL index needs a rebuild.' },
  { term: 'VM', spoken: 'virtual machine', context: 'The VM hosts the nightly benchmark job.' },
  { term: 'VM', spoken: 'virtual machine', context: 'Provision a fresh VM for the sweep.' },
  { term: 'OS', spoken: 'operating system', context: 'The OS reports 8 available cores.' },
  { term: 'OS', spoken: 'operating system', context: 'Pin the OS version in the image.' },
  { term: 'https', spoken: 'H T T P S', context: 'Only https endpoints should be fetched.' },
  { term: 'https', spoken: 'H T T P S', context: 'Serve the preview over https.' },
  { term: 'IO', spoken: 'I O', context: 'Disk IO dominates the indexing step.' },
  { term: 'IO', spoken: 'I O', context: 'Blocking IO stalls the event loop.' },
];

export const VOICE_NORMALIZATION_POOL: NormalizationItem[] = [
  { text: 'The build took 42 seconds.', expected: 'The build took forty-two seconds.' },
  { text: 'Latency dropped by 12%.', expected: 'Latency dropped by twelve percent.' },
  { text: 'It costs $15 per month.', expected: 'It costs fifteen dollars per month.' },
  { text: 'Dr. Weber signed off on the release.', expected: 'Doctor Weber signed off on the release.' },
  { text: 'Prof. Chen will review it.', expected: 'Professor Chen will review it.' },
  { text: 'The run took approx. 3 minutes.', expected: 'The run took approximately three minutes.' },
  { text: 'Use a sparse index, e.g. BM25.', expected: 'Use a sparse index, for example BM25.' },
  { text: 'The gate is strict, i.e. no regression.', expected: 'The gate is strict, that is no regression.' },
  { text: 'Compare lexical vs. dense retrieval.', expected: 'Compare lexical versus dense retrieval.' },
  { text: 'Store weights, metrics, etc. with the run.', expected: 'Store weights, metrics, et cetera with the run.' },
  { text: 'Finished in under 3 min.', expected: 'Finished in under three minutes.' },
  { text: 'p95 stayed below 0.4 sec.', expected: 'p95 stayed below zero point four seconds.' },
  { text: 'Review API keys monthly.', expected: 'Review A P I keys monthly.' },
  { text: 'A GPU speeds up fine tuning.', expected: 'A G P U speeds up fine tuning.' },
  { text: 'Settings are stored as JSON.', expected: 'Settings are stored as J S O N.' },
  { text: 'The HTTP layer validates payloads.', expected: 'The H T T P layer validates payloads.' },
  { text: 'An LLM can handle escalation.', expected: 'An L L M can handle escalation.' },
  { text: 'Install the SDK first.', expected: 'Install the S D K first.' },
  { text: 'UR going to like the benchmark view.', expected: 'you are going to like the benchmark view.' },
  { text: 'Ship the release w/ the guide.', expected: 'Ship the release with the guide.' },
  { text: 'Retry b/c the service restarted.', expected: 'Retry because the service restarted.' },
  { text: 'BM25, AKA Okapi BM25, is strong.', expected: 'BM25, also known as Okapi BM25, is strong.' },
  { text: 'Review the change ASAP.', expected: 'Review the change as soon as possible.' },
  { text: 'Add it to the FAQ section.', expected: 'Add it to the F A Q section.' },
  { text: 'Run the SQL migration first.', expected: 'Run the sequel migration first.' },
  { text: 'The VM hosts benchmarks.', expected: 'The virtual machine hosts benchmarks.' },
  { text: 'The OS reports 8 cores.', expected: 'The operating system reports eight cores.' },
  { text: 'Only https endpoints are fetched.', expected: 'Only H T T P S endpoints are fetched.' },
  { text: 'Disk IO dominates indexing.', expected: 'Disk I O dominates indexing.' },
  { text: '**Latency** is 250 ms today.', expected: 'Latency is two hundred fifty milliseconds today.' },
  { text: 'See [docs](https://example.com) for details.', expected: 'See docs for details.' },
  { text: 'Speed improved by 3.5x.', expected: 'Speed improved by three point five x.' },
  { text: 'The model has 7 GB of weights.', expected: 'The model has seven gigabytes of weights.' },
  { text: 'Train for 20 epochs.', expected: 'Train for twenty epochs.' },
  { text: 'Set the threshold to 0.45.', expected: 'Set the threshold to zero point four five.' },
  { text: 'Web and desktop clients differ.', expected: 'Web and desktop clients differ.' },
  { text: 'Dr. Chen shipped the release.', expected: 'Doctor Chen shipped the release.' },
  { text: 'The sweep took approx. 20 min.', expected: 'The sweep took approximately twenty minutes.' },
  { text: 'Use sparse search, e.g. BM25.', expected: 'Use sparse search, for example BM25.' },
  { text: 'It is free, i.e. no cost.', expected: 'It is free, that is no cost.' },
  { text: 'Head vs. policy is compared.', expected: 'Head versus policy is compared.' },
  { text: 'Weights, metrics, etc. are stored.', expected: 'Weights, metrics, et cetera are stored.' },
  { text: 'Wait 5 sec between probes.', expected: 'Wait five seconds between probes.' },
  { text: 'Issue no. 7 is fixed.', expected: 'Issue number seven is fixed.' },
  { text: 'The CPU stays idle.', expected: 'The C P U stays idle.' },
  { text: 'Export as JSON lines.', expected: 'Export as J S O N lines.' },
  { text: 'HTTP failures are warnings.', expected: 'H T T P failures are warnings.' },
  { text: 'The LLM answers when unsure.', expected: 'The L L M answers when unsure.' },
  { text: 'Install the SDK first.', expected: 'Install the S D K first.' },
  { text: 'UR welcome to rerun it.', expected: 'you are welcome to rerun it.' },
  { text: 'Test it w/ the offline engine.', expected: 'Test it with the offline engine.' },
  { text: 'Rejected b/c quality dropped.', expected: 'Rejected because quality dropped.' },
  { text: 'The head, AKA task head, is linear.', expected: 'The head, also known as task head, is linear.' },
  { text: 'Ship the fix ASAP.', expected: 'Ship the fix as soon as possible.' },
  { text: 'The FAQ lists setup steps.', expected: 'The F A Q lists setup steps.' },
  { text: 'Run the SQL migration now.', expected: 'Run the sequel migration now.' },
  { text: 'Provision a fresh VM today.', expected: 'Provision a fresh virtual machine today.' },
  { text: 'Pin the OS version.', expected: 'Pin the operating system version.' },
  { text: 'Serve the preview over https.', expected: 'Serve the preview over H T T P S.' },
  { text: 'Blocking IO stalls the loop.', expected: 'Blocking I O stalls the loop.' },
  { text: 'Weights are 4 GB in total.', expected: 'Weights are four gigabytes in total.' },
  { text: 'Training ran for 45 min.', expected: 'Training ran for forty-five minutes.' },
  { text: 'Use 2 sec timeouts.', expected: 'Use two seconds timeouts.' },
  { text: 'Prof. Novak published 3 papers.', expected: 'Professor Novak published three papers.' },
  { text: 'The API returns 200 ok.', expected: 'The A P I returns two hundred ok.' },
];

// ---------------------------------------------------------------------------
// General specialist: which tool should handle this request?
// ---------------------------------------------------------------------------

export const GENERAL_TOOL_POOL: RoutingItem[] = [
  { text: 'what time is it on this machine (system clock and uptime)', label: 'system.info' },
  { text: 'how many cpu cores do I have', label: 'system.info' },
  { text: 'show me the platform details', label: 'system.info' },
  { text: 'what is my hostname', label: 'system.info' },
  { text: 'how much free memory is left', label: 'system.info' },
  { text: 'calculate 8 * 9', label: 'calc.evaluate' },
  { text: 'what is 123 + 456', label: 'calc.evaluate' },
  { text: 'compute 2 ^ 16', label: 'calc.evaluate' },
  { text: 'what is 45 % 6', label: 'calc.evaluate' },
  { text: 'evaluate (18 + 6) / 3', label: 'calc.evaluate' },
  { text: 'list the files in the workspace', label: 'workspace.list' },
  { text: 'what is in this directory', label: 'workspace.list' },
  { text: 'show the project structure', label: 'workspace.list' },
  { text: 'read the README', label: 'workspace.read' },
  { text: 'open package.json', label: 'workspace.read' },
  { text: 'cat vite.config.ts', label: 'workspace.read' },
  { text: 'remember that the release is friday', label: 'memory.save' },
  { text: 'save a note about the migration plan', label: 'memory.save' },
  { text: 'write this down for later', label: 'memory.save' },
  { text: 'search my notes for caching', label: 'memory.search' },
  { text: 'do you remember the deployment steps', label: 'memory.search' },
  { text: 'look up the note about postgres', label: 'memory.search' },
  { text: 'summarize https://example.com/report', label: 'http.fetch' },
  { text: 'fetch https://api.github.com/repos/nodejs/node', label: 'http.fetch' },
  { text: 'get https://news.ycombinator.com', label: 'http.fetch' },
  { text: 'explain how permissions work', label: 'answer.direct' },
  { text: 'write a haiku about latency', label: 'answer.direct' },
  { text: 'draft an email about the release', label: 'answer.direct' },
  { text: 'what is the difference between the two approaches', label: 'answer.direct' },
  { text: 'brainstorm names for the feature', label: 'answer.direct' },
  { text: 'summarize today in three bullet points', label: 'answer.direct' },
  { text: 'what should I test next', label: 'answer.direct' },
  { text: 'how much memory is available', label: 'system.info' },
  { text: 'what does the host report', label: 'system.info' },
  { text: 'show the cpu count', label: 'system.info' },
  { text: 'give me the system summary', label: 'system.info' },
  { text: 'what is 77 + 23', label: 'calc.evaluate' },
  { text: 'compute 15 % 4', label: 'calc.evaluate' },
  { text: 'what is 9 ^ 3', label: 'calc.evaluate' },
  { text: 'multiply 12 by 12', label: 'calc.evaluate' },
  { text: 'divide 240 by 6', label: 'calc.evaluate' },
  { text: 'what is 88 / 11', label: 'calc.evaluate' },
  { text: 'list the root directory', label: 'workspace.list' },
  { text: 'what is in shared', label: 'workspace.list' },
  { text: 'enumerate the workspace entries', label: 'workspace.list' },
  { text: 'show the repo layout', label: 'workspace.list' },
  { text: 'open README.md', label: 'workspace.read' },
  { text: 'read server/index.ts', label: 'workspace.read' },
  { text: 'show me docs/ACCEPTANCE.md', label: 'workspace.read' },
  { text: 'inspect package.json', label: 'workspace.read' },
  { text: 'remember that reviews happen on tuesday', label: 'memory.save' },
  { text: 'save a note about the api token policy', label: 'memory.save' },
  { text: 'note down the staging url pattern', label: 'memory.save' },
  { text: 'keep this: thresholds live in settings', label: 'memory.save' },
  { text: 'search notes for release dates', label: 'memory.search' },
  { text: 'do you remember the staging url', label: 'memory.search' },
  { text: 'what did we save about retention', label: 'memory.search' },
  { text: 'look up notes about the review process', label: 'memory.search' },
  { text: 'get https://status.example.com', label: 'http.fetch' },
  { text: 'fetch the page https://example.org/guide', label: 'http.fetch' },
  { text: 'summarize https://example.net/data.json', label: 'http.fetch' },
  { text: 'explain the permission model', label: 'answer.direct' },
  { text: 'draft a short update for the team', label: 'answer.direct' },
  { text: 'brainstorm names for the release', label: 'answer.direct' },
  { text: 'summarize the week in three bullets', label: 'answer.direct' },
  { text: 'what should we prioritise next', label: 'answer.direct' },
  { text: 'write a haiku about uptime', label: 'answer.direct' },
  { text: 'compare the two rollout options', label: 'answer.direct' },
  { text: 'what is the difference between the plans', label: 'answer.direct' },
  { text: 'how do I reset my settings', label: 'answer.direct' },
  { text: 'give me ideas for onboarding docs', label: 'answer.direct' },
  { text: 'what is 64 - 8', label: 'calc.evaluate' },
  { text: 'list files under server', label: 'workspace.list' },
  { text: 'read tsconfig.json', label: 'workspace.read' },
  { text: 'remember the office wifi password hint', label: 'memory.save' },
  { text: 'look up my note about wifi', label: 'memory.search' },
];

/**
 * Lexicon split: the first sentence of every term is used for training, the
 * remaining sentences become the held-out split. That measures exactly what a
 * lexicon is for — applying a term learned in one context to new sentences —
 * without pretending an unseen abbreviation could be known.
 */
export function splitLexiconPool(pool: LexiconItem[]): { train: LexiconItem[]; evaluation: LexiconItem[] } {
  const seen = new Set<string>();
  const train: LexiconItem[] = [];
  const evaluation: LexiconItem[] = [];
  for (const item of pool) {
    const key = item.term.toLowerCase();
    if (seen.has(key)) evaluation.push(item);
    else {
      seen.add(key);
      train.push(item);
    }
  }
  return { train, evaluation };
}

/** Splits a pool into disjoint train and evaluation partitions (2:1 by default). */
export function splitPool<T>(pool: T[], evalEvery = 3): { train: T[]; evaluation: T[] } {
  const train: T[] = [];
  const evaluation: T[] = [];
  // Duplicate inputs (the same instruction written twice) are collapsed first:
  // index-based splits would otherwise leak a benchmark task into the training
  // split through its twin.
  pool.forEach((item, index) => {
    if (index % evalEvery === evalEvery - 1) evaluation.push(item);
    else train.push(item);
  });
  const seen = new Set<string>();
  const dedupe = (items: T[]) =>
    items.filter((item) => {
      const key = JSON.stringify(item);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  return { train: dedupe(train), evaluation: dedupe(evaluation) };
}
