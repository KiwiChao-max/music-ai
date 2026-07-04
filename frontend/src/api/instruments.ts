import { api, API_BASE_URL } from "./axios";

export interface SampleFileInfo {
  id?: number;
  label: string;
  midi_note: number;
  relative_path: string;
  velocity_offset: number;
  velocity_min?: number;
  velocity_max?: number;
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

export interface SampleClassification {
  filename: string | undefined;
  drum_type: string;
  drum_type_label: string;
  midi_note: number;
  confidence: number;
  features: Record<string, number>;
}

export interface DrumTypeInfo {
  drum_type: string;
  midi_note: number;
  label: string;
}

export interface GmInstrumentInfo {
  program: number;
  name: string;
}

export interface PresetInfo {
  bank_msb: number;
  bank_lsb: number;
  program: number;
  name: string;
  category?: string;
  instrument_type?: string;
}

export interface PresetTableImportResult {
  name: string;
  preset_count: number;
  presets: PresetInfo[];
}

export interface SoundFontImportResult {
  name: string;
  description: string | null;
  file_path: string;
  preset_count: number;
  presets: PresetInfo[];
}

export interface SoundFontInfo {
  id: number;
  name: string;
  description: string | null;
  type: "sf2" | "preset_table";
  file_path: string | null;
  preset_count: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  presets?: PresetInfo[];
}

export interface LibraryExport {
  version: number;
  name: string;
  description: string | null;
  provider: string;
  format: string;
  note_range: [number, number];
  sample_count: number;
  mapping: Record<number, {
    label: string;
    velocity_offset: number;
    relative_path: string;
  }>;
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
  update: (libraryId: number, params: { name?: string; description?: string }): Promise<SampleLibraryInfo> =>
    api
      .patch<SampleLibraryInfo>(`/instruments/libraries/${libraryId}`, params)
      .then((r) => r.data),
  batchRemoveSamples: (libraryId: number, sampleIds: number[]): Promise<SampleLibraryInfo> =>
    api
      .delete<SampleLibraryInfo>(`/instruments/libraries/${libraryId}/samples/batch`, {
        data: { sample_ids: sampleIds },
      })
      .then((r) => r.data),
  updateSample: (libraryId: number, sampleId: number, params: { midi_note?: number; label?: string }): Promise<SampleLibraryInfo> => {
    const form = new FormData();
    if (params.midi_note !== undefined) {
      form.append("midi_note", String(params.midi_note));
    }
    if (params.label !== undefined) {
      form.append("label", params.label);
    }
    return api
      .patch<SampleLibraryInfo>(`/instruments/libraries/${libraryId}/samples/${sampleId}`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  addSample: (libraryId: number, file: File, midiNote?: number): Promise<SampleLibraryInfo> => {
    const form = new FormData();
    form.append("file", file);
    if (midiNote !== undefined) {
      form.append("midi_note", String(midiNote));
    }
    return api
      .post<SampleLibraryInfo>(`/instruments/libraries/${libraryId}/samples`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  removeSample: (libraryId: number, sampleId: number): Promise<SampleLibraryInfo> =>
    api.delete<SampleLibraryInfo>(`/instruments/libraries/${libraryId}/samples/${sampleId}`).then((r) => r.data),
  sampleUrl: (libraryId: number, note: number) =>
    `${API_BASE_URL}/instruments/libraries/${libraryId}/files/${note}`,
  classify: (file: File): Promise<SampleClassification> => {
    const form = new FormData();
    form.append("file", file);
    return api
      .post<SampleClassification>("/instruments/classify", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  listDrumTypes: () =>
    api.get<DrumTypeInfo[]>("/instruments/drum-types").then((r) => r.data),
  listGmInstruments: () =>
    api.get<GmInstrumentInfo[]>("/instruments/gm-instruments").then((r) => r.data),
  importPresetTable: (file: File, name: string): Promise<PresetTableImportResult> => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    return api
      .post<PresetTableImportResult>("/instruments/preset-table/import", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  importSoundFont: (file: File, name: string, description?: string): Promise<SoundFontInfo> => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name);
    if (description) {
      form.append("description", description);
    }
    return api
      .post<SoundFontInfo>("/instruments/soundfont/import", form, {
        headers: { "Content-Type": "multipart/form-data" },
      })
      .then((r) => r.data);
  },
  listSoundFonts: () =>
    api.get<SoundFontInfo[]>("/instruments/soundfonts").then((r) => r.data),
  activeSoundFont: async (): Promise<SoundFontInfo | null> => {
    const response = await api.get<SoundFontInfo | "" | null>(
      "/instruments/soundfonts/active",
      { validateStatus: (s) => (s >= 200 && s < 300) || s === 204 },
    );
    if (response.status === 204) return null;
    if (response.data === "" || response.data === null) return null;
    return response.data as SoundFontInfo;
  },
  getSoundFont: (id: number) =>
    api.get<SoundFontInfo>(`/instruments/soundfonts/${id}`).then((r) => r.data),
  activateSoundFont: (id: number) =>
    api.post<SoundFontInfo>(`/instruments/soundfonts/${id}/activate`).then((r) => r.data),
  deleteSoundFont: (id: number): Promise<void> =>
    api.delete(`/instruments/soundfonts/${id}`).then(() => undefined),
  exportLibrary: (libraryId: number): string =>
    `${API_BASE_URL}/instruments/libraries/${libraryId}/export`,
};
