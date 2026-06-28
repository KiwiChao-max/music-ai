import { api } from "./axios";

export interface SampleFileInfo {
  label: string;
  midi_note: number;
  relative_path: string;
  velocity_offset: number;
}

export interface SampleLibraryInfo {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  provider: string;
  created_at: string;
  updated_at: string;
  files: SampleFileInfo[];
}

export const instrumentsApi = {
  list: () =>
    api.get<SampleLibraryInfo[]>("/instruments/libraries").then((r) => r.data),
  active: async (): Promise<SampleLibraryInfo | null> => {
    const response = await api.get<SampleLibraryInfo | "" | null>(
      "/instruments/active",
      { validateStatus: (s) => (s >= 200 && s < 300) || s === 204 },
    );
    if (response.status === 204) return null;
    if (response.data === "" || response.data === null) return null;
    return response.data as SampleLibraryInfo;
  },
  get: (libraryId: number) =>
    api
      .get<SampleLibraryInfo>(`/instruments/libraries/${libraryId}`)
      .then((r) => r.data),
  create: (params: {
    name: string;
    description?: string;
    files: File[];
    zipFile?: File;
  }): Promise<SampleLibraryInfo> => {
    const form = new FormData();
    form.append("name", params.name);
    if (params.description) {
      form.append("description", params.description);
    }
    for (const file of params.files) {
      form.append("files", file);
    }
    if (params.zipFile) {
      form.append("zip_file", params.zipFile);
    }
    return api
      .post<SampleLibraryInfo>("/instruments/libraries", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  activate: (libraryId: number) =>
    api
      .post<SampleLibraryInfo>(
        `/instruments/libraries/${libraryId}/activate`,
      )
      .then((r) => r.data),
  remove: async (libraryId: number): Promise<void> => {
    await api.delete(`/instruments/libraries/${libraryId}`);
  },
  sampleUrl: (libraryId: number, note: number) =>
    `/api/instruments/libraries/${libraryId}/files/${note}`,
};
