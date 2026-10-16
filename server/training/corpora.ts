import type { LexiconExample } from '../ml/lexicon.js';
import type { RetrievalDoc } from '../ml/retrieval.js';

/**
 * Curated seed corpora.
 *
 * These are the shipped starting point: small, hand-written, versioned with the
 * code. Every pool is split deterministically (see `splitPool`), so the model
 * trains on two thirds and is measured on the remaining third that it has never
 * seen. Users can add their own examples on top, which is what makes a
 * specialist improve over time.
 */

export interface LabeledItem {
  text: string;
  label: string;
}

export interface ExpressionItem {
  text: string;
  expected: string;
}

export interface RetrievalQuery {
  query: string;
  relevant: string[];
}

/** Instruction → label item (routing / tool selection). */
export type RoutingItem = LabeledItem;
/** A document the retrieval index can serve. */
export type SeedDoc = RetrievalDoc;
/** A labelled retrieval question. */
export type SeedQuery = RetrievalQuery;

// ---------------------------------------------------------------------------
// Coding · tool routing (write code, read files, compute, remember)
// ---------------------------------------------------------------------------

export const CODING_ROUTING_POOL: LabeledItem[] = [
  { text: 'what is 12 * 4', label: 'calc.evaluate' },
  { text: 'open server/app.ts', label: 'workspace.read' },
  { text: 'list the files in the src folder', label: 'workspace.list' },
  { text: 'how much memory is free on this machine', label: 'system.info' },
  { text: 'refactor this function to be async', label: 'answer.direct' },
  { text: 'remember that the API port is 4317', label: 'memory.save' },
  { text: 'what do you know about our caching strategy', label: 'memory.search' },
  { text: 'compute 88 / 4', label: 'calc.evaluate' },
  { text: 'read package.json', label: 'workspace.read' },
  { text: 'what files exist in this project', label: 'workspace.list' },
  { text: 'explain how the tool registry works', label: 'answer.direct' },
  { text: 'note that the release happens on friday', label: 'memory.save' },
  { text: 'find the note about migrations', label: 'memory.search' },
  { text: 'what is 7 * 9', label: 'calc.evaluate' },
  { text: 'cat vite.config.ts', label: 'workspace.read' },
  { text: 'show me the repository layout', label: 'workspace.list' },
  { text: 'which node version is running', label: 'system.info' },
  { text: 'write a debounce helper in typescript', label: 'answer.direct' },
  { text: 'save this: settings are validated with zod', label: 'memory.save' },
  { text: 'look up our notes about deployment', label: 'memory.search' },
  { text: 'evaluate 25 + 17', label: 'calc.evaluate' },
  { text: 'display the contents of README.md', label: 'workspace.read' },
  { text: 'enumerate the folders in the workspace', label: 'workspace.list' },
  { text: 'how many cpus does this box have', label: 'system.info' },
  { text: 'why does this promise reject so late', label: 'answer.direct' },
  { text: 'remember the staging url pattern', label: 'memory.save' },
  { text: 'did we save anything about rate limits', label: 'memory.search' },
  { text: 'calculate 144 / 12', label: 'calc.evaluate' },
  { text: 'show me src/components/Sidebar.tsx', label: 'workspace.read' },
  { text: 'list every markdown file in docs', label: 'workspace.list' },
  { text: 'report the current uptime', label: 'system.info' },
  { text: 'summarise the tradeoffs of caching', label: 'answer.direct' },
  { text: 'keep this: the training pipeline writes checkpoints', label: 'memory.save' },
  { text: 'recall the note about permissions', label: 'memory.search' },
  { text: 'what is 19 % 4', label: 'calc.evaluate' },
  { text: 'inspect tailwind.config.js', label: 'workspace.read' },
  { text: 'what directories are at the top level', label: 'workspace.list' },
  { text: 'give me the platform and arch', label: 'system.info' },
  { text: 'how should I structure error handling', label: 'answer.direct' },
  { text: 'store this for later: vitest runs the frontend tests', label: 'memory.save' },
  { text: 'what do we know about vector indexes', label: 'memory.search' },
  { text: 'what is 256 ^ 0', label: 'calc.evaluate' },
  { text: 'print the first lines of server/index.ts', label: 'workspace.read' },
  { text: 'show the workspace tree', label: 'workspace.list' },
  { text: 'how much heap is the process using', label: 'system.info' },
  { text: 'draft a changelog entry for this release', label: 'answer.direct' },
  { text: 'make a note that reviews happen on tuesday', label: 'memory.save' },
  { text: 'search memory for benchmark thresholds', label: 'memory.search' },
  { text: 'what is 45 - 19', label: 'calc.evaluate' },
  { text: 'read docs/ARCHITECTURE.md', label: 'workspace.read' },
  { text: 'which files changed in the last commit', label: 'answer.direct' },
  { text: 'what is the hostname of this machine', label: 'system.info' },
  { text: 'explain the promotion gate in one paragraph', label: 'answer.direct' },
  { text: 'remember that the confidence threshold is configurable', label: 'memory.save' },
  { text: 'look up the note about the data directory', label: 'memory.search' },
  { text: 'what is 3 * 3 * 3', label: 'calc.evaluate' },
  { text: 'show me scripts/smoke.mjs', label: 'workspace.read' },
  { text: 'list the test files', label: 'workspace.list' },
  { text: 'what os is this running on', label: 'system.info' },
  { text: 'suggest a better name for this module', label: 'answer.direct' },
  { text: 'note that embeddings are hashed locally', label: 'memory.save' },
  { text: 'fetch my saved note about timeouts', label: 'memory.search' },
  { text: 'what is 81 / 9', label: 'calc.evaluate' },
  { text: 'open src/App.tsx', label: 'workspace.read' },
  { text: 'how many files are in the repo', label: 'workspace.list' },
  { text: 'what is the load average', label: 'system.info' },
  { text: 'plan the next three steps for this milestone', label: 'answer.direct' },
  { text: 'save a note about the export format', label: 'memory.save' },
  { text: 'have we stored anything about batch sizes', label: 'memory.search' },
  { text: 'what is 1000 - 250', label: 'calc.evaluate' },
  { text: 'read the file docs/ACCEPTANCE.md', label: 'workspace.read' },
  { text: 'list all typescript files in server', label: 'workspace.list' },
  { text: 'report free disk space', label: 'system.info' },
  { text: 'rewrite this loop with map', label: 'answer.direct' },
  { text: 'remember: the scorecard keeps every task result', label: 'memory.save' },
  { text: 'what notes do we have about latency', label: 'memory.search' },
  { text: 'compute 7 ^ 2', label: 'calc.evaluate' },
  { text: 'what is inside tsconfig.server.json', label: 'workspace.read' },
  { text: 'show folder contents of shared', label: 'workspace.list' },
  { text: 'how many worker threads are configured', label: 'system.info' },
  { text: 'compare the two rollout options', label: 'answer.direct' },
  { text: 'write down that the voice lexicon is learned', label: 'memory.save' },
  { text: 'find any note about recall at k', label: 'memory.search' },
  { text: 'what is 0.5 * 8', label: 'calc.evaluate' },
  { text: 'open server/specialists/inference.ts', label: 'workspace.read' },
  { text: 'what is in the benchmarks directory', label: 'workspace.list' },
  { text: 'state the total system memory', label: 'system.info' },
  { text: 'what would a schema validation error look like', label: 'answer.direct' },
  { text: 'note that checkpoints are written every epoch', label: 'memory.save' },
  { text: 'look up the note on dataset provenance', label: 'memory.search' },
  { text: 'calculate 17 * 6', label: 'calc.evaluate' },
  { text: 'show me the first 20 lines of server/logger.ts', label: 'workspace.read' },
  { text: 'give me a directory overview of server', label: 'workspace.list' },
  { text: 'what are the cpu times of this process', label: 'system.info' },
  { text: 'explain why the benchmark is slower on repeat runs', label: 'answer.direct' },
  { text: 'keep this thought: small pools need fewer features', label: 'memory.save' },
  { text: 'what did we note about holdout splits', label: 'memory.search' },
  { text: 'what is 62 + 38', label: 'calc.evaluate' },
  { text: 'read the eslint config if it exists', label: 'workspace.read' },
  { text: 'list the json files in the project', label: 'workspace.list' },
  { text: 'how long has the server been up', label: 'system.info' },
  { text: 'give me a short review of this function', label: 'answer.direct' },
  { text: 'remember that promotions need a benchmark', label: 'memory.save' },
  { text: 'search my notes for the word version', label: 'memory.search' },
  { text: 'what is 9 * 9', label: 'calc.evaluate' },
  { text: 'display server/routes/api.ts', label: 'workspace.read' },
  { text: 'what sits in the tools folder', label: 'workspace.list' },
  { text: 'describe the runtime environment', label: 'system.info' },
  { text: 'what is the difference between a candidate and an active version', label: 'answer.direct' },
  { text: 'save this detail: the rule baseline is deterministic', label: 'memory.save' },
  { text: 'do we have notes about resource usage', label: 'memory.search' },
  { text: 'evaluate 100 % 7', label: 'calc.evaluate' },
  { text: 'open src/index.css', label: 'workspace.read' },
  { text: 'list everything inside the scripts folder', label: 'workspace.list' },
  { text: 'what is the total memory of the host', label: 'system.info' },
  { text: 'how would you test this edge case', label: 'answer.direct' },
  { text: 'note that runs are cancellable', label: 'memory.save' },
  { text: 'look up anything about confirmation thresholds', label: 'memory.search' },
  { text: 'what is 15 * 15', label: 'calc.evaluate' },
  { text: 'read the shared types file', label: 'workspace.read' },
  { text: 'how many directories does data contain', label: 'workspace.list' },
  { text: 'what is the process id of the server', label: 'system.info' },
  { text: 'propose a plan for the frontend redesign', label: 'answer.direct' },
  { text: 'remember the naming convention for datasets', label: 'memory.save' },
  { text: 'what do we know about sqlite or json storage', label: 'memory.search' },
  { text: 'what is 2 ^ 8', label: 'calc.evaluate' },
  { text: 'show the contents of .env.example', label: 'workspace.read' },
  { text: 'which test files exist in the client', label: 'workspace.list' },
  { text: 'what languages are installed here', label: 'system.info' },
  { text: 'explain how specialists pick tools', label: 'answer.direct' },
  { text: 'store this: the ui reads the same api as the tests', label: 'memory.save' },
  { text: 'find the note mentioning rollout', label: 'memory.search' },
];

