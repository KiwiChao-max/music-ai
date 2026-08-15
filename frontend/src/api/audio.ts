import { api } from "./axios";
import type {
  AudioTask,
  MusicAnalysis,
  ProcessResponse,
  StemInfo,
  TaskStatus,
  UploadResponse,
} from "@/types/audio";

export const audioApi = {
  list: () => api.get<AudioTask[]>("/audio").then((r) => r.data),
  get: (taskId: number) => api.get<AudioTask>(`/audio/${taskId}`).then((r) => r.data),
  upload: (file: File, onUploadProgress?: (pct: number) => void, signal?: AbortSignal) => {
    const form = new FormData();
    form.append("file", file);
    return api
      .post<UploadResponse>("/audio/upload", form, {
        onUploadProgress: (e) => {
          if (onUploadProgress && e.total) onUploadProgress(Math.round((e.loaded / e.total) * 100));
        },
        signal,
        timeout: 300_000,
      })
      .then((r) => r.data);
  },
  remove: (taskId: number) => api.delete<void>(`/audio/${taskId}`).then((r) => r.status),
};

export const tasksApi = {
  process: (taskId: number) =>
    api.post<ProcessResponse>(`/tasks/${taskId}/process`).then((r) => r.data),
  status: (taskId: number) => api.get<TaskStatus>(`/tasks/${taskId}/status`).then((r) => r.data),
  stems: (taskId: number) => api.get<StemInfo[]>(`/tasks/${taskId}/stems`).then((r) => r.data),
  analysis: (taskId: number) =>
    api.get<MusicAnalysis>(`/tasks/${taskId}/analysis`).then((r) => r.data),
};
