import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { audioApi, tasksApi } from "@/api/audio";
import type { AudioTask, MusicAnalysis, ProcessResponse, StemInfo } from "@/types/audio";

const TASKS_KEY = ["audio-tasks"] as const;

// Slow fallback poll for the list view: while any task is still
// PROCESSING, refresh the list every 8 s in case a WebSocket update
// was missed (e.g. WS temporarily down). The WebSocket patch path is
// the primary live-update mechanism.
const LIST_POLL_MS = 8000;

export function useAudioTasks() {
  return useQuery({
    queryKey: TASKS_KEY,
    queryFn: audioApi.list,
    refetchInterval: (query) =>
      query.state.data?.some((task) => task.status === "PROCESSING")
        ? LIST_POLL_MS
        : false,
  });
}

interface UseAudioTaskOptions {
  /**
   * Polling interval in milliseconds. Pass a function of the current task
   * to drive polling off live data (e.g. only while `status === "PROCESSING"`).
   * Defaults to "no polling".
   */
  refetchInterval?:
    | number
    | false
    | ((task: AudioTask | undefined) => number | false);
}

export function useAudioTask(
  taskId: number,
  options: UseAudioTaskOptions = {},
) {
  const { refetchInterval } = options;
  return useQuery({
    queryKey: [...TASKS_KEY, taskId],
    queryFn: () => audioApi.get(taskId),
    enabled: Number.isFinite(taskId),
    refetchInterval: (query) =>
      typeof refetchInterval === "function"
        ? refetchInterval(query.state.data)
        : refetchInterval,
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
