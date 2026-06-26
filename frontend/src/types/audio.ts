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
}