// ---------------------------------------------------------------------------
// Coding · expressions (end-to-end arithmetic)
// ---------------------------------------------------------------------------

export const CODING_EXPRESSION_POOL: ExpressionItem[] = [
  { text: 'what is 12 * 4?', expected: '48' },
  { text: 'compute 88 / 4', expected: '22' },
  { text: 'what is 7 * 9', expected: '63' },
  { text: 'evaluate 25 + 17', expected: '42' },
  { text: 'calculate 144 / 12', expected: '12' },
  { text: 'what is 19 % 4', expected: '3' },
  { text: 'what is 45 - 19', expected: '26' },
  { text: 'what is 3 * 3 * 3', expected: '27' },
  { text: 'compute (4 + 6) / 2', expected: '5' },
  { text: 'what is 81 / 9', expected: '9' },
  { text: 'what is 1000 - 250', expected: '750' },
  { text: 'compute 7 ^ 2', expected: '49' },
  { text: 'what is 0.5 * 8', expected: '4' },
  { text: 'calculate 17 * 6', expected: '102' },
  { text: 'what is 62 + 38', expected: '100' },
  { text: 'what is 9 * 9', expected: '81' },
  { text: 'evaluate 100 % 7', expected: '2' },
  { text: 'what is 15 * 15', expected: '225' },
  { text: 'what is 2 ^ 8', expected: '256' },
  { text: 'compute 128 / 4', expected: '32' },
  { text: 'what is 5 * 12', expected: '60' },
  { text: 'what is 96 / 8', expected: '12' },
  { text: 'calculate 33 + 44', expected: '77' },
  { text: 'what is 250 - 75', expected: '175' },
  { text: 'compute 11 * 11', expected: '121' },
  { text: 'what is 2 * (3 + 4)', expected: '14' },
  { text: 'what is 64 / 2', expected: '32' },
  { text: 'what is 30 % 6', expected: '0' },
  { text: 'compute 9 ^ 2', expected: '81' },
  { text: 'what is 14 + 28 + 58', expected: '100' },
  { text: 'what is 3.5 * 2', expected: '7' },
  { text: 'what is 1024 / 16', expected: '64' },
  { text: 'calculate 13 * 3', expected: '39' },
  { text: 'what is 77 - 7', expected: '70' },
  { text: 'what is 8 * 9', expected: '72' },
  { text: 'compute 56 / 7', expected: '8' },
  { text: 'what is 4 ^ 3', expected: '64' },
  { text: 'what is 49 + 51', expected: '100' },
  { text: 'evaluate 6 * 6 + 1', expected: '37' },
  { text: 'what is 20 % 3', expected: '2' },
  { text: 'what is 5 * 5 * 5', expected: '125' },
  { text: 'calculate 90 / 3', expected: '30' },
  { text: 'what is 128 - 28', expected: '100' },
  { text: 'what is 11 + 22', expected: '33' },
  { text: 'compute 2.5 * 4', expected: '10' },
  { text: 'what is 72 / 9', expected: '8' },
  { text: 'what is 100 - 1', expected: '99' },
  { text: 'evaluate 3 * 7 * 2', expected: '42' },
  { text: 'what is 81 % 8', expected: '1' },
  { text: 'what is 6 ^ 3', expected: '216' },
  { text: 'calculate 48 / 6', expected: '8' },
  { text: 'what is 35 + 65', expected: '100' },
  { text: 'what is 12 * 12', expected: '144' },
  { text: 'compute 99 / 3', expected: '33' },
  { text: 'what is 7 * 8 + 9', expected: '65' },
  { text: 'what is 210 - 110', expected: '100' },
  { text: 'what is 18 % 5', expected: '3' },
  { text: 'what is 4.5 + 4.5', expected: '9' },
  { text: 'compute 27 / 3', expected: '9' },
  { text: 'what is 16 * 4', expected: '64' },
  { text: 'calculate (12 + 8) * 3', expected: '60' },
  { text: 'what is 55 + 45', expected: '100' },
  { text: 'what is 100 / 8', expected: '12.5' },
  { text: 'what is 13 * 4', expected: '52' },
  { text: 'compute 63 / 7', expected: '9' },
  { text: 'what is 111 - 11', expected: '100' },
];

