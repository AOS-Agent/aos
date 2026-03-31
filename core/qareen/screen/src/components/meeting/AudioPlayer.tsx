import { useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Play, Pause, RotateCcw, RotateCw } from 'lucide-react';

export interface AudioPlayerHandle {
  seekTo: (seconds: number) => void;
  play: () => void;
}

interface AudioPlayerProps {
  src: string;
}

const AudioPlayer = forwardRef<AudioPlayerHandle, AudioPlayerProps>(({ src }, ref) => {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);

  useImperativeHandle(ref, () => ({
    seekTo: (seconds: number) => {
      if (!audioRef.current) return;
      audioRef.current.currentTime = Math.max(0, Math.min(duration, seconds));
      if (!playing) { audioRef.current.play(); setPlaying(true); }
    },
    play: () => {
      if (!audioRef.current) return;
      audioRef.current.play();
      setPlaying(true);
    },
  }));

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onLoad = () => setDuration(audio.duration || 0);
    const onTime = () => setCurrent(audio.currentTime || 0);
    const onEnd = () => setPlaying(false);
    audio.addEventListener('loadedmetadata', onLoad);
    audio.addEventListener('timeupdate', onTime);
    audio.addEventListener('ended', onEnd);
    return () => {
      audio.removeEventListener('loadedmetadata', onLoad);
      audio.removeEventListener('timeupdate', onTime);
      audio.removeEventListener('ended', onEnd);
    };
  }, []);

  const toggle = () => {
    if (!audioRef.current) return;
    if (playing) audioRef.current.pause(); else audioRef.current.play();
    setPlaying(!playing);
  };
  const skip = (delta: number) => {
    if (!audioRef.current) return;
    audioRef.current.currentTime = Math.max(0, Math.min(duration, audioRef.current.currentTime + delta));
  };
  const seek = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!audioRef.current || !duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    audioRef.current.currentTime = pct * duration;
  };
  const fmt = (s: number) => {
    const t = Math.floor(s);
    return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
  };
  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  return (
    <div className="flex items-center gap-3">
      <audio ref={audioRef} src={src} preload="metadata" />
      <button onClick={() => skip(-10)} className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-hover text-text-quaternary hover:text-text-tertiary cursor-pointer transition-colors" title="Back 10s">
        <RotateCcw className="w-3.5 h-3.5" />
      </button>
      <button onClick={toggle} className="w-9 h-9 flex items-center justify-center rounded-full bg-bg-tertiary hover:bg-bg-quaternary text-text cursor-pointer transition-colors">
        {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
      </button>
      <button onClick={() => skip(10)} className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-hover text-text-quaternary hover:text-text-tertiary cursor-pointer transition-colors" title="Forward 10s">
        <RotateCw className="w-3.5 h-3.5" />
      </button>
      <span className="text-[11px] font-mono text-text-quaternary w-12 text-right shrink-0">{fmt(currentTime)}</span>
      <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full cursor-pointer group relative" onClick={seek}>
        <div className="h-full bg-text-quaternary group-hover:bg-text-tertiary rounded-full transition-colors relative" style={{ width: `${progress}%` }}>
          <div className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 bg-text-secondary rounded-full opacity-0 group-hover:opacity-100 transition-opacity shadow-md" />
        </div>
      </div>
      <span className="text-[11px] font-mono text-text-quaternary w-12 shrink-0">{fmt(duration)}</span>
    </div>
  );
});

AudioPlayer.displayName = 'AudioPlayer';
export default AudioPlayer;
