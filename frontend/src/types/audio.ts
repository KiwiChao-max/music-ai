export type AudioTaskStatus = "UPLOADED" | "PROCESSING" | "FINISHED" | "FAILED";

export interface AudioTask {
  id: number;
  filename: string;
  status: AudioTaskStatus;
  progress: number;
  current_step: string | null;
  duration: number | null;
  output_dir: string | null;
  error_message: string | null;
  finished_at: string | null;
}

export interface UploadResponse {
  task_id: number;
}

// ---- /api/tasks/{id}/* ---------------------------------------------------

export interface ProcessResponse {
  task_id: number;
  status: AudioTaskStatus;
}

export interface TaskStatus {
  status: AudioTaskStatus;
  progress: number;
}

export interface StemInfo {
  name: string; // e.g. "drums" or "original"
  url: string; // e.g. "/storage/outputs/task_6/drums.wav"
  kind: "audio" | "midi"; // "audio" = playable stem, "midi" = downloadable MIDI
  profile?: "raw" | "gm" | "xg" | null;
}

export interface ChordSegment {
  start: number;
  end: number;
  chord: string;
  confidence: number;
}

export interface MusicSection {
  label: string;
  start: number;
  end: number;
  energy: "low" | "medium" | "high" | string;
  density: number;
  suggestion: string;
}

export interface DetectedInstrument {
  instrument: string;
  probability: number;
}

export interface MusicAnalysis {
  bpm: number | null;
  bpm_confidence: number;
  key: string | null;
  key_confidence: number;
  scale: string | null;
  note_count: number;
  duration: number;
  pitch_range: string | null;
  chords: ChordSegment[];
  sections: MusicSection[];
  instrumentation: string[];
  arrangement: string[];
  warnings: string[];
  detected_instruments?: DetectedInstrument[];
  dominant_instrument?: string;
  // LLM commentary fields, attached by the API when the worker has
  // finished the analysis step. May all be null if LLM is disabled
  // or the LLM call failed.
  commentary?: string | null;
  commentary_model?: string | null;
  commentary_generated_at?: string | null;
}