// ---------------------------------------------------------------------------
// Research · routing and extraction
// ---------------------------------------------------------------------------

export const RESEARCH_ROUTING_POOL: LabeledItem[] = [
  { text: 'summarise https://arxiv.org/abs/1706.03762', label: 'extract.url' },
  { text: 'what do we know about sparse retrieval', label: 'memory.search' },
  { text: 'explain reciprocal rank fusion', label: 'answer.direct' },
  { text: 'fetch https://blog.cloudflare.com/workers', label: 'extract.url' },
  { text: 'do we have notes about ablations', label: 'memory.search' },
  { text: 'what is bm25', label: 'answer.direct' },
  { text: 'read https://en.wikipedia.org/wiki/Okapi_BM25', label: 'extract.url' },
  { text: 'look up our saved note on quantisation', label: 'memory.search' },
  { text: 'compare lexical and dense retrieval', label: 'answer.direct' },
  { text: 'can you open https://arxiv.org/list/cs.IR/recent', label: 'extract.url' },
  { text: 'find the note about evaluation sets', label: 'memory.search' },
  { text: 'draft a research question about drift', label: 'answer.direct' },
  { text: 'grab https://developer.mozilla.org/en-US/docs/Web/HTTP', label: 'extract.url' },
  { text: 'have we stored anything about tokenisation', label: 'memory.search' },
  { text: 'why does precision at k matter', label: 'answer.direct' },
  { text: 'summarise https://huggingface.co/docs/transformers', label: 'extract.url' },
  { text: 'recall our note on labelled data', label: 'memory.search' },
  { text: 'outline a literature review', label: 'answer.direct' },
  { text: 'check https://paperswithcode.com/sota', label: 'extract.url' },
  { text: 'what did we save about the curated pools', label: 'memory.search' },
  { text: 'how do we measure recall', label: 'answer.direct' },
  { text: 'open https://www.aclweb.org/anthology/', label: 'extract.url' },
  { text: 'look up the note on held-out splits', label: 'memory.search' },
  { text: 'explain the difference between mrr and recall', label: 'answer.direct' },
  { text: 'fetch https://bair.berkeley.edu/blog/', label: 'extract.url' },
  { text: 'do we know anything about chunk sizes', label: 'memory.search' },
  { text: 'write a short abstract about retrieval', label: 'answer.direct' },
  { text: 'see https://vitest.dev/guide/', label: 'extract.url' },
  { text: 'what notes mention provenance', label: 'memory.search' },
  { text: 'what are the limits of lexical matching', label: 'answer.direct' },
  { text: 'retrieve https://react.dev/reference/react', label: 'extract.url' },
  { text: 'have we noted anything about overfitting', label: 'memory.search' },
  { text: 'explain how the index is scored', label: 'answer.direct' },
  { text: 'open https://nodejs.org/api/process.html', label: 'extract.url' },
  { text: 'find our note about confidence thresholds', label: 'memory.search' },
  { text: 'summarise the state of retrieval research', label: 'answer.direct' },
  { text: 'read https://www.postgresql.org/docs/current/textsearch.html', label: 'extract.url' },
  { text: 'what do we know about k1 and b', label: 'memory.search' },
  { text: 'plan a benchmark for retrieval quality', label: 'answer.direct' },
  { text: 'check https://expressjs.com/en/4x/api.html', label: 'extract.url' },
  { text: 'did we save the dataset provenance rules', label: 'memory.search' },
  { text: 'what would an ablation study look like', label: 'answer.direct' },
  { text: 'grab https://www.nature.com/articles/s41586-021-03819-2', label: 'extract.url' },
  { text: 'look up anything about learning rates', label: 'memory.search' },
  { text: 'explain why held-out data matters', label: 'answer.direct' },
  { text: 'can you fetch https://status.example.com', label: 'extract.url' },
  { text: 'what notes do we have about regularisation', label: 'memory.search' },
  { text: 'how should I cite this approach', label: 'answer.direct' },
];

