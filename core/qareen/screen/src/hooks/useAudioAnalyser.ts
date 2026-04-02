import { useRef, useCallback } from 'react';

export function useAudioAnalyser() {
  const analyserRef = useRef<AnalyserNode | null>(null);
  const dataArrayRef = useRef<Uint8Array | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);

  const start = useCallback(async () => {
    // Clean up any previous instance
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
    }
    if (ctxRef.current) {
      try { await ctxRef.current.close(); } catch {}
    }

    try {
      // Try default device first
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (e) {
        // If default fails, enumerate devices and try the first audio input
        console.warn('[AudioAnalyser] Default device failed, enumerating...', e);
        const devices = await navigator.mediaDevices.enumerateDevices();
        const audioInput = devices.find(d => d.kind === 'audioinput');
        if (!audioInput) {
          console.warn('[AudioAnalyser] No audio input devices found');
          return false;
        }
        console.log('[AudioAnalyser] Trying device:', audioInput.label || audioInput.deviceId);
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { deviceId: { exact: audioInput.deviceId } }
        });
      }

      const audioCtx = new AudioContext();
      // Resume if suspended (Chrome autoplay policy)
      if (audioCtx.state === 'suspended') {
        await audioCtx.resume();
      }

      ctxRef.current = audioCtx;
      streamRef.current = stream;

      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.8;

      source.connect(analyser);

      analyserRef.current = analyser;
      dataArrayRef.current = new Uint8Array(analyser.frequencyBinCount);
      console.log('[AudioAnalyser] Started — device:', stream.getAudioTracks()[0]?.label);
      return true;
    } catch (e) {
      console.warn('[AudioAnalyser] Failed to start:', e);
      return false;
    }
  }, []);

  const getAmplitude = useCallback((): number => {
    const analyser = analyserRef.current;
    const dataArray = dataArrayRef.current;
    if (!analyser || !dataArray) return 0;

    analyser.getByteTimeDomainData(dataArray);

    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) {
      const normalized = (dataArray[i] - 128) / 128;
      sum += normalized * normalized;
    }
    return Math.sqrt(sum / dataArray.length);
  }, []);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
    analyserRef.current = null;
    dataArrayRef.current = null;
  }, []);

  return { start, stop, getAmplitude };
}
