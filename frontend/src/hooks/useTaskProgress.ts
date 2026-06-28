/**
 * useTaskProgress — open a WebSocket to the backend progress channel
 * and patch the React Query cache in place as events arrive.
 *
 * The hook is transparent: callers don't need to change their existing
 * `useAudioTask` / `useAudioTasks` wiring — the WS handler updates the
 * same query keys those hooks read, so the UI re-renders without
 * polling. Falls back to the existing HTTP polling cadence if the WS
 * never connects (the caller still has `refetchInterval` configured).
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { AudioTask, AudioTaskStatus } from "@/types/audio";

const STORAGE_KEY = "music-ai.token";

function readToken(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function wsBaseUrl(): string {
  // In dev, Vite proxies `/api/*` to the FastAPI backend on the same
  // host:port, so we just need to switch the protocol to ws/wss.
  const base =
    import.meta.env.VITE_API_BASE_URL ?? `${window.location.origin}/api`;
  if (base.startsWith("https://")) {
    return `wss://${base.slice("https://".length)}`;
  }
  if (base.startsWith("http://")) {
    return `ws://${base.slice("http://".length)}`;
  }
  // base was a relative path: derive from the page origin.
  const origin = window.location.origin;
  return origin.replace(/^http/, "ws") + base;
}

interface ProgressEvent {
  type: string;
  task_id: number;
  status?: AudioTaskStatus;
  progress?: number;
  current_step?: string | null;
  error_message?: string | null;
  finished_at?: string | null;
}

function isTerminal(status: AudioTaskStatus | undefined): boolean {
  return status === "FINISHED" || status === "FAILED";
}

function patchTask(
  prev: AudioTask | undefined,
  event: ProgressEvent,
): AudioTask | undefined {
  if (!prev || prev.id !== event.task_id) {
    return prev;
  }
  return {
    ...prev,
    status: event.status ?? prev.status,
    progress: event.progress ?? prev.progress,
    current_step: event.current_step ?? prev.current_step,
    error_message: event.error_message ?? prev.error_message,
    finished_at: event.finished_at ?? prev.finished_at,
  };
}

interface UseTaskProgressOptions {
  /** Disable the WS connection (e.g. when the task is already terminal). */
  enabled?: boolean;
}

/**
 * Subscribe to live progress for a single task and patch the React
 * Query cache. Auto-reconnects with exponential backoff (cap 10 s)
 * while the task is still in a non-terminal state.
 */
export function useTaskProgress(
  taskId: number,
  options: UseTaskProgressOptions = {},
) {
  const { enabled = true } = options;
  const qc = useQueryClient();
  const detailKey = ["audio-tasks", taskId] as const;
  const listKey = ["audio-tasks"] as const;
  const closedByUs = useRef(false);

  useEffect(() => {
    if (!enabled || !Number.isFinite(taskId)) return;
    const token = readToken();
    const url = `${wsBaseUrl()}/ws/tasks/${taskId}/progress${
      token ? `?token=${encodeURIComponent(token)}` : ""
    }`;

    let socket: WebSocket | null = null;
    let retryMs = 500;
    let timer: number | null = null;
    closedByUs.current = false;

    const connect = () => {
      socket = new WebSocket(url);

      socket.addEventListener("open", () => {
        retryMs = 500; // reset backoff after a successful connect
      });

      socket.addEventListener("message", (msg) => {
        let event: ProgressEvent | null = null;
        try {
          event = JSON.parse(msg.data) as ProgressEvent;
        } catch {
          return;
        }
        if (!event || typeof event !== "object" || !("type" in event)) {
          return;
        }
        if (event.type === "error") {
          // Surface the error via the detail query so the UI can show it.
          const message = (event as { message?: string }).message;
          qc.setQueryData<AudioTask | undefined>(detailKey, (prev) => {
            if (!prev || prev.id !== taskId) return prev;
            return {
              ...prev,
              error_message: message ?? prev.error_message,
            };
          });
          return;
        }
        // snapshot / task_finished / progress — patch the cached task.
        const ev = event as ProgressEvent;
        qc.setQueryData<AudioTask | undefined>(detailKey, (prev) => patchTask(prev, ev));
        qc.setQueryData<AudioTask[] | undefined>(listKey, (prev) => {
          if (!prev) return prev;
          return prev.map((t) => (t.id === taskId ? (patchTask(t, ev) ?? t) : t));
        });
        // If the task reached a terminal state, stop trying to reconnect.
        if (event.type === "task_finished" && isTerminal(event.status)) {
          closedByUs.current = true;
          socket?.close();
        }
      });

      const scheduleRetry = () => {
        if (closedByUs.current) return;
        timer = window.setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, 10_000);
      };

      socket.addEventListener("close", (e) => {
        // 1008 = policy violation (e.g. forbidden / not found). Don't retry.
        if (e.code === 1008) {
          closedByUs.current = true;
          return;
        }
        scheduleRetry();
      });
      socket.addEventListener("error", () => {
        try {
          socket?.close();
        } catch {
          // ignore: the close handler will trigger a reconnect
        }
      });
    };

    connect();
    return () => {
      closedByUs.current = true;
      if (timer != null) window.clearTimeout(timer);
      if (socket) {
        try {
          socket.close();
        } catch {
          // ignore
        }
      }
    };
  }, [taskId, enabled, qc]);
}