export const RESEARCH_EXTRACTION_POOL: ExpressionItem[] = [
  { text: 'summarise https://arxiv.org/abs/1706.03762', expected: 'arxiv.org/abs/1706.03762' },
  { text: 'fetch https://blog.cloudflare.com/workers', expected: 'blog.cloudflare.com/workers' },
  { text: 'read https://en.wikipedia.org/wiki/Okapi_BM25', expected: 'en.wikipedia.org/wiki/Okapi_BM25' },
  { text: 'can you open https://arxiv.org/list/cs.IR/recent', expected: 'arxiv.org/list/cs.IR/recent' },
  { text: 'grab https://developer.mozilla.org/en-US/docs/Web/HTTP', expected: 'developer.mozilla.org/en-US/docs/Web/HTTP' },
  { text: 'summarise https://huggingface.co/docs/transformers', expected: 'huggingface.co/docs/transformers' },
  { text: 'check https://paperswithcode.com/sota', expected: 'paperswithcode.com/sota' },
  { text: 'open https://www.aclweb.org/anthology/', expected: 'aclweb.org/anthology' },
  { text: 'fetch https://bair.berkeley.edu/blog/', expected: 'bair.berkeley.edu/blog' },
  { text: 'see https://vitest.dev/guide/', expected: 'vitest.dev/guide' },
  { text: 'retrieve https://react.dev/reference/react', expected: 'react.dev/reference/react' },
  { text: 'open https://nodejs.org/api/process.html', expected: 'nodejs.org/api/process.html' },
  { text: 'read https://www.postgresql.org/docs/current/textsearch.html', expected: 'postgresql.org/docs/current/textsearch.html' },
  { text: 'check https://expressjs.com/en/4x/api.html', expected: 'expressjs.com/en/4x/api.html' },
  { text: 'grab https://www.nature.com/articles/s41586-021-03819-2', expected: 'nature.com/articles/s41586-021-03819-2' },
  { text: 'can you fetch https://status.example.com', expected: 'status.example.com' },
  { text: 'summarise https://www.rfc-editor.org/rfc/rfc9110.html', expected: 'rfc-editor.org/rfc/rfc9110.html' },
  { text: 'open https://tailwindcss.com/docs/configuration', expected: 'tailwindcss.com/docs/configuration' },
  { text: 'fetch https://vitejs.dev/config/', expected: 'vitejs.dev/config' },
  { text: 'read https://www.w3.org/TR/wai-aria-1.2/', expected: 'w3.org/TR/wai-aria-1.2' },
  { text: 'check https://doi.org/10.1145/3292500', expected: 'doi.org/10.1145/3292500' },
  { text: 'retrieve https://aclanthology.org/2023.acl-long.1/', expected: 'aclanthology.org/2023.acl-long.1' },
  { text: 'see https://github.com/jonas050210/AgentOS', expected: 'github.com/jonas050210/AgentOS' },
  { text: 'open https://news.ycombinator.com/item?id=1', expected: 'news.ycombinator.com/item' },
  { text: 'grab https://cdn.jsdelivr.net/npm/zod/package.json', expected: 'cdn.jsdelivr.net/npm/zod/package.json' },
  { text: 'fetch http://127.0.0.1:4318/api/health', expected: '127.0.0.1:4318/api/health' },
  { text: 'summarise https://www.anthropic.com/research', expected: 'anthropic.com/research' },
  { text: 'read https://openai.com/index/gpt-4/', expected: 'openai.com/index/gpt-4' },
  { text: 'check https://stripe.com/docs/api', expected: 'stripe.com/docs/api' },
  { text: 'open https://developer.apple.com/design/human-interface-guidelines', expected: 'developer.apple.com/design/human-interface-guidelines' },
];

// ---------------------------------------------------------------------------
// General · tool selection
// ---------------------------------------------------------------------------

