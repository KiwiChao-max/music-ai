import { api } from "./axios";
import type { AudioTask, UploadResponse } from "@/types/audio";

export const audioApi = {
  list: () => api.get<AudioTask[]>("/audio").then((r) => r.data),
  get: (taskId: number) =>
    api.get<AudioTask>(`/audio/${taskId}`).then((r) => r.data),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api
      .post<UploadResponse>("/audio/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  remove: (taskId: number) =>
    api.delete<void>(`/audio/${taskId}`).then((r) => r.status),
};
