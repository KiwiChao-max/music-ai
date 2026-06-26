import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { audioApi, tasksApi } from "@/api/audio";
import type { MusicAnalysis, ProcessResponse, StemInfo } from "@/types/audio";

const TASKS_KEY = ["audio-tasks"] as const;

export function useAudioTasks() {
  return useQuery({
    queryKey: TASKS_KEY,
    queryFn: audioApi.list,
    refetchInterval: (query) =>
      query.state.data?.some((task) => task.status === "PROCESSING")
        ? 1500
        : false,
  });
}

export function useAudioTask(
  taskId: number,
  options?: { refetchInterval?: number | (() => number | false) | false },
) {
  return useQuery({
    queryKey: [...TASKS_KEY, taskId],
    queryFn: () => audioApi.get(taskId),
    enabled: Number.isFinite(taskId),
    refetchInterval: options?.refetchInterval,
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

export function useStartProcess() {
  const qc = useQueryClient();
  return useMutation<ProcessResponse, Error, number>({
    mutationFn: (taskId) => tasksApi.process(taskId),
    onSuccess: (_data, taskId) => {
      qc.invalidateQueries({ queryKey: [...TASKS_KEY, taskId] });
      qc.invalidateQueries({ queryKey: TASKS_KEY });
    },
  });
}

export function useStems(taskId: number, enabled: boolean) {
  return useQuery<StemInfo[]>({
    queryKey: [...TASKS_KEY, taskId, "stems"],
    queryFn: () => tasksApi.stems(taskId),
    enabled: enabled && Number.isFinite(taskId),
  });
}

export function useMusicAnalysis(taskId: number, enabled: boolean) {
  return useQuery<MusicAnalysis>({
    queryKey: [...TASKS_KEY, taskId, "analysis"],
    queryFn: () => tasksApi.analysis(taskId),
    enabled: enabled && Number.isFinite(taskId),
    retry: false,
  });
}