export const GENERAL_TOOL_POOL: LabeledItem[] = [
  { text: 'what is 21 * 2', label: 'calc.evaluate' },
  { text: 'how much ram is free', label: 'system.info' },
  { text: 'read README.md', label: 'workspace.read' },
  { text: 'list the top-level files', label: 'workspace.list' },
  { text: 'remember that standup is at nine', label: 'memory.save' },
  { text: 'what do you remember about the roadmap', label: 'memory.search' },
  { text: 'draft a polite decline to the vendor', label: 'answer.direct' },
  { text: 'what is 34 + 66', label: 'calc.evaluate' },
  { text: 'describe the running environment', label: 'system.info' },
  { text: 'show me package.json', label: 'workspace.read' },
  { text: 'enumerate the folders here', label: 'workspace.list' },
  { text: 'save this for me: the demo is on tuesday', label: 'memory.save' },
  { text: 'look up my note on the api key rotation', label: 'memory.search' },
  { text: 'explain what a capability module is', label: 'answer.direct' },
  { text: 'compute 240 / 8', label: 'calc.evaluate' },
  { text: 'how long has this been running', label: 'system.info' },
  { text: 'open docs/ARCHITECTURE.md', label: 'workspace.read' },
  { text: 'what files are in the scripts folder', label: 'workspace.list' },
  { text: 'note that reviews happen on monday', label: 'memory.save' },
  { text: 'find anything about release cadence', label: 'memory.search' },
  { text: 'write a short summary of today', label: 'answer.direct' },
  { text: 'what is 5 * 20', label: 'calc.evaluate' },
  { text: 'what is the hostname', label: 'system.info' },
  { text: 'display the file server/index.ts', label: 'workspace.read' },
  { text: 'how many files are in the root', label: 'workspace.list' },
  { text: 'remember the meeting password hint', label: 'memory.save' },
  { text: 'what did we save about pricing', label: 'memory.search' },
  { text: 'summarise the three open questions', label: 'answer.direct' },
  { text: 'calculate 90 - 45', label: 'calc.evaluate' },
  { text: 'report cpu model and memory', label: 'system.info' },
  { text: 'cat vite.config.ts', label: 'workspace.read' },
  { text: 'show me the workspace tree', label: 'workspace.list' },
  { text: 'keep a note about the backup schedule', label: 'memory.save' },
  { text: 'search memory for the design doc', label: 'memory.search' },
  { text: 'brainstorm names for the release', label: 'answer.direct' },
  { text: 'what is 17 * 4', label: 'calc.evaluate' },
  { text: 'how much memory does the process use', label: 'system.info' },
  { text: 'read tsconfig.json', label: 'workspace.read' },
  { text: 'list the json files', label: 'workspace.list' },
  { text: 'store this: the meeting moved to thursday', label: 'memory.save' },
  { text: 'recall the note about incidents', label: 'memory.search' },
  { text: 'what would a rollback plan look like', label: 'answer.direct' },
  { text: 'what is 12 % 5', label: 'calc.evaluate' },
  { text: 'which platform am I on', label: 'system.info' },
  { text: 'look at src/main.tsx', label: 'workspace.read' },
  { text: 'give me a directory overview', label: 'workspace.list' },
  { text: 'write this down: tests run with vitest', label: 'memory.save' },
  { text: 'do we have anything about onboarding', label: 'memory.search' },
  { text: 'compare the two plans briefly', label: 'answer.direct' },
  { text: 'what is 8 * 8', label: 'calc.evaluate' },
  { text: 'state the total system memory', label: 'system.info' },
  { text: 'open scripts/smoke.mjs', label: 'workspace.read' },
  { text: 'what is inside the data directory', label: 'workspace.list' },
  { text: 'note the deployment checklist', label: 'memory.save' },
  { text: 'find my note about wifi', label: 'memory.search' },
  { text: 'how do I reset my settings', label: 'answer.direct' },
  { text: 'what is 64 / 4', label: 'calc.evaluate' },
  { text: 'what is the load average right now', label: 'system.info' },
  { text: 'read server/routes/platform.ts', label: 'workspace.read' },
  { text: 'list the files in server/ml', label: 'workspace.list' },
  { text: 'remember that we ship on fridays', label: 'memory.save' },
  { text: 'what notes mention latency budgets', label: 'memory.search' },
  { text: 'explain our naming conventions', label: 'answer.direct' },
  { text: 'get https://status.example.com', label: 'http.fetch' },
  { text: 'fetch https://example.org/guide', label: 'http.fetch' },
  { text: 'open the page https://example.net/data.json', label: 'http.fetch' },
  { text: 'what is 3 * 11', label: 'calc.evaluate' },
  { text: 'show me the system summary', label: 'system.info' },
  { text: 'read the changelog if present', label: 'workspace.read' },
  { text: 'which directories exist in server', label: 'workspace.list' },
  { text: 'save a note about the token policy', label: 'memory.save' },
  { text: 'search notes for the word freeze', label: 'memory.search' },
  { text: 'draft an update for the team', label: 'answer.direct' },
];

// ---------------------------------------------------------------------------
// Voice · pronunciation lexicon (each term has a training sentence plus
// held-out sentences that reuse the term in a new context)
// ---------------------------------------------------------------------------

