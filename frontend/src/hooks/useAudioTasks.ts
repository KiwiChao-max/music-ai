import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { audioApi } from "@/api/audio";
import type { AudioTask } from "@/types/audio";

const TASKS_KEY = ["audio-tasks"] as const;

export function useAudioTasks() {
  return useQuery<AudioTask[]>({
    queryKey: TASKS_KEY,
    queryFn: audioApi.list,
  });
}

export function useUploadAudio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => audioApi.upload(file),
    onSuccess: () => qc.invalidateQueries({ queryKey: TASKS_KEY }),
  });
}

export function useDeleteAudio() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (taskId: number) => audioApi.remove(taskId),
    onSuccess: () => qc.invalidateQueries({ queryKey: TASKS_KEY }),
  });
}
