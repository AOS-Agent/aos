import { useRef, useCallback } from 'react';

const TARGET_RATE = 16000;
const CHUNK_MS = 100; // send every 100ms

export function useLiveMic() {
  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);

  const start = useCallback(async (wsUrl: string): Promise<boolean> => {
    try {
      // Request mic — let browser choose sample rate (Safari ignores constraints)
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      // Create AudioContext — Safari needs webkit prefix sometimes
      const AC = window.AudioContext || (window as any).webkitAudioContext;
      const ctx = new AC();
      contextRef.current = ctx;

      // Resume context (Safari requires this after user gesture)
      if (ctx.state === 'suspended') {
        await ctx.resume();
      }

      const source = ctx.createMediaStreamSource(stream);
      const nativeSampleRate = ctx.sampleRate;

      // Use ScriptProcessorNode — works on ALL browsers including Safari iOS
      // (AudioWorklet + Blob URL fails on Safari)
      const bufferSize = 4096;
      const processor = ctx.createScriptProcessor(bufferSize, 1, 1);
      processorRef.current = processor;

      // Connect WebSocket
      const ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error('WebSocket connection failed'));
        setTimeout(() => reject(new Error('WebSocket timeout')), 8000);
      });

      // Resample buffer for target rate
      const resampleRatio = TARGET_RATE / nativeSampleRate;

      processor.onaudioprocess = (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        const input = e.inputBuffer.getChannelData(0);

        // Resample if needed
        let audio: Float32Array;
        if (Math.abs(resampleRatio - 1.0) < 0.01) {
          audio = new Float32Array(input);
        } else {
          const outputLength = Math.round(input.length * resampleRatio);
          audio = new Float32Array(outputLength);
          for (let i = 0; i < outputLength; i++) {
            const srcIdx = i / resampleRatio;
            const low = Math.floor(srcIdx);
            const high = Math.min(low + 1, input.length - 1);
            const frac = srcIdx - low;
            audio[i] = input[low] * (1 - frac) + input[high] * frac;
          }
        }

        // Send with 8-byte header (sample_rate u32 LE + num_samples u32 LE)
        const header = new ArrayBuffer(8);
        const view = new DataView(header);
        view.setUint32(0, TARGET_RATE, true);
        view.setUint32(4, audio.length, true);
        const payload = new Uint8Array(8 + audio.byteLength);
        payload.set(new Uint8Array(header), 0);
        payload.set(new Uint8Array(audio.buffer, audio.byteOffset, audio.byteLength), 8);
        ws.send(payload.buffer);
      };

      source.connect(processor);
      processor.connect(ctx.destination); // Required for ScriptProcessor to fire

      console.log(`[LiveMic] Streaming at ${nativeSampleRate}Hz → resampled to ${TARGET_RATE}Hz`);
      return true;
    } catch (e) {
      console.error('[LiveMic] Failed:', e);
      stop();
      return false;
    }
  }, []);

  const stop = useCallback(() => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (contextRef.current) {
      contextRef.current.close().catch(() => {});
      contextRef.current = null;
    }
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      try { wsRef.current.send(JSON.stringify({ type: 'end' })); } catch {}
      wsRef.current.close();
    }
    wsRef.current = null;
  }, []);

  return { start, stop };
}
