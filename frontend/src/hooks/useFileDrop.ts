import { useCallback, useState } from "react";
import type { DragEvent } from "react";

interface UseFileDropOptions {
  /** When false, only the first dropped file is passed through. */
  multiple?: boolean;
  onFiles: (files: File[]) => void;
}

/**
 * Shared drag-and-drop wiring for the upload drop zones.
 *
 * The upload page and the sample-library FilePicker previously duplicated
 * this handler set almost verbatim. `isDragging` drives the highlight
 * styling; the handlers prevent the browser from navigating away on drop.
 */
export function useFileDrop({ multiple = true, onFiles }: UseFileDropOptions) {
  const [isDragging, setIsDragging] = useState(false);

  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const onDragEnter = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const onDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);
      const dropped = Array.from(e.dataTransfer.files ?? []);
      if (dropped.length > 0) {
        onFiles(multiple ? dropped : [dropped[0]]);
      }
    },
    [multiple, onFiles],
  );

  return { isDragging, onDragOver, onDragEnter, onDragLeave, onDrop };
}
