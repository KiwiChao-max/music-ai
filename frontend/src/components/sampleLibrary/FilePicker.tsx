import { useTranslation } from "react-i18next";

import { useFileDrop } from "@/hooks/useFileDrop";

interface FilePickerProps {
  label: string;
  accept: string;
  multiple: boolean;
  files: File[];
  onFiles: (files: File[]) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
}

export function FilePicker({ label, accept, multiple, files, onFiles, inputRef }: FilePickerProps) {
  const { t } = useTranslation();
  const { isDragging, onDragOver, onDragEnter, onDragLeave, onDrop } = useFileDrop({
    multiple,
    onFiles,
  });

  return (
    <label
      onDragOver={onDragOver}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={`flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-4 py-6 text-center transition-colors ${
        isDragging
          ? "border-indigo-500 bg-indigo-50 dark:border-indigo-400 dark:bg-indigo-950/40"
          : "border-slate-300 bg-slate-50 hover:border-slate-400 hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-800 dark:hover:border-slate-500 dark:hover:bg-slate-700"
      }`}
    >
      <span className="text-sm font-medium text-slate-700 dark:text-slate-200">{label}</span>
      {files.length > 0 && (
        <span className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          {files.length === 1
            ? files[0].name
            : t("samples.filesSelectedPlural", { count: files.length })}
        </span>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        onChange={(e) => onFiles(Array.from(e.target.files ?? []))}
        className="sr-only"
      />
    </label>
  );
}