export const VOICE_LEXICON_POOL: LexiconExample[] = [
  { term: 'Dr.', spoken: 'Doctor', context: 'Dr. Weber will review the release notes tomorrow.' },
  { term: 'Dr.', spoken: 'Doctor', context: 'I spoke to Dr. Klein about the latency budget.' },
  { term: 'Prof.', spoken: 'Professor', context: 'Prof. Novak published the evaluation yesterday.' },
  { term: 'Prof.', spoken: 'Professor', context: 'The lecture by Prof. Silva starts at nine.' },
  { term: 'approx.', spoken: 'approximately', context: 'The run took approx. 12 seconds to finish.' },
  { term: 'approx.', spoken: 'approximately', context: 'approx. half of the requests hit the cache.' },
  { term: 'e.g.', spoken: 'for example', context: 'Use a sparse index, e.g. BM25, for keyword queries.' },
  { term: 'e.g.', spoken: 'for example', context: 'Cheaper heads, e.g. linear models, train in seconds.' },
  { term: 'i.e.', spoken: 'that is', context: 'The gate is strict, i.e. no regression is allowed.' },
  { term: 'i.e.', spoken: 'that is', context: 'Keep the artefact, i.e. the trained weights.' },
  { term: 'vs.', spoken: 'versus', context: 'Compare lexical vs. dense retrieval before deciding.' },
  { term: 'vs.', spoken: 'versus', context: 'We measured head vs. rules on the held-out split.' },
  { term: 'etc.', spoken: 'et cetera', context: 'Store weights, metrics, etc. with every run.' },
  { term: 'etc.', spoken: 'et cetera', context: 'Notes, tags, provenance, etc. live on disk.' },
  { term: 'min', spoken: 'minutes', context: 'The training finished in under 3 min on this laptop.' },
  { term: 'min', spoken: 'minutes', context: 'Give it 5 min before judging the rollout.' },
  { term: 'sec', spoken: 'seconds', context: 'Latency p95 stayed below 0.4 sec for the suite.' },
  { term: 'sec', spoken: 'seconds', context: 'Wait 2 sec between the two probes.' },
  { term: 'no.', spoken: 'number', context: 'Open issue no. 42 for the full discussion.' },
  { term: 'no.', spoken: 'number', context: 'Release no. 7 fixes the regression.' },
  { term: 'API', spoken: 'A P I', context: 'The API key is stored server side only.' },
  { term: 'API', spoken: 'A P I', context: 'The editor reads the API directly.' },
  { term: 'GPU', spoken: 'G P U', context: 'A GPU is recommended for adapter fine tuning.' },
  { term: 'GPU', spoken: 'G P U', context: 'The GPU stays idle during head training.' },
  { term: 'CPU', spoken: 'C P U', context: 'The baseline runs on CPU in milliseconds.' },
  { term: 'CPU', spoken: 'C P U', context: 'The CPU time is reported per benchmark run.' },
  { term: 'JSON', spoken: 'J S O N', context: 'Settings are persisted as JSON with atomic writes.' },
  { term: 'JSON', spoken: 'J S O N', context: 'The dataset export is JSON lines.' },
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

export const VOICE_NORMALIZATION_POOL: ExpressionItem[] = [
  { text: 'Training took 42 seconds and improved accuracy by 7%.', expected: 'Training took forty-two seconds and improved accuracy by seven percent.' },
  { text: 'The API returns 404 for unknown routes.', expected: 'The A P I returns four hundred four for unknown routes.' },
  { text: 'Dr. Meyer joined the call at 9 am.', expected: 'Doctor Meyer joined the call at nine am.' },
  { text: 'It costs $15 per month.', expected: 'It costs fifteen dollars per month.' },
  { text: 'The build finished in approx. 3 min.', expected: 'The build finished in approximately three minutes.' },
  { text: 'Open the file README.md first.', expected: 'Open the file README dot m d first.' },
  { text: 'The GPU costs €1200.', expected: 'The G P U costs one thousand two hundred euros.' },
  { text: 'We shipped 3 fixes, e.g. the timeout bug.', expected: 'We shipped three fixes, for example the timeout bug.' },
  { text: 'Recall improved from 0.6 to 0.85.', expected: 'Recall improved from zero point six to zero point eight five.' },
  { text: 'The VM restarted twice today.', expected: 'The virtual machine restarted twice today.' },
  { text: 'Use the SQL index for large tables.', expected: 'Use the sequel index for large tables.' },
  { text: 'The OS reports 8 available cores.', expected: 'The operating system reports eight available cores.' },
  { text: 'Only https endpoints should be fetched.', expected: 'Only H T T P S endpoints should be fetched.' },
  { text: 'Ship the fix ASAP.', expected: 'Ship the fix as soon as possible.' },
  { text: 'Read the FAQ before asking.', expected: 'Read the F A Q before asking.' },
  { text: 'Issue no. 7 was closed.', expected: 'Issue number seven was closed.' },
  { text: 'The LLM answered in 2 sec.', expected: 'The L L M answered in two seconds.' },
  { text: 'Blocking IO stalls the event loop.', expected: 'Blocking I O stalls the event loop.' },
  { text: 'UR welcome to rerun the suite.', expected: 'you are welcome to rerun the suite.' },
  { text: 'Test it w/ the offline engine.', expected: 'Test it with the offline engine.' },
  { text: 'Rejected b/c the quality dropped.', expected: 'Rejected because the quality dropped.' },
  { text: 'Weights are 4 GB in total.', expected: 'Weights are four gigabytes in total.' },
  { text: 'Export the dataset as JSON lines.', expected: 'Export the dataset as JSON lines.' },
  { text: 'Prof. Klein teaches the lecture.', expected: 'Professor Klein teaches the lecture.' },
  { text: 'The SDK ships with typed helpers.', expected: 'The S D K ships with typed helpers.' },
  { text: 'Head vs. rules was measured.', expected: 'Head versus rules was measured.' },
  { text: 'Store weights, metrics, etc. per run.', expected: 'Store weights, metrics, et cetera per run.' },
  { text: 'The CPU time is 12 ms.', expected: 'The C P U time is twelve milliseconds.' },
  { text: 'Latency p95 stayed below 0.4 sec.', expected: 'Latency p95 stayed below zero point four seconds.' },
  { text: '**Latency** is 250 ms today.', expected: 'Latency is two hundred fifty milliseconds today.' },
  { text: 'The head, AKA task head, is linear.', expected: 'The head, also known as task head, is linear.' },
  { text: 'Speed improved by 3.5x.', expected: 'Speed improved by three point five x.' },
  { text: 'Open https://example.com to check.', expected: 'Open H T T P S colon slash slash example dot com to check.' },
  { text: 'Recipes for sampling & caching.', expected: 'Recipes for sampling and caching.' },
  { text: 'The file is 500 mb.', expected: 'The file is five hundred megabytes.' },
  { text: 'Apply the patch i.e. the hotfix.', expected: 'Apply the patch that is the hotfix.' },
  { text: 'HTTP failures are warnings.', expected: 'H T T P failures are warnings.' },
  { text: 'Prof. Nowak sent 2 papers.', expected: 'Professor Nowak sent two papers.' },
  { text: 'Retry b/c the probe timed out.', expected: 'Retry because the probe timed out.' },
  { text: 'The VM has 4 cores.', expected: 'The virtual machine has four cores.' },
];

// ---------------------------------------------------------------------------
// Retrieval corpora
// ---------------------------------------------------------------------------

export const RESEARCH_CORPUS: RetrievalDoc[] = [
  { id: 'r-transformer', title: 'Attention is all you need', text: 'The transformer architecture replaces recurrence with self attention, allowing parallel training and better scaling of sequence models.' },
  { id: 'r-bm25', title: 'Okapi BM25 ranking', text: 'BM25 scores documents with term frequency saturation controlled by k1 and length normalisation controlled by b. It remains a strong lexical baseline.' },
  { id: 'r-rrf', title: 'Reciprocal rank fusion', text: 'Reciprocal rank fusion merges multiple ranked lists by summing the reciprocal of each rank, which is robust without score calibration.' },
  { id: 'r-dense', title: 'Dense retrieval with embeddings', text: 'Dense retrieval encodes queries and passages into vectors and searches with approximate nearest neighbours, capturing paraphrase that lexical matching misses.' },
  { id: 'r-hybrid', title: 'Hybrid retrieval', text: 'Hybrid retrieval combines lexical and dense candidates, usually by fusing ranks; it improves recall when terminology is mixed.' },
  { id: 'r-ablation', title: 'Ablation studies', text: 'An ablation removes one component at a time and re-measures the metric, isolating which change caused an improvement.' },
  { id: 'r-eval', title: 'Evaluation protocols', text: 'A held-out split must never be trained on. Benchmarks report quality, task success, latency and reliability so trade-offs stay visible.' },
  { id: 'r-quant', title: 'Quantisation', text: 'Quantisation stores weights in fewer bits, reducing memory and latency at some cost in accuracy depending on the bit width.' },
  { id: 'r-distill', title: 'Knowledge distillation', text: 'Distillation trains a small model on the outputs of a larger one, transferring behaviour rather than labels.' },
  { id: 'r-overfit', title: 'Overfitting', text: 'A model that memorises its training set scores well there and poorly on held-out data; regularisation and early stopping limit it.' },
  { id: 'r-drift', title: 'Distribution drift', text: 'Drift describes the change of real inputs over time, which slowly degrades the accuracy of a frozen model.' },
  { id: 'r-chunk', title: 'Chunking strategies', text: 'Passages are split into chunks of a few hundred tokens so retrieval returns focused evidence instead of whole documents.' },
  { id: 'r-mrr', title: 'Mean reciprocal rank', text: 'MRR averages the reciprocal of the rank of the first relevant result, emphasising that the best answer should come first.' },
  { id: 'r-recall', title: 'Recall at k', text: 'Recall at k measures which share of the relevant documents appear in the top k results, the primary question for a retriever.' },
  { id: 'r-rerank', title: 'Cross-encoder reranking', text: 'A cross encoder scores query and passage together and reranks a small candidate set, buying precision at extra latency.' },
  { id: 'r-tokenizer', title: 'Tokenisation', text: 'Subword tokenisation balances vocabulary size and sequence length, and it changes how rare technical terms are represented.' },
  { id: 'r-adapter', title: 'Adapter fine tuning', text: 'Adapters add small trainable matrices to a frozen base model, so a task can be learned without updating all parameters.' },
  { id: 'r-citation', title: 'Provenance and citation', text: 'Every stored note keeps its origin, so an answer can always point back to the exact source it was derived from.' },
];

export const RESEARCH_QUERIES: RetrievalQuery[] = [
  { query: 'which scoring function uses k1 and b', relevant: ['r-bm25'] },
  { query: 'how do I combine lexical and dense candidates', relevant: ['r-hybrid'] },
  { query: 'what does recall at k measure', relevant: ['r-recall'] },
  { query: 'how is a held-out split used', relevant: ['r-eval'] },
  { query: 'what is reciprocal rank fusion', relevant: ['r-rrf'] },
  { query: 'which technique shrinks a model by teaching a smaller one', relevant: ['r-distill'] },
  { query: 'how do subword tokens affect rare terms', relevant: ['r-tokenizer'] },
  { query: 'how does drift hurt a frozen model', relevant: ['r-drift'] },
  { query: 'why does a cross encoder cost more latency', relevant: ['r-rerank'] },
  { query: 'what limits memorising the training set', relevant: ['r-overfit'] },
  { query: 'how do adapters avoid updating all parameters', relevant: ['r-adapter'] },
  { query: 'how are passages split for retrieval', relevant: ['r-chunk'] },
  { query: 'what does MRR emphasise about ranking', relevant: ['r-mrr'] },
  { query: 'how do weights get stored in fewer bits', relevant: ['r-quant'] },
];

export const KNOWLEDGE_CORPUS: RetrievalDoc[] = [
  { id: 'k-port', title: 'Server ports', text: 'The AgentOS backend listens on port 4317 and the Vite development server on 4318. Both can be overridden with AGENTOS_PORT and the vite config.' },
  { id: 'k-data-dir', title: 'Data directory', text: 'AGENTOS_DATA_DIR holds settings, memory, datasets, trained models, training runs and benchmark scorecards. Everything there is user data and can be backed up.' },
  { id: 'k-permissions', title: 'Permission model', text: 'Tools declare required permissions. File read, file write, network and shell access are each granted individually in Settings and denied by default except for reads.' },
  { id: 'k-providers', title: 'Model providers', text: 'AgentOS ships a built-in offline engine and can attach Ollama locally or an OpenAI-compatible API. Availability is probed and shown honestly in the UI.' },
  { id: 'k-routing', title: 'Routing rules', text: 'The central assistant routes a request to a specialist by explicit choice, by legacy capability id or by intent keywords. Auto routing can be switched off.' },
  { id: 'k-training', title: 'Training pipeline', text: 'A training run loads a dataset, applies a method, writes checkpoints, evaluates on a held-out split, then produces a model version.' },
  { id: 'k-promotion', title: 'Promotion gate', text: 'A candidate version only becomes active when it beats the current version on the benchmark by the configured minimum improvement. Otherwise the active version stays.' },
  { id: 'k-bench', title: 'Benchmarks', text: 'Benchmark suites measure quality, task success, latency p50 and p95, reliability and resource usage on held-out tasks, and detect regressions against a baseline.' },
  { id: 'k-lexicon', title: 'Speech lexicon', text: 'Voice AI learns abbreviation expansions from aligned sentences, then applies them together with deterministic rules for numbers, currency and symbols.' },
  { id: 'k-retrieval', title: 'Local retrieval index', text: 'Knowledge AI builds a BM25 index over the shipped knowledge base plus your saved notes. Answers are extracted from the index and labelled as such.' },
  { id: 'k-head', title: 'Task heads', text: 'A task head is a compact softmax classifier over hashed n-gram features, trained with mini-batch SGD. It decides which tool or strategy resolves an instruction.' },
  { id: 'k-scope', title: 'What is not local', text: 'Adapter fine tuning of a base language model needs Python, torch and transformers, and runs outside AgentOS. The platform exports datasets for that workflow instead of pretending to run it.' },
  { id: 'k-backup', title: 'Backups', text: 'Back up the data directory before a training sweep. Model artefacts are plain JSON and can be copied between machines.' },
  { id: 'k-latency', title: 'Latency budgets', text: 'Local head decisions take well under a millisecond, retrieval a few milliseconds. Anything that needs prose is escalated and therefore bound by the provider latency.' },
  { id: 'k-notes', title: 'Working with notes', text: 'Notes are stored with a title, content and tags, indexed for search, and injected as grounding context when the selected specialist may search memory.' },
  { id: 'k-addai', title: 'Adding a specialist', text: 'A new specialist is created from a template, gets its own tool bindings, wording and datasets, and appears as its own workspace in the sidebar.' },
  { id: 'k-versions', title: 'Model versions', text: 'Versions are immutable. Promoting one archives the previous version, and a rejected candidate is kept on disk with its metrics as evidence.' },
  { id: 'k-activity', title: 'Activity log', text: 'Tool executions, routing decisions, training events and benchmark runs are recorded per specialist and can be filtered by level.' },
  { id: 'k-settings', title: 'Settings', text: 'Appearance, permissions, providers, routing, training and voice settings are validated on the server and persisted atomically to disk.' },
  { id: 'k-smoke', title: 'Smoke checks', text: 'npm run smoke boots the real backend on a temporary data directory and verifies the API, the training loop and the benchmark gate end to end.' },
];

export const KNOWLEDGE_QUERIES: RetrievalQuery[] = [
  { query: 'which port does the backend use', relevant: ['k-port'] },
  { query: 'where are trained models stored', relevant: ['k-data-dir'] },
  { query: 'how are permissions granted to tools', relevant: ['k-permissions'] },
  { query: 'when does a candidate version become active', relevant: ['k-promotion'] },
  { query: 'what does a benchmark suite measure', relevant: ['k-bench'] },
  { query: 'how does Voice AI expand abbreviations', relevant: ['k-lexicon'] },
  { query: 'can AgentOS fine tune a language model locally', relevant: ['k-scope'] },
  { query: 'how does the assistant pick a specialist', relevant: ['k-routing'] },
  { query: 'what happens during a training run', relevant: ['k-training'] },
  { query: 'how do I create another specialist', relevant: ['k-addai'] },
  { query: 'what are the latency expectations', relevant: ['k-latency'] },
  { query: 'how are model versions kept', relevant: ['k-versions'] },
];

// ---------------------------------------------------------------------------
// Deterministic splitting
// ---------------------------------------------------------------------------

/**
 * Splits a pool into training and evaluation partitions. The evaluation slice
 * is every k-th item, which keeps both sides representative without shuffling
 * (so a pool can be extended later without invalidating earlier splits).
 */
export function splitPool<T>(pool: T[], evalEvery = 3): { train: T[]; evaluation: T[] } {
  const train: T[] = [];
  const evaluation: T[] = [];
  pool.forEach((item, index) => {
    if (index % evalEvery === evalEvery - 1) evaluation.push(item);
    else train.push(item);
  });
  return { train, evaluation };
}

/**
 * Lexicon split: the first sentence of every term trains the induction, every
 * extra sentence with the same term is held out. That measures generalisation
 * to new contexts, which is the only honest question for a lexicon — an unseen
 * abbreviation cannot be guessed.
 */
export function splitLexiconPool(pool: LexiconExample[]): { train: LexiconExample[]; evaluation: LexiconExample[] } {
  const seen = new Set<string>();
  const train: LexiconExample[] = [];
  const evaluation: LexiconExample[] = [];
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
