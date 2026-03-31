import { useRef, useCallback } from 'react';

export function usePhoneAudio() {
  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const restartTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const start = useCallback(async (wsUrl: string): Promise<boolean> => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;

      const ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error('WebSocket failed'));
        setTimeout(() => reject(new Error('WebSocket timeout')), 8000);
      });

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/mp4';

      function startSession() {
        if (!streamRef.current || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

        const recorder = new MediaRecorder(streamRef.current, { mimeType, audioBitsPerSecond: 64000 });
        recorderRef.current = recorder;

        const chunks: Blob[] = [];
        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) chunks.push(e.data);
        };

        recorder.onstop = () => {
          if (chunks.length > 0 && wsRef.current?.readyState === WebSocket.OPEN) {
            const blob = new Blob(chunks, { type: mimeType });
            blob.arrayBuffer().then(buf => {
              wsRef.current?.send(buf);
            });
          }
        };

        recorder.start();
      }

      startSession();

      restartTimer.current = setInterval(() => {
        if (recorderRef.current?.state === 'recording') {
          recorderRef.current.stop();
        }
        startSession();
      }, 5000);

      return true;
    } catch (e) {
      console.error('[PhoneAudio] Failed:', e);
      stop();
      return false;
    }
  }, []);

  const stop = useCallback(() => {
    if (restartTimer.current) {
      clearInterval(restartTimer.current);
      restartTimer.current = null;
    }

    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.stop();
    }
    recorderRef.current = null;

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
