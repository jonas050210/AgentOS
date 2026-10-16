import { useMemo, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import { useSystem } from '@/state/SystemContext';
import { Badge, Card, Chip, ErrorBanner, KeyValue, Toggle } from '@/components/ui';
import type { SpecialistCategory } from '@shared/types';
import type { NavTarget } from '@/components/Sidebar';

/**
 * Registering a specialist.
 *
 * A new specialist starts untrained, and this screen says so before it is
 * created: which datasets will fit it, which training method can run here, and
 * which tools it will be allowed to call. Nothing is promised that the platform
 * cannot then deliver.
 */

interface Template {
  id: string;
  label: string;
  category: SpecialistCategory;
  tagline: string;
  description: string;
  accent: string;
  glyph: string;
  systemPrompt: string;
  toolIds: string[];
  trainingHint: string;
}

const TEMPLATES: Template[] = [
  {
    id: 'coding',
    label: 'Coding AI',
    category: 'coding',
    tagline: 'Reads the workspace and computes',
    description:
      'Decides between reading files, listing directories, arithmetic, system inspection and memory, then answers with those results.',
    accent: '#6ea8ff',
    glyph: 'CD',
    systemPrompt: 'You are a coding specialist inside AgentOS. Prefer inspecting the workspace over guessing, and state uncertainty plainly.',
    toolIds: ['workspace.read', 'workspace.list', 'calc.evaluate', 'system.info', 'memory.save', 'memory.search'],
    trainingHint: 'Trained with a softmax task head over instruction → tool decisions (SGD on hashed n-gram features).',
  },
  {
    id: 'research',
    label: 'Research AI',
    category: 'research',
    tagline: 'Fetches sources, cites what it read',
    description:
      'Routes between reading a link, consulting saved notes and answering directly, then extracts the target and fetches it when permission allows.',
    accent: '#8f7bff',
    glyph: 'RE',
    systemPrompt: 'You are a research specialist inside AgentOS. Cite what you actually retrieved and never invent a source.',
    toolIds: ['http.fetch', 'memory.search', 'memory.save', 'workspace.read'],
    trainingHint: 'Trained with a softmax task head over research action decisions plus a ledger of tasks it cannot resolve.',
  },
  {
    id: 'knowledge',
    label: 'Knowledge AI',
    category: 'knowledge',
    tagline: 'Answers from your own notes',
    description:
      'Retrieves passages from your notes and the curated corpus with BM25 and returns the ranked evidence instead of generated prose.',
    accent: '#3ecf8e',
    glyph: 'KN',
    systemPrompt: 'You are a knowledge specialist inside AgentOS. Answer from retrieved passages and name the note you used.',
    toolIds: ['memory.search', 'memory.save', 'workspace.read'],
    trainingHint: 'Trained by building a BM25 retrieval index — no gradient descent, and the UI says index construction, not fine-tuning.',
  },
  {
    id: 'voice',
    label: 'Voice AI',
    category: 'voice',
    tagline: 'Speaks the output as a human would',
    description:
      'Learns how abbreviations and symbols are actually spoken in your domain, then normalises text for speech synthesis.',
    accent: '#e5b567',
    glyph: 'VO',
    systemPrompt: 'You are a voice specialist inside AgentOS. Produce text that reads correctly when spoken aloud.',
    toolIds: ['memory.search', 'memory.save'],
    trainingHint: 'Trained by lexicon induction: candidate readings are counted, scored for agreement and pruned on held-out contexts.',
  },
  {
    id: 'general',
    label: 'General AI',
    category: 'general',
    tagline: 'The catch-all and the default route',
    description:
      'Handles anything that does not clearly belong to another specialist, and takes every request when auto-routing is switched off.',
    accent: '#7b87a3',
    glyph: 'GE',
    systemPrompt: 'You are the general specialist inside AgentOS. Be direct, and use a tool when one resolves the request better than prose.',
    toolIds: ['calc.evaluate', 'system.info', 'workspace.read', 'workspace.list', 'memory.save', 'memory.search', 'http.fetch'],
    trainingHint: 'Trained with a softmax task head over the wider eight-way tool selection corpus.',
  },
];

export function AddAIView({ onOpen }: { onOpen: (target: NavTarget) => void }) {
  const { tools, datasets, methods, refreshPlatform, specialists, settings, saveSettings } = useSystem();
  const [templateId, setTemplateId] = useState<string>('coding');
  const [name, setName] = useState('');
  const [tagline, setTagline] = useState('');
  const [description, setDescription] = useState('');
  const [toolIds, setToolIds] = useState<string[]>(TEMPLATES[0].toolIds);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ id: string; name: string } | null>(null);
  const [enabled, setEnabled] = useState(true);

  const template = TEMPLATES.find((entry) => entry.id === templateId) ?? TEMPLATES[0];

  function pick(next: Template) {
    setTemplateId(next.id);
    setToolIds(next.toolIds);
    setCreated(null);
    setError(null);
  }

  const suggestedDatasets = useMemo(
    () => datasets.filter((dataset) => dataset.specialistId === `custom` || dataset.specialistId.includes(template.category)),
    [datasets, template.category],
  );
  const usableMethods = useMemo(
    () => methods.filter((method) => method.available && method.appliesTo.includes(template.category)),
    [methods, template.category],
  );

  async function create() {
    if (creating) return;
    setCreating(true);
    setError(null);
    try {
      const definition = await api.createSpecialist({
        name: name.trim() || template.label,
        tagline: tagline.trim() || template.tagline,
        description: description.trim() || template.description,
        category: template.category,
        accent: template.accent,
        glyph: template.glyph,
        templateId: template.id,
        toolIds,
        systemPrompt: template.systemPrompt,
      });
      if (!enabled) await api.updateSpecialist(definition.id, { enabled: false });
      setCreated({ id: definition.id, name: definition.name });
      void refreshPlatform();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  const availableTools = tools.map((tool) => ({ id: tool.id, name: tool.name, description: tool.description }));

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-[1.05fr_1fr]">
        <Card title="Choose a starting point" subtitle="A template sets the tool set, the system prompt and the training method that fits this kind of work.">
          <div className="grid gap-2 sm:grid-cols-2">
            {TEMPLATES.map((entry) => {
              const active = entry.id === templateId;
              const instances = specialists.filter((specialist) => specialist.category === entry.category).length;
              return (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => pick(entry)}
                  aria-pressed={active}
                  className={`glass-subtle tile-hover p-3 text-left transition-shadow ${active ? 'ring-1 ring-accent-400/60' : ''}`}
                >
                  <span className="flex items-center gap-2">
                    <span
                      className="flex h-7 w-7 items-center justify-center rounded-lg text-[10px] font-semibold"
                      style={{ background: `${entry.accent}22`, color: entry.accent, boxShadow: `inset 0 0 0 1px ${entry.accent}44` }}
                      aria-hidden
                    >
                      {entry.glyph}
                    </span>
                    <span className="text-xs font-medium text-slate-100">{entry.label}</span>
                  </span>
                  <span className="mt-1.5 block text-2xs leading-relaxed text-slate-400">{entry.tagline}</span>
                  <span className="mt-1.5 block text-[10px] text-slate-500">{instances} registered with this category</span>
                </button>
              );
            })}
          </div>

          <div className="glass-subtle mt-4 p-3">
            <p className="eyebrow mb-1">What this template promises</p>
            <p className="text-xs leading-relaxed text-slate-300">{template.description}</p>
            <p className="mt-1.5 text-2xs leading-relaxed text-slate-500">{template.trainingHint}</p>
          </div>
        </Card>

        <Card title="Describe it" subtitle="Names and descriptions are yours; the capabilities come from the template and can be changed later.">
          <div className="space-y-3">
            <label className="block">
              <span className="label">Name</span>
              <input className="input" value={name} placeholder={template.label} onChange={(event) => setName(event.target.value)} />
            </label>
            <label className="block">
              <span className="label">Tagline</span>
              <input className="input" value={tagline} placeholder={template.tagline} onChange={(event) => setTagline(event.target.value)} />
            </label>
            <label className="block">
              <span className="label">Description</span>
              <textarea
                className="input min-h-[80px] resize-none"
                value={description}
                placeholder={template.description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            <Toggle
              label="Enable immediately"
              description="A disabled specialist is listed but refuses requests until re-enabled."
              checked={enabled}
              onChange={setEnabled}
            />
          </div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Tools it may call" subtitle="Only these tools are available to this specialist — the rest stay out of reach.">
          <div className="flex flex-wrap gap-2">
            {availableTools.map((tool) => {
              const active = toolIds.includes(tool.id);
              return (
                <Chip
                  key={tool.id}
                  active={active}
                  title={tool.description}
                  onClick={() => setToolIds((prev) => (active ? prev.filter((id) => id !== tool.id) : [...prev, tool.id]))}
                >
                  {tool.id}
                </Chip>
              );
            })}
          </div>
          <p className="mt-3 text-2xs leading-relaxed text-slate-500">
            {toolIds.length} selected. If the permissions in Settings block one of them, the specialist is shown as degraded when it
            needs that tool — not silently broken.
          </p>
        </Card>

        <Card title="How it will be trained" subtitle="Methods that can actually run for this category on this machine.">
          {usableMethods.length === 0 ? (
            <p className="text-xs text-slate-500">No training method currently applies to this category.</p>
          ) : (
            <div className="space-y-3">
              {usableMethods.map((method) => (
                <div key={method.id} className="glass-subtle p-3">
                  <p className="flex items-center gap-2 text-xs text-slate-200">
                    {method.name}
                    <Badge tone="ok">{method.runtime}</Badge>
                  </p>
                  <p className="mt-1 text-2xs leading-relaxed text-slate-400">{method.artifactKind}</p>
                  <p className="mt-1 text-2xs leading-relaxed text-slate-500">{method.honesty}</p>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card title="Datasets it can learn from" subtitle="A specialist with no data in its category starts with user examples you capture.">
          {suggestedDatasets.length === 0 ? (
            <p className="text-xs leading-relaxed text-slate-500">
              No shipped dataset matches this category yet. Capture examples in the specialist's test bench — those become its first
              training data.
            </p>
          ) : (
            <div className="space-y-2">
              {suggestedDatasets.map((dataset) => (
                <div key={dataset.id} className="glass-subtle px-3 py-2 text-2xs">
                  <p className="text-slate-300">{dataset.name}</p>
                  <p className="mt-0.5 text-slate-500">
                    {dataset.size} items · {dataset.labels.length} labels · {dataset.provenance}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="Create" subtitle="The specialist is registered untrained; train it from the Training Lab.">
        {error && <div className="mb-3">{<ErrorBanner message={error} onDismiss={() => setError(null)} />}</div>}
        {created ? (
          <div className="space-y-3">
            <p className="rounded-xl border border-signal-ok/25 bg-signal-ok/[0.07] px-4 py-3 text-xs text-signal-ok">
              {created.name} is registered and starts as untrained — no model pretends to exist for it yet.
            </p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary" onClick={() => onOpen({ view: 'specialist', specialistId: created.id })}>
                Open its workspace
              </button>
              <button type="button" className="btn-ghost" onClick={() => onOpen({ view: 'training' })}>
                Train it now
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="glass-subtle px-3 py-2.5">
              <KeyValue label="Template">{template.label}</KeyValue>
              <KeyValue label="Category">{template.category}</KeyValue>
              <KeyValue label="Tools">{toolIds.length}</KeyValue>
              <KeyValue label="Initial state">untrained</KeyValue>
              <KeyValue label="Auto-benchmark after training">
                {settings?.training.autoBenchmark ? 'on' : 'off'}{' '}
                <button
                  type="button"
                  className="ml-1 text-accent-300 hover:text-accent-200"
                  onClick={() =>
                    settings && void saveSettings({ training: { ...settings.training, autoBenchmark: !settings.training.autoBenchmark } })
                  }
                >
                  toggle
                </button>
              </KeyValue>
            </div>
            <button type="button" className="btn-primary" onClick={() => void create()} disabled={creating}>
              {creating ? 'Creating…' : `Create ${name.trim() || template.label}`}
            </button>
          </div>
        )}
      </Card>
    </div>
  );
}
