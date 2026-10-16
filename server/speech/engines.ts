/**
 * Speech subsystem. Engines are resolved at runtime; nothing is assumed to be
 * installed. The browser engine relies on the platform Web Speech API and is
 * therefore only usable from the desktop client.
 */
export interface SpeechEngineStatus {
  id: string;
  name: string;
  kind: 'stt' | 'tts';
  available: boolean;
  location: 'client' | 'server';
  detail: string;
}

export interface SpeechProvider {
  readonly id: string;
  readonly name: string;
  readonly kind: 'stt' | 'tts';
  status(): Promise<SpeechEngineStatus>;
}

export class BrowserSpeechEngine implements SpeechProvider {
  readonly kind: 'stt' | 'tts';
  readonly id: string;
  readonly name: string;

  constructor(kind: 'stt' | 'tts') {
    this.kind = kind;
    this.id = `browser-${kind}`;
    this.name = kind === 'stt' ? 'Browser SpeechRecognition' : 'Browser SpeechSynthesis';
  }

  async status(): Promise<SpeechEngineStatus> {
    return {
      id: this.id,
      name: this.name,
      kind: this.kind,
      // The server cannot introspect browser capabilities — the client reports back.
      available: true,
      location: 'client',
      detail: 'Provided by the desktop client runtime (Web Speech API)',
    };
  }
}

export class ServerSpeechEngine implements SpeechProvider {
  constructor(
    readonly id: string,
    readonly name: string,
    readonly kind: 'stt' | 'tts',
    private probe: () => Promise<boolean>,
    private detailFn: () => string,
  ) {}

  async status(): Promise<SpeechEngineStatus> {
    let available = false;
    try {
      available = await this.probe();
    } catch {
      available = false;
    }
    return {
      id: this.id,
      name: this.name,
      kind: this.kind,
      available,
      location: 'server',
      detail: this.detailFn(),
    };
  }
}

export const speechEngines: SpeechProvider[] = [
  new BrowserSpeechEngine('stt'),
  new BrowserSpeechEngine('tts'),
  new ServerSpeechEngine(
    'whisper-server',
    'Whisper (server-side)',
    'stt',
    async () => false,
    () => 'Not implemented in this milestone — interface ready, engine unconfigured',
  ),
  new ServerSpeechEngine(
    'piper-server',
    'Piper (server-side)',
    'tts',
    async () => false,
    () => 'Not implemented in this milestone — interface ready, engine unconfigured',
  ),
];

export async function speechStatus(): Promise<SpeechEngineStatus[]> {
  return Promise.all(speechEngines.map((e) => e.status()));
}
