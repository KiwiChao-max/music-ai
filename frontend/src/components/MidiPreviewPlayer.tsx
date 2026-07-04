/**
 * Minimal MIDI file parser + Web Audio synthesizer for in-browser preview.
 *
 * Parses standard MIDI (format 0/1) into a flat note array with absolute
 * times, then schedules them on an AudioContext using simple oscillators
 * shaped by per-program ADSR envelopes. Good enough for previewing melodic
 * stems in the browser; not intended to replace a full GM synth.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "@/api/axios";

interface MidiNote {
  note: number;
  start: number;
  duration: number;
  velocity: number;
  program: number;
}

interface MidiData {
  notes: MidiNote[];
  duration: number;
  trackCount: number;
}

const _activePlayers = new Set<() => void>();

function stopOtherPlayers(self: () => void) {
  for (const stop of _activePlayers) {
    if (stop !== self) stop();
  }
}

function midiToFreq(note: number): number {
  return 440 * Math.pow(2, (note - 69) / 12);
}

function getOscType(program: number): OscillatorType {
  if (program >= 40 && program <= 51) return "sawtooth";
  if (program >= 80 && program <= 87) return "square";
  if (program >= 56 && program <= 79) return "sawtooth";
  if (program >= 32 && program <= 39) return "sawtooth";
  if (program >= 24 && program <= 31) return "triangle";
  return "triangle";
}

function getEnvelope(program: number): { attack: number; decay: number; sustain: number; release: number } {
  if (program >= 40 && program <= 47) return { attack: 0.08, decay: 0.1, sustain: 0.7, release: 0.2 };
  if (program >= 48 && program <= 55) return { attack: 0.15, decay: 0.1, sustain: 0.8, release: 0.3 };
  if (program >= 80 && program <= 87) return { attack: 0.01, decay: 0.05, sustain: 0.8, release: 0.05 };
  if (program >= 88 && program <= 95) return { attack: 0.2, decay: 0.3, sustain: 0.7, release: 0.5 };
  if (program >= 32 && program <= 39) return { attack: 0.01, decay: 0.1, sustain: 0.8, release: 0.1 };
  if (program >= 24 && program <= 31) return { attack: 0.005, decay: 0.05, sustain: 0.6, release: 0.05 };
  return { attack: 0.005, decay: 0.08, sustain: 0.6, release: 0.08 };
}

function getFilterFreq(program: number): number {
  if (program >= 32 && program <= 39) return 800;
  if (program >= 88 && program <= 95) return 2000;
  return 4000;
}

function parseMidi(buffer: ArrayBuffer): MidiData {
  const view = new DataView(buffer);
  let offset = 0;

  const readVarLen = (): number => {
    let value = 0;
    let byte;
    let guards = 0;
    do {
      byte = view.getUint8(offset++);
      value = (value << 7) | (byte & 0x7f);
      guards++;
      if (guards > 4) break;
    } while (byte & 0x80);
    return value;
  };

  const magic = String.fromCharCode(
    view.getUint8(offset), view.getUint8(offset + 1),
    view.getUint8(offset + 2), view.getUint8(offset + 3),
  );
  offset += 4;
  const headerLen = view.getUint32(offset);
  offset += 4;
  const format = view.getUint16(offset);
  offset += 2;
  const numTracks = view.getUint16(offset);
  offset += 2;
  const division = view.getUint16(offset);
  offset += 2;
  if (magic !== "MThd") throw new Error("Not a MIDI file");
  void headerLen;
  void format;

  const ticksPerBeat = division & 0x8000 ? 480 : division;
  const notes: MidiNote[] = [];
  let maxTick = 0;
  const tempoChanges: { tick: number; usPerBeat: number }[] = [{ tick: 0, usPerBeat: 500000 }];

  for (let t = 0; t < numTracks; t++) {
    const trackMagic = String.fromCharCode(
      view.getUint8(offset), view.getUint8(offset + 1),
      view.getUint8(offset + 2), view.getUint8(offset + 3),
    );
    offset += 4;
    const trackLen = view.getUint32(offset);
    offset += 4;
    if (trackMagic !== "MTrk") {
      offset += trackLen;
      continue;
    }
    const trackEnd = offset + trackLen;

    let tick = 0;
    let currentStatus = 0;
    const activeNotes = new Map<number, { startTick: number; velocity: number }>();
    let currentProgram = 0;

    while (offset < trackEnd) {
      const delta = readVarLen();
      tick += delta;

      let status = view.getUint8(offset++);
      if (status < 0x80) {
        offset--;
        status = currentStatus;
      }
      currentStatus = status;

      if (status === 0xff) {
        const metaType = view.getUint8(offset++);
        const metaLen = readVarLen();
        if (metaType === 0x51 && metaLen === 3) {
          const usPerBeat = (view.getUint8(offset) << 16) | (view.getUint8(offset + 1) << 8) | view.getUint8(offset + 2);
          tempoChanges.push({ tick, usPerBeat });
        }
        offset += metaLen;
      } else if (status === 0xf0 || status === 0xf7) {
        const sysexLen = readVarLen();
        offset += sysexLen;
      } else {
        const high = status & 0xf0;
        const channel = status & 0x0f;
        void channel;

        if (high === 0x80 || high === 0x90) {
          const note = view.getUint8(offset++);
          const velocity = view.getUint8(offset++);
          if (high === 0x90 && velocity > 0) {
            activeNotes.set(note, { startTick: tick, velocity });
          } else {
            const active = activeNotes.get(note);
            if (active) {
              notes.push({
                note,
                start: active.startTick,
                duration: tick - active.startTick,
                velocity: active.velocity,
                program: currentProgram,
              });
              activeNotes.delete(note);
              if (tick > maxTick) maxTick = tick;
            }
          }
        } else if (high === 0xb0 || high === 0xe0) {
          offset += 2;
        } else if (high === 0xc0) {
          currentProgram = view.getUint8(offset++);
        } else if (high === 0xd0) {
          offset += 1;
        } else {
          offset += 2;
        }
      }
    }
    offset = trackEnd;
  }

  tempoChanges.sort((a, b) => a.tick - b.tick);

  const tickToSec = (targetTick: number): number => {
    let sec = 0;
    let lastTick = 0;
    let usPerBeat = 500000;
    for (const tc of tempoChanges) {
      if (tc.tick >= targetTick) break;
      const deltaTicks = tc.tick - lastTick;
      sec += (deltaTicks * usPerBeat) / (ticksPerBeat * 1_000_000);
      lastTick = tc.tick;
      usPerBeat = tc.usPerBeat;
    }
    const deltaTicks = targetTick - lastTick;
    sec += (deltaTicks * usPerBeat) / (ticksPerBeat * 1_000_000);
    return sec;
  };

  const timedNotes = notes.map((n) => ({
    ...n,
    start: tickToSec(n.start),
    duration: tickToSec(n.duration + n.start) - tickToSec(n.start),
  }));

  const duration = tickToSec(maxTick);
  return { notes: timedNotes, duration, trackCount: numTracks };
}

interface MidiPreviewPlayerProps {
  url: string;
  label?: string;
}

export function MidiPreviewPlayer({ url }: MidiPreviewPlayerProps) {
  const { t } = useTranslation();
  const [midiData, setMidiData] = useState<MidiData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [position, setPosition] = useState(0);

  const contextRef = useRef<AudioContext | null>(null);
  const masterRef = useRef<GainNode | null>(null);
  const activeRef = useRef<{ stop: () => void }[]>([]);
  const startRef = useRef<{ ctxTime: number; songTime: number } | null>(null);
  const rafRef = useRef<number | null>(null);
  const midiDataRef = useRef<MidiData | null>(null);
  const stopAllRef = useRef<() => void>(() => {});

  useEffect(() => {
    midiDataRef.current = midiData;
  }, [midiData]);

  const stopAll = useCallback(() => {
    for (const a of activeRef.current) {
      try { a.stop(); } catch { /* */ }
    }
    activeRef.current = [];
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    setIsPlaying(false);
  }, []);

  useEffect(() => {
    stopAllRef.current = stopAll;
  }, [stopAll]);

  useEffect(() => {
    const stopFn = () => stopAllRef.current();
    _activePlayers.add(stopFn);
    return () => {
      _activePlayers.delete(stopFn);
      stopAllRef.current();
      if (contextRef.current) {
        contextRef.current.close().catch(() => {});
        contextRef.current = null;
      }
    };
  }, []);

  const loadMidi = useCallback(async () => {
    if (midiData) return;
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.get<ArrayBuffer>(url, { responseType: "arraybuffer" });
      const data = parseMidi(response.data);
      setMidiData(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("errors.loadMidi"));
    } finally {
      setIsLoading(false);
    }
  }, [url, midiData, t]);

  const playFrom = useCallback((fromSec: number) => {
    if (!midiDataRef.current) return;
    stopOtherPlayers(stopAll);
    const ctx = contextRef.current ?? new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)();
    contextRef.current = ctx;
    if (ctx.state === "suspended") ctx.resume();

    const master = ctx.createGain();
    master.gain.value = 0.5;
    master.connect(ctx.destination);
    masterRef.current = master;

    const startCtxTime = ctx.currentTime + 0.05;
    startRef.current = { ctxTime: startCtxTime, songTime: fromSec };
    const dur = midiDataRef.current.duration;

    for (const note of midiDataRef.current.notes) {
      if (note.start < fromSec) continue;
      const freq = midiToFreq(note.note);
      const oscType = getOscType(note.program);
      const env = getEnvelope(note.program);
      const filterFreq = getFilterFreq(note.program);
      const v = Math.max(1, Math.min(127, note.velocity)) / 127;

      const osc = ctx.createOscillator();
      osc.type = oscType;
      osc.frequency.value = freq;

      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = filterFreq;
      filter.Q.value = 1;

      const gain = ctx.createGain();
      const noteStart = startCtxTime + (note.start - fromSec);
      const noteDur = Math.max(0.05, note.duration);
      const attackEnd = noteStart + env.attack;
      const decayEnd = attackEnd + env.decay;
      const sustainEnd = noteStart + noteDur;
      const releaseEnd = sustainEnd + env.release;
      const peakGain = v * 0.3;
      const sustainGain = peakGain * env.sustain;

      gain.gain.setValueAtTime(0, noteStart);
      gain.gain.linearRampToValueAtTime(peakGain, attackEnd);
      gain.gain.linearRampToValueAtTime(sustainGain, decayEnd);
      gain.gain.setValueAtTime(sustainGain, sustainEnd);
      gain.gain.linearRampToValueAtTime(0, releaseEnd);

      osc.connect(filter).connect(gain).connect(master);
      osc.start(noteStart);
      osc.stop(releaseEnd + 0.01);
      const oscRef = osc;
      activeRef.current.push({ stop: () => { try { oscRef.stop(); } catch { /* */ } } });
    }

    const tick = () => {
      const s = startRef.current;
      if (!s || !contextRef.current) return;
      const elapsed = contextRef.current.currentTime - s.ctxTime;
      const pos = s.songTime + elapsed;
      if (pos >= dur) {
        setPosition(dur);
        stopAll();
        setPosition(0);
        startRef.current = null;
        return;
      }
      setPosition(pos);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    setIsPlaying(true);
  }, [stopAll]);

  const onPlay = useCallback(() => {
    if (!midiData) {
      loadMidi().then(() => {
        setTimeout(() => playFrom(0), 100);
      });
      return;
    }
    if (isPlaying) return;
    playFrom(position > 0 ? position : 0);
  }, [midiData, isPlaying, position, loadMidi, playFrom]);

  const onPause = useCallback(() => {
    stopAll();
  }, [stopAll]);

  const onStop = useCallback(() => {
    stopAll();
    setPosition(0);
    startRef.current = null;
  }, [stopAll]);

  const pct = midiData && midiData.duration > 0 ? Math.min(100, (position / midiData.duration) * 100) : 0;

  return (
    <div className="flex items-center gap-2">
      {!isPlaying ? (
        <button
          type="button"
          onClick={onPlay}
          disabled={isLoading}
          className="inline-flex h-7 min-w-[56px] items-center justify-center rounded bg-indigo-600 px-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          title={t("player.midiPlayback")}
        >
          {isLoading ? "…" : position > 0 ? t("player.resume") : t("player.play")}
        </button>
      ) : (
        <button
          type="button"
          onClick={onPause}
          className="inline-flex h-7 min-w-[56px] items-center justify-center rounded bg-indigo-600 px-2 text-xs font-medium text-white hover:bg-indigo-700"
        >
          {t("player.pause")}
        </button>
      )}
      <button
        type="button"
        onClick={onStop}
        disabled={position === 0 && !isPlaying}
        className="inline-flex h-7 w-7 items-center justify-center rounded border border-slate-300 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-30 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-800"
      >
        {t("player.stop")}
      </button>
      <div
        className="h-1 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={t("player.midiPlayback")}
      >
        <div className="h-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} aria-hidden="true" />
      </div>
      {error && <span className="text-[10px] text-red-500">{t("player.playerError")}</span>}
    </div>
  );
}
