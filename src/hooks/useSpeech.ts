import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/**
 * Thin wrapper around the platform Web Speech API. Availability is probed at
 * runtime so the UI can honestly report "not supported in this browser".
 */
type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onend: (() => void) | null;
};

function getRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface UseSpeechResult {
  sttSupported: boolean;
  ttsSupported: boolean;
  listening: boolean;
  transcript: string;
  speechError: string | null;
  startListening: () => void;
  stopListening: () => void;
  speak: (text: string) => void;
  cancelSpeech: () => void;
  voices: string[];
}

export function useSpeech(): UseSpeechResult {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [speechError, setSpeechError] = useState<string | null>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

  const sttSupported = useMemo(() => Boolean(getRecognitionCtor()), []);
  // `'speechSynthesis' in window` is not enough: the property may exist but be
  // empty/undefined (jsdom, hardened browsers), so verify the actual API.
  const ttsSupported = useMemo(() => {
    const synth = typeof window !== 'undefined' ? window.speechSynthesis : undefined;
    return Boolean(synth && typeof synth.speak === 'function');
  }, []);

  useEffect(() => {
    const synth = window.speechSynthesis;
    if (!ttsSupported || !synth || typeof synth.getVoices !== 'function') return;
    const load = () => setVoices(synth.getVoices().map((v) => v.name));
    load();
    synth.addEventListener?.('voiceschanged', load);
    return () => synth.removeEventListener?.('voiceschanged', load);
  }, [ttsSupported]);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    recognitionRef.current = null;
    setListening(false);
  }, []);

  const startListening = useCallback(() => {
    const Ctor = getRecognitionCtor();
    if (!Ctor) {
      setSpeechError('Speech recognition is not available in this browser.');
      return;
    }
    try {
      const recognition = new Ctor();
      recognition.lang = 'en-US';
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.onresult = (event: unknown) => {
        const e = event as { results: ArrayLike<ArrayLike<{ transcript: string }>> };
        let text = '';
        for (let i = 0; i < e.results.length; i++) {
          text += e.results[i][0]?.transcript ?? '';
        }
        setTranscript(text);
      };
      recognition.onerror = (event: unknown) => {
        const e = event as { error?: string };
        setSpeechError(
          e.error === 'not-allowed'
            ? 'Microphone permission was denied by the browser.'
            : `Speech recognition error: ${e.error ?? 'unknown'}`,
        );
        setListening(false);
      };
      recognition.onend = () => setListening(false);
      recognitionRef.current = recognition;
      setSpeechError(null);
      setTranscript('');
      recognition.start();
      setListening(true);
    } catch (e) {
      setSpeechError(e instanceof Error ? e.message : String(e));
      setListening(false);
    }
  }, []);

  const speak = useCallback(
    (text: string) => {
      const synth = window.speechSynthesis;
      if (!ttsSupported || !synth) {
        setSpeechError('Speech synthesis is not available in this browser.');
        return;
      }
      try {
        synth.cancel();
        const utterance = new SpeechSynthesisUtterance(text.slice(0, 2000));
        utterance.rate = 1;
        synth.speak(utterance);
      } catch (e) {
        setSpeechError(e instanceof Error ? e.message : String(e));
      }
    },
    [ttsSupported],
  );

  const cancelSpeech = useCallback(() => {
    if (ttsSupported) window.speechSynthesis?.cancel();
  }, [ttsSupported]);

  useEffect(
    () => () => {
      recognitionRef.current?.stop();
      window.speechSynthesis?.cancel();
    },
    [],
  );

  return {
    sttSupported,
    ttsSupported,
    listening,
    transcript,
    speechError,
    startListening,
    stopListening,
    speak,
    cancelSpeech,
    voices,
  };
}
