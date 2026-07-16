/**
 * Global audio transport.
 *
 * A single `<audio>` element lives at the root of the app and plays whatever
 * the user most recently pushed into it. Pages push tracks in with
 * `usePlayer().play(url, title)`, the bar at the bottom of the screen
 * shows waveform + transport controls, and playback survives route
 * changes --- exactly the behaviour a real music app has.
 *
 * Why a context and not per-page <audio> elements? Three reasons:
 *
 *  1. Continuity. The user clicks "Play drums" on the detail page,
 *     navigates to the task list, and the music keeps playing. They
 *     don't expect a SPA to be less capable than a tabbed audio
 *     player from 2008.
 *  2. Single audio stream. If the user starts a new track, the old one
 *     has to stop. Doing that with N independent <audio> elements is
 *     fiddly (each one needs to know about the others); doing it with
 *     a single store is one `audio.src = ...` line.
 *  3. Live state. The bar (and the per-row "is this playing?" badge in
 *     the stem list) both read from the same `isPlaying` flag. They
 *     can never disagree.
 *
 * The state machine is intentionally simple: play/pause/stop/seek. No
 * queue, no shuffle, no crossfade --- the app already has 4-stem
 * separation, an LLM commentary engine, and a sample-based drum
 * player; "Spotify" is not the goal.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

export interface PlayerTrack {
  /** Public URL the <audio> element can fetch (relative or absolute). */
  url: string;
  /** Display title (stem name, sample name, ...). */
  title: string;
  /** Optional kind badge for the bar ("audio" / "midi" / "sample"). */
  kind?: string;
}

interface PlayerState {
  current: PlayerTrack | null;
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  volume: number;
  /** True while the audio is buffering the new src. */
  loading: boolean;
}

interface PlayerControls extends PlayerState {
  play: (track: PlayerTrack) => void;
  toggle: () => void;
  pause: () => void;
  stop: () => void;
  seek: (timeSec: number) => void;
  setVolume: (vol: number) => void;
}

const PlayerContext = createContext<PlayerControls | null>(null);

/**
 * Format a time in seconds as `m:ss` (or `h:mm:ss` past the hour mark).
 * Used by the transport bar's time display.
 */
function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = s.toString().padStart(2, "0");
  if (h > 0) {
    const mm = m.toString().padStart(2, "0");
    return `${h}:${mm}:${ss}`;
  }
  return `${m}:${ss}`;
}

export { formatTime };

interface PlayerProviderProps {
  children: ReactNode;
}

export function PlayerProvider({ children }: PlayerProviderProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [current, setCurrent] = useState<PlayerTrack | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolumeState] = useState(1);
  const [loading, setLoading] = useState(false);

  // The audio element lives once for the whole app. We create it lazily
  // (on the client) and wire DOM events to local state.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const audio = new Audio();
    audio.preload = "metadata";
    audioRef.current = audio;

    const onTimeUpdate = () => setCurrentTime(audio.currentTime);
    const onLoadedMetadata = () => setDuration(audio.duration || 0);
    const onDurationChange = () => setDuration(audio.duration || 0);
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onEnded = () => {
      setIsPlaying(false);
      setCurrentTime(0);
    };
    const onWaiting = () => setLoading(true);
    const onCanPlay = () => setLoading(false);
    const onError = () => {
      setLoading(false);
      setIsPlaying(false);
    };

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("loadedmetadata", onLoadedMetadata);
    audio.addEventListener("durationchange", onDurationChange);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("waiting", onWaiting);
    audio.addEventListener("canplay", onCanPlay);
    audio.addEventListener("error", onError);

    return () => {
      audio.pause();
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("loadedmetadata", onLoadedMetadata);
      audio.removeEventListener("durationchange", onDurationChange);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("waiting", onWaiting);
      audio.removeEventListener("canplay", onCanPlay);
      audio.removeEventListener("error", onError);
    };
  }, []);

  const play = useCallback((track: PlayerTrack) => {
    const audio = audioRef.current;
    if (!audio) return;

    // If the user clicks play on the track that's already loaded, treat
    // it as a resume from pause.
    if (current && current.url === track.url) {
      audio.play().catch(() => {
        // Autoplay can be blocked until the user clicks the page; that's
        // fine --- they'll see the bar in a paused state and can hit play.
      });
      return;
    }

    setCurrent(track);
    setLoading(true);
    audio.src = track.url;
    audio.currentTime = 0;
    audio.play().catch(() => {
      // Browser autoplay policy. The user can hit play again.
    });
  }, [current]);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !current) return;
    if (audio.paused) {
      audio.play().catch(() => {});
    } else {
      audio.pause();
    }
  }, [current]);

  const pause = useCallback(() => {
    audioRef.current?.pause();
  }, []);

  const stop = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    setCurrent(null);
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setLoading(false);
  }, []);

  const seek = useCallback((timeSec: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, Math.min(timeSec, audio.duration || 0));
    setCurrentTime(audio.currentTime);
  }, []);

  const setVolume = useCallback((vol: number) => {
    const clamped = Math.max(0, Math.min(1, vol));
    setVolumeState(clamped);
    if (audioRef.current) {
      audioRef.current.volume = clamped;
    }
  }, []);

  const value = useMemo<PlayerControls>(
    () => ({
      current,
      isPlaying,
      currentTime,
      duration,
      volume,
      loading,
      play,
      toggle,
      pause,
      stop,
      seek,
      setVolume,
    }),
    [
      current,
      isPlaying,
      currentTime,
      duration,
      volume,
      loading,
      play,
      toggle,
      pause,
      stop,
      seek,
      setVolume,
    ],
  );

  return <PlayerContext.Provider value={value}>{children}</PlayerContext.Provider>;
}

export function usePlayer(): PlayerControls {
  const ctx = useContext(PlayerContext);
  if (!ctx) {
    throw new Error("usePlayer must be used inside <PlayerProvider>");
  }
  return ctx;
}
