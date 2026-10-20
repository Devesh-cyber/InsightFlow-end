// The only place that talks to the InsightFlow API.
import { config } from "./config";
import { accessToken, supabase } from "./supabase";
import type {
  ChartData, ChartOptions, CheckDef, ChecksView, ColumnDetail, ColumnProfile, Correlations, DatasetDetail, DatasetSummary, Health, Issue, MatchingRows, Me,
  Operation, Overview, Preview, Relationship, RowMatch, RowsPage, Versions,
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  requestId: string | null;

  constructor(status: number, code: string, message: string, details: unknown = null, requestId: string | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

type Query = Record<string, string | number | null | undefined>;

function url(path: string, query?: Query): string {
  const u = new URL(config.apiUrl + path);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== null && v !== undefined && v !== "") u.searchParams.set(k, String(v));
  }
  return u.toString();
}

async function headers(extra?: HeadersInit): Promise<Headers> {
  const h = new Headers(extra);
  const token = await accessToken();
  if (token) h.set("Authorization", `Bearer ${token}`);
  return h;
}

async function toError(res: Response): Promise<ApiError> {
  let body: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } = {};
  try {
    body = await res.json();
  } catch {
    /* not JSON */
  }
  const e = body.error ?? {};
  const fallback = res.status === 413 ? "That file is too large." : `Request failed (${res.status}).`;
  return new ApiError(res.status, e.code ?? `http_${res.status}`, e.message ?? fallback, e.details ?? null, e.request_id ?? null);
}

async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url(path, opts.query), {
      method,
      headers: await headers(opts.body !== undefined ? { "Content-Type": "application/json" } : undefined),
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach the server. Check your connection; a sleeping free server can take ~30 s to wake up.");
  }
  if (res.status === 401) {
    await supabase.auth.signOut();
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Download a file the API produces (auth header required, so a plain <a href> can't be used). */
async function download(path: string, query: Query, fallbackName: string): Promise<void> {
  const res = await fetch(url(path, query), { headers: await headers() }).catch(() => {
    throw new ApiError(0, "network_error", "Can't reach the server.");
  });
  if (!res.ok) throw await toError(res);
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  saveBlob(blob, match?.[1] ?? fallbackName);
}

export function saveBlob(blob: Blob, name: string): void {
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

/** Upload with progress (fetch can't report upload progress, XHR can). */
export function uploadDataset(file: File, onProgress: (fraction: number) => void, sheet?: string): Promise<DatasetDetail> {
  return new Promise((resolve, reject) => {
    accessToken().then((token) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url("/v1/datasets", { filename: file.name, sheet }));
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      xhr.onerror = () => reject(new ApiError(0, "network_error", "Upload failed: can't reach the server."));
      xhr.onload = () => {
        let body: any = null;
        try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) return resolve(body as DatasetDetail);
        const e = body?.error ?? {};
        reject(new ApiError(xhr.status, e.code ?? `http_${xhr.status}`, e.message ?? (xhr.status === 413 ? "That file is too large." : "Upload failed."), e.details ?? null, e.request_id ?? null));
      };
      xhr.send(file);
    });
  });
}

const ds = (id: string) => `/v1/datasets/${encodeURIComponent(id)}`;

export const api = {
  me: () => request<Me>("GET", "/v1/me"),
  listDatasets: () => request<{ datasets: DatasetSummary[]; limit: number }>("GET", "/v1/datasets"),
  dataset: (id: string) => request<DatasetDetail>("GET", ds(id)),
  rename: (id: string, name: string) => request<DatasetDetail>("PATCH", ds(id), { body: { name } }),
  remove: (id: string) => request<void>("DELETE", ds(id)),
  selectSheet: (id: string, sheet_name: string, expected_version: number) =>
    request<DatasetDetail>("POST", `${ds(id)}/sheet`, { body: { sheet_name, expected_version } }),

  overview: (id: string, version?: number) => request<Overview>("GET", `${ds(id)}/overview`, { query: { version } }),
  rows: (id: string, offset: number, limit: number, version?: number) =>
    request<RowsPage>("GET", `${ds(id)}/rows`, { query: { offset, limit, version } }),
  health: (id: string, version?: number) => request<Health>("GET", `${ds(id)}/health`, { query: { version } }),
  columns: (id: string, version?: number) => request<{ version: number; columns: ColumnProfile[] }>("GET", `${ds(id)}/columns`, { query: { version } }),
  columnDetail: (id: string, name: string, version?: number) =>
    request<ColumnDetail>("GET", `${ds(id)}/columns/detail`, { query: { name, version } }),
  relationship: (id: string, a: string, b: string) => request<Relationship>("GET", `${ds(id)}/relationships`, { query: { a, b } }),
  correlations: (id: string, method: "pearson" | "spearman") => request<Correlations>("GET", `${ds(id)}/correlations`, { query: { method } }),
  chartOptions: (id: string, x: string, y?: string) => request<ChartOptions>("GET", `${ds(id)}/charts/options`, { query: { x, y } }),
  chartData: (id: string, q: { type: string; x: string; y?: string; agg?: string; bins?: number }) =>
    request<ChartData>("GET", `${ds(id)}/charts/data`, { query: q }),

  matchingRows: (id: string, match: RowMatch, offset: number, limit: number, version?: number) =>
    request<MatchingRows>("POST", `${ds(id)}/matching-rows`, { body: { ...match, offset, limit, version } }),
  recommendations: (id: string) => request<{ version: number; issues: Issue[] }>("GET", `${ds(id)}/recommendations`),
  preview: (id: string, op: Operation) => request<Preview>("POST", `${ds(id)}/preview`, { body: op }),
  apply: (id: string, op: Operation, expected_version: number) =>
    request<{ dataset: DatasetDetail; applied: { description: string; effect: Preview["effect"]; warnings: string[] } }>(
      "POST", `${ds(id)}/apply`, { body: { ...op, expected_version } }),
  versions: (id: string) => request<Versions>("GET", `${ds(id)}/versions`),
  checks: (id: string, version?: number) => request<ChecksView>("GET", `${ds(id)}/checks`, { query: { version } }),
  saveChecks: (id: string, checks: CheckDef[]) => request<ChecksView>("PUT", `${ds(id)}/checks`, { body: { checks } }),
  revert: (id: string, to_version: number, expected_version: number) =>
    request<DatasetDetail>("POST", `${ds(id)}/revert`, { body: { to_version, expected_version } }),

  exportFile: (id: string, format: "csv" | "xlsx", version?: number) =>
    download(`${ds(id)}/export`, { format, version }, `dataset.${format}`),
  recipe: (id: string) => download(`${ds(id)}/recipe`, {}, "recipe.py"),
  report: (id: string, q: { format: "html" | "docx"; title?: string; author?: string; tz?: string; version?: number }) =>
    download(`${ds(id)}/report`, q, `report.${q.format}`),
  /** The HTML report as text, so it can be opened in a new tab for printing. */
  reportHtml: async (id: string, q: { title?: string; author?: string; tz?: string; version?: number }): Promise<Blob> => {
    const res = await fetch(url(`${ds(id)}/report`, { ...q, format: "html" }), { headers: await headers() });
    if (!res.ok) throw await toError(res);
    return res.blob();
  },
};
