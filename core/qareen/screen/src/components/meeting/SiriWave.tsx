import { useRef, useEffect } from 'react';

interface SiriWaveProps {
  getAmplitude: () => number;
  width?: number;
  height?: number;
  color?: string;
  className?: string;
}

const ATT = 4;
const globalAtt = (x: number) => Math.pow(ATT / (ATT + x * x), ATT);

export default function SiriWave({ getAmplitude, width = 400, height = 100, color = '#D9730D', className = '' }: SiriWaveProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const phaseRef = useRef(0);
  const ampRef = useRef(0.03);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d')!;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    const r = parseInt(color.slice(1, 3), 16);
    const g = parseInt(color.slice(3, 5), 16);
    const b = parseInt(color.slice(5, 7), 16);

    const waves = [
      { freqMul: 0.8, width: 1, opaMul: 0.08 },
      { freqMul: 1.2, width: 1, opaMul: 0.15 },
      { freqMul: 0.6, width: 1, opaMul: 0.25 },
      { freqMul: 1.0, width: 1.2, opaMul: 0.45 },
      { freqMul: 1.0, width: 1.8, opaMul: 1.0 },
    ];
    const FREQUENCY = 6, PIXEL_STEP = 0.02, LERP_SPEED = 0.12, SPEED = 0.25;

    function draw() {
      const rawAmp = getAmplitude();
      const target = Math.max(rawAmp * 3.5, 0.03);
      ampRef.current += (target - ampRef.current) * LERP_SPEED;
      const amp = Math.min(ampRef.current, 1);
      phaseRef.current = (phaseRef.current + Math.PI / 2 * SPEED) % (2 * Math.PI);
      ctx.clearRect(0, 0, width, height);
      const halfH = height / 2;
      const maxAmpPx = halfH * 0.75;
      ctx.globalCompositeOperation = 'lighter';
      for (let wi = 0; wi < waves.length; wi++) {
        const wave = waves[wi];
        const opacity = wave.opaMul * (0.3 + amp * 0.7);
        ctx.beginPath();
        ctx.lineWidth = wave.width;
        const grad = ctx.createLinearGradient(0, 0, width, 0);
        grad.addColorStop(0, `rgba(${r},${g},${b},${opacity * 0.1})`);
        grad.addColorStop(0.25, `rgba(${r},${g},${b},${opacity * 0.7})`);
        grad.addColorStop(0.5, `rgba(${r},${g},${b},${opacity})`);
        grad.addColorStop(0.75, `rgba(${r},${g},${b},${opacity * 0.7})`);
        grad.addColorStop(1, `rgba(${r},${g},${b},${opacity * 0.1})`);
        ctx.strokeStyle = grad;
        if (wi === waves.length - 1) {
          ctx.shadowColor = `rgba(${r},${g},${b},${0.5 * amp})`;
          ctx.shadowBlur = 16 * amp;
        } else {
          ctx.shadowColor = 'transparent';
          ctx.shadowBlur = 0;
        }
        const wavePhase = phaseRef.current + wi * 0.35;
        const freq = FREQUENCY * wave.freqMul;
        for (let i = -2; i <= 2; i += PIXEL_STEP) {
          const x = (i + 2) / 4 * width;
          const att = globalAtt(i);
          const y = halfH + amp * maxAmpPx * att * Math.sin(freq * i - wavePhase);
          if (i === -2) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      ctx.globalCompositeOperation = 'source-over';
      rafRef.current = requestAnimationFrame(draw);
    }
    rafRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(rafRef.current);
  }, [getAmplitude, width, height, color]);

  return <canvas ref={canvasRef} style={{ width, height }} className={`block ${className}`} />;
}
