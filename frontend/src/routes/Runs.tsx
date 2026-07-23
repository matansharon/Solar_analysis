import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Run, RunStatus, TimeRange } from "../api";

const RANGE_OPTIONS: { value: TimeRange; label: string }[] = [
  { value: "snapshot", label: "Snapshot" },
  { value: "30d", label: "Last 30 days" },
  { value: "12mo", label: "Last 12 months" },
  { value: "all", label: "All time" },
];

export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function formatDuration(startedAt: string, finishedAt: string | null): string {
  if (!finishedAt) return "—";
  const start = new Date(startedAt).getTime();
  const end = new Date(finishedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return "—";
  const totalSeconds = Math.round((end - start) / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

export function RunStatusChip({ status }: { status: RunStatus }) {
  return <span className={`run-status run-status--${status}`}>{status}</span>;
}

export default function Runs() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: runs, isLoading, error } = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const { data: plants } = useQuery({ queryKey: ["plants"], queryFn: api.plants });
  const enabledPlants = (plants ?? []).filter((p) => p.enabled);
  const nameById = new Map((plants ?? []).map((p) => [p.id, p.name] as const));
  const [plantId, setPlantId] = useState<number | null>(null); // null = all enabled
  const [range, setRange] = useState<TimeRange>("snapshot");
  const [startError, setStartError] = useState<string | null>(null);

  const startRun = useMutation({
    mutationFn: () => api.startRun(range, plantId),
    onSuccess: (res) => {
      setStartError(null);
      void qc.invalidateQueries({ queryKey: ["runs"] });
      navigate(`/runs/${res.id}`);
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : "failed to start run";
      setStartError(message === "busy" ? "a run/test is already active" : message);
    },
  });

  return (
    <div>
      <div className="page-header">
        <div className="page-header__title">
          <h1>Runs</h1>
          <p>Analysis run history — trigger a manual run or inspect a past one.</p>
        </div>
        <div className="btn-row">
          <select
            className="field__select"
            value={plantId ?? ""}
            onChange={(e) => setPlantId(e.target.value === "" ? null : Number(e.target.value))}
            aria-label="System for new run"
          >
            <option value="">All enabled systems</option>
            {enabledPlants.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select
            className="field__select"
            value={range}
            onChange={(e) => setRange(e.target.value as TimeRange)}
            aria-label="Time range for new run"
          >
            {RANGE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => {
              setStartError(null);
              startRun.mutate();
            }}
            disabled={startRun.isPending}
          >
            {startRun.isPending ? "Starting…" : "Run now"}
          </button>
        </div>
      </div>

      {startError && (
        <p className="alert alert--error" role="alert">
          {startError}
        </p>
      )}

      <div className="panel">
        {isLoading ? (
          <div className="empty-state">Loading runs…</div>
        ) : error ? (
          <div className="empty-state">{error instanceof Error ? error.message : "failed to load runs"}</div>
        ) : !runs || runs.length === 0 ? (
          <div className="empty-state">No runs yet. Trigger one above, or set up a schedule.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Trigger</th>
                  <th>Range</th>
                  <th>System</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <RunRow
                    key={r.id}
                    run={r}
                    systemLabel={r.plant_id == null ? "All" : (nameById.get(r.plant_id) ?? `#${r.plant_id}`)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function RunRow({ run, systemLabel }: { run: Run; systemLabel: string }) {
  return (
    <tr>
      <td className="mono">#{run.id}</td>
      <td>
        <RunStatusChip status={run.status} />
      </td>
      <td className="cell-muted">{run.trigger}</td>
      <td className="cell-muted">{run.time_range}</td>
      <td className="cell-muted">{systemLabel}</td>
      <td className="cell-timestamp">{formatTimestamp(run.started_at)}</td>
      <td className="cell-timestamp">{formatDuration(run.started_at, run.finished_at)}</td>
      <td>
        <Link className="btn btn--ghost btn--small" to={`/runs/${run.id}`}>
          View
        </Link>
      </td>
    </tr>
  );
}
