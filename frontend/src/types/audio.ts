export type AudioTaskStatus = "UPLOADED" | "PROCESSING" | "FINISHED" | "FAILED";

export interface AudioTask {
  id: number;
  filename: string;
  status: AudioTaskStatus;
}

export interface UploadResponse {
  task_id: number;
}
