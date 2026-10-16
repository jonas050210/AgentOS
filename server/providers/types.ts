import { ProviderInfo, ModelInfo } from '../../shared/types.js';

export interface GenerateRequest {
  model: string;
  prompt: string;
  system?: string;
  temperature?: number;
  maxTokens?: number;
}

export interface GenerateResponse {
  text: string;
  model: string;
  providerId: string;
  usage?: { promptTokens?: number; completionTokens?: number };
}

export interface ModelProvider {
  readonly id: string;
  readonly name: string;
  readonly type: 'local' | 'cloud' | 'remote';
  info(): ProviderInfo;
  isAvailable(): Promise<boolean>;
  listModels(): Promise<ModelInfo[]>;
  generate(req: GenerateRequest): Promise<GenerateResponse>;
}
