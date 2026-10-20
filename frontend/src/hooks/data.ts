// Data hooks. Every query key includes the dataset's current version, so after a
// cleaning step (new version) every page fetches fresh numbers automatically,
// while revisiting a version you already saw is instant (cached).
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { CheckDef, DatasetDetail, Issue } from "../lib/types";

export function useDatasetId(): string {
  const { id } = useParams();
  return id ?? "";
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: api.me, staleTime: 60_000 });
}

export function useDatasets() {
  return useQuery({ queryKey: ["datasets"], queryFn: api.listDatasets });
}

export function useDataset(id: string) {
  return useQuery({ queryKey: ["dataset", id], queryFn: () => api.dataset(id), enabled: !!id });
}

/** Run a per-version query only once we know the current version. */
function useVersioned<T>(kind: string, extra: unknown[], fn: (id: string, v: number) => Promise<T>, enabled = true) {
  const id = useDatasetId();
  const ds = useDataset(id);
  const v = ds.data?.current_version;
  return useQuery({
    queryKey: ["ds", id, v, kind, ...extra],
    queryFn: () => fn(id, v as number),
    enabled: !!id && v !== undefined && enabled,
    staleTime: Infinity, // a version never changes
    placeholderData: keepPreviousData,
  });
}

export const useOverview = () => useVersioned("overview", [], (id, v) => api.overview(id, v));
export const useHealth = () => useVersioned("health", [], (id, v) => api.health(id, v));
export const useColumns = () => useVersioned("columns", [], (id, v) => api.columns(id, v));
export const useColumnDetail = (name: string | null) =>
  useVersioned("column", [name], (id, v) => api.columnDetail(id, name as string, v), !!name);
export const useRows = (offset: number, limit: number) => useVersioned("rows", [offset, limit], (id, v) => api.rows(id, offset, limit, v));
export const useCorrelations = (method: "pearson" | "spearman") => useVersioned("corr", [method], (id) => api.correlations(id, method));
export const useRelationship = (a: string | null, b: string | null) =>
  useVersioned("rel", [a, b], (id) => api.relationship(id, a as string, b as string), !!a && !!b && a !== b);
export const useChartOptions = (x: string | null, y: string | null) =>
  useVersioned("chartopts", [x, y], (id) => api.chartOptions(id, x as string, y ?? undefined), !!x);
export const useChartData = (q: { type: string; x: string; y?: string; agg?: string } | null) =>
  useVersioned("chart", [q], (id) => api.chartData(id, q!), !!q);
export const useRecommendations = () => useVersioned("recs", [], (id) => api.recommendations(id));
export const useVersions = () => useVersioned("versions", [], (id) => api.versions(id));
export const useChecks = () => useVersioned("checks", [], (id, v) => api.checks(id, v));

/** "Looks fine, keep it": close an issue without changing the data (and undo that). */
export function useReview() {
  const id = useDatasetId();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({
    predicate: (q) => q.queryKey[0] === "ds" && q.queryKey[1] === id && ["health", "recs"].includes(String(q.queryKey[3])),
  });
  return useMutation({
    mutationFn: async ({ issue, keep }: { issue: Issue; keep: boolean }) => {
      const current = (await api.reviewed(id)).reviewed.filter((m) => m.issue_id !== issue.id);
      const next = keep ? [...current, { issue_id: issue.id, fingerprint: issue.fingerprint ?? "", title: issue.title }] : current;
      return api.saveReviewed(id, next.slice(-300));
    },
    onSuccess: refresh,
  });
}

/** Saving checks doesn't create a version, so drop the cached health / issues / checks of every version. */
export function useSaveChecks() {
  const id = useDatasetId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (checks: CheckDef[]) => api.saveChecks(id, checks),
    onSuccess: () => qc.invalidateQueries({
      predicate: (q) => q.queryKey[0] === "ds" && q.queryKey[1] === id && ["health", "recs", "checks"].includes(String(q.queryKey[3])),
    }),
  });
}

/** After apply / revert / sheet switch: store the new dataset and refresh lists. */
export function useAfterChange() {
  const qc = useQueryClient();
  return (dataset: DatasetDetail) => {
    qc.setQueryData(["dataset", dataset.id], dataset);
    qc.invalidateQueries({ queryKey: ["datasets"] });
    qc.invalidateQueries({ queryKey: ["me"] });
  };
}
