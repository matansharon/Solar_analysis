import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Plant, TimeRange } from "../api";
import { RunStatusChip, formatDuration, formatTimestamp } from "./Runs";
import { RANGE_OPTIONS, formatDaysOfWeek, nextScheduledRun, rangeLabel } from "./Schedules";

/** Formats a future timestamp relative to `now`, e.g. "in 2d 4h", "in 45m". */
function formatRelative(target: Date, now: Date): string {
  const ms = target.getTime() - now.getTime();
  if (ms <= 0) return "due now";
  const totalMinutes = Math.round(ms / 60000);
  const days = Math.floor(totalMinutes / (60 * 24));
  const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `in ${days}d ${hours}h`;
  if (hours > 0) return `in ${hours}h ${minutes}m`;
  if (minutes > 0) return `in ${minutes}m`;
  return "in <1m";
}

type Health = "ok" | "failed" | "unknown" | "disabled";

function healthStatus(plant: Plant): Health {
  if (!plant.enabled) return "disabled";
  if (plant.last_test_ok === true) return "ok";
  if (plant.last_test_ok === false) return "failed";
  return "unknown";
}

const HEALTH_LABEL: Record<Health, string> = {
  ok: "healthy",
  failed: "failing",
  unknown: "untested",
  disabled: "disabled",
};

export default function Dashboard() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const runsQuery = useQuery({ queryKey: ["runs"], queryFn: api.runs, refetchInterval: 5000 });
  const schedulesQuery = useQuery({ queryKey: ["schedules"], queryFn: api.schedules });
  const plantsQuery = useQuery({ queryKey: ["plants"], queryFn: api.plants });

  const [range, setRange] = useState<TimeRange>("snapshot");
  const [startError, setStartError] = useState<string | null>(null);

  const startRun = useMutation({
    mutationFn: () => api.startRun(range),
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

  const runs = runsQuery.data;
  // `api.runs()` is ordered newest-first, so the first "running" hit is also
  // the most recent one; falling back to runs[0] surfaces the last completed
  // run (of any terminal status) when nothing is active.
  const activeRun = runs?.find((r) => r.status === "running") ?? null;
  const lastRun = runs && runs.length > 0 ? runs[0] : null;

  const next = useMemo(() => nextScheduledRun(schedulesQuery.data), [schedulesQuery.data]);

  return (
    <div>
      <div className="page-header">
        <div className="page-header__title">
          <h1>Dashboard</h1>
          <p>Fleet status at a glance.</p>
        </div>
      </div>

      <div className="dashboard-grid">
        <div className="stat-card">
          <div className="stat-card__label">Current operation</div>
          {runsQuery.isLoading ? (
            <div className="stat-card__value stat-card__value--muted">Loading…</div>
          ) : runsQuery.error ? (
            <div className="stat-card__value stat-card__value--muted">
              {runsQuery.error instanceof Error ? runsQuery.error.message : "failed to load runs"}
            </div>
          ) : activeRun ? (
            <>
              <div className="stat-card__value">
                <RunStatusChip status={activeRun.status} /> Run #{activeRun.id}
              </div>
              <div className="stat-card__meta">
                {activeRun.trigger} · {rangeLabel(activeRun.time_range)} · started{" "}
                {formatTimestamp(activeRun.started_at)}
              </div>
              <Link className="btn btn--ghost btn--small stat-card__action" to={`/runs/${activeRun.id}`}>
                View progress
              </Link>
            </>
          ) : lastRun ? (
            <>
              <div className="stat-card__value">
                <RunStatusChip status={lastRun.status} /> Run #{lastRun.id}
              </div>
              <div className="stat-card__meta">
                {lastRun.trigger} · {rangeLabel(lastRun.time_range)} ·{" "}
                {formatDuration(lastRun.started_at, lastRun.finished_at)}
              </div>
              <Link className="btn btn--ghost btn--small stat-card__action" to={`/runs/${lastRun.id}`}>
                View last run
              </Link>
            </>
          ) : (
            <div className="stat-card__value stat-card__value--muted">No runs yet</div>
          )}
        </div>

        <div className="stat-card">
          <div className="stat-card__label">Run now</div>
          <div className="stat-card__control">
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
          {startError && (
            <p className="alert alert--error stat-card__alert" role="alert">
              {startError}
            </p>
          )}
        </div>

        <div className="stat-card">
          <div className="stat-card__label">Next scheduled run</div>
          {schedulesQuery.isLoading ? (
            <div className="stat-card__value stat-card__value--muted">Loading…</div>
          ) : next ? (
            <>
              <div className="stat-card__value">{formatRelative(next.at, new Date())}</div>
              <div className="stat-card__meta">
                {next.at.toLocaleString()} · {formatDaysOfWeek(next.schedule.days_of_week)} ·{" "}
                {rangeLabel(next.schedule.time_range)}
              </div>
            </>
          ) : (
            <div className="stat-card__value stat-card__value--muted">none</div>
          )}
          <Link className="btn btn--ghost btn--small stat-card__action" to="/schedules">
            Manage schedules
          </Link>
        </div>
      </div>

      <section style={{ marginTop: 28 }}>
        <h2>Plant health</h2>
        {plantsQuery.isLoading ? (
          <div className="empty-state">Loading plants…</div>
        ) : plantsQuery.error ? (
          <div className="empty-state">
            {plantsQuery.error instanceof Error ? plantsQuery.error.message : "failed to load plants"}
          </div>
        ) : !plantsQuery.data || plantsQuery.data.length === 0 ? (
          <div className="empty-state">No plants configured yet. Add one in Plants.</div>
        ) : (
          <div className="health-chip-row">
            {plantsQuery.data.map((p) => {
              const status = healthStatus(p);
              return (
                <Link key={p.id} to="/plants" className={`health-chip health-chip--${status}`}>
                  <span className="health-chip__dot" aria-hidden="true" />
                  <span className="health-chip__name">{p.name}</span>
                  <span className="health-chip__meta">{HEALTH_LABEL[status]}</span>
                </Link>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
