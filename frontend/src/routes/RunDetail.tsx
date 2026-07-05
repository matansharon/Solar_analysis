import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useRunStream } from "../sse";
import { RunStatusChip, formatDuration, formatTimestamp } from "./Runs";

type PlantState = "running" | "ok" | "failed";
type StepPlant = { name: string; state: PlantState };

function StepRail({ plants, running }: { plants: StepPlant[]; running: boolean }) {
  if (plants.length === 0) {
    return <p className="cell-muted">{running ? "No plants have started yet." : "No plants."}</p>;
  }
  return (
    <div className="step-rail">
      {plants.map((p) => (
        <span key={p.name} className={`step-chip step-chip--${p.state}`}>
          <span className="step-chip__dot" aria-hidden="true" />
          {p.name}
        </span>
      ))}
    </div>
  );
}

export default function RunDetail() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const idValid = Number.isFinite(id);
  const qc = useQueryClient();

  const runQuery = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.run(id),
    enabled: idValid,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2000 : false),
  });
  const run = runQuery.data;
  const running = run?.status === "running";

  const { logLines, lastEvent, ended, errorCount } = useRunStream(idValid ? id : null, running);

  const [plantsState, setPlantsState] = useState<Record<string, string>>({});
  const [resynced, setResynced] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);

  // For completed runs the log is fetched once; while running (or before the
  // run record has loaded at all) it stays disabled and is only pulled
  // on-demand as a resync fallback (see errorCount effect below). Gating on
  // `run != null` matters: before the first run fetch resolves, `running` is
  // `false` (not "unknown"), which would otherwise let this fire prematurely
  // against a run that's actually still in progress.
  const logQuery = useQuery({
    queryKey: ["runLog", id],
    queryFn: () => api.runLog(id),
    enabled: idValid && run != null && !running,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  // Reset local per-run state when navigating between runs.
  useEffect(() => {
    setPlantsState({});
    setResynced(false);
  }, [id]);

  // Seed/refresh per-plant state from the authoritative server snapshot
  // (present on the run record while it is running).
  useEffect(() => {
    if (run?.progress?.plants) setPlantsState(run.progress.plants);
  }, [run?.progress]);

  // Apply incremental per-plant updates from the live stream between polls.
  useEffect(() => {
    if (!lastEvent) return;
    const ev = lastEvent as { event?: string; plant?: string; ok?: boolean };
    if (ev.event === "plant_start" && ev.plant) {
      const plant = ev.plant;
      setPlantsState((p) => ({ ...p, [plant]: "running" }));
    } else if (ev.event === "plant_done" && ev.plant) {
      const plant = ev.plant;
      setPlantsState((p) => ({ ...p, [plant]: ev.ok ? "ok" : "failed" }));
    }
  }, [lastEvent]);

  // Clean end-of-stream: the run just finished — pull the final state right away
  // instead of waiting for the next poll.
  useEffect(() => {
    if (ended) void runQuery.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ended]);

  // The SSE connection dropped and is not auto-retried — resync both the run
  // record and the log from the server so the view doesn't go stale/silent.
  useEffect(() => {
    if (errorCount === 0) return;
    setResynced(true);
    void runQuery.refetch();
    void logQuery.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [errorCount]);

  // Auto-scroll the live log console as new lines arrive.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [logLines]);

  async function handleCancel() {
    if (!run) return;
    setCancelling(true);
    try {
      await api.cancelRun(run.id);
      void qc.invalidateQueries({ queryKey: ["run", id] });
    } finally {
      setCancelling(false);
    }
  }

  if (!idValid) {
    return <div className="empty-state">Invalid run id.</div>;
  }
  if (runQuery.isLoading) {
    return <div className="empty-state">Loading run…</div>;
  }
  if (runQuery.error || !run) {
    return (
      <div className="empty-state">
        {runQuery.error instanceof Error ? runQuery.error.message : "run not found"}
      </div>
    );
  }

  const showLiveLog = running && !resynced;
  const logText = showLiveLog ? logLines.join("\n") : logQuery.data?.log ?? logLines.join("\n");

  const stepPlants: StepPlant[] = running
    ? Object.entries(plantsState).map(([name, state]) => ({
        name,
        state: state === "ok" || state === "failed" ? state : "running",
      }))
    : (run.plants_summary ?? []).map((p) => ({ name: p.name, state: p.ok ? "ok" : "failed" }));

  const verifyMissingCount =
    run.notes && typeof run.notes.verify_missing_count === "number" ? (run.notes.verify_missing_count as number) : 0;

  return (
    <div>
      <div className="page-header">
        <div className="page-header__title">
          <h1>Run #{run.id}</h1>
          <p>
            {run.trigger === "manual" ? "Manually triggered" : "Scheduled"} · {run.time_range}
          </p>
        </div>
        <div className="btn-row">
          <RunStatusChip status={run.status} />
          {running && (
            <button type="button" className="btn btn--danger" onClick={() => void handleCancel()} disabled={cancelling}>
              {cancelling ? "Cancelling…" : "Cancel run"}
            </button>
          )}
          <Link className="btn btn--ghost" to="/runs">
            Back to runs
          </Link>
        </div>
      </div>

      <div className="panel meta-grid" style={{ padding: 16, marginBottom: 24 }}>
        <div className="meta-item">
          <div className="meta-item__label">Started</div>
          <div className="meta-item__value">{formatTimestamp(run.started_at)}</div>
        </div>
        <div className="meta-item">
          <div className="meta-item__label">Finished</div>
          <div className="meta-item__value">{formatTimestamp(run.finished_at)}</div>
        </div>
        <div className="meta-item">
          <div className="meta-item__label">Duration</div>
          <div className="meta-item__value">{formatDuration(run.started_at, run.finished_at)}</div>
        </div>
        <div className="meta-item">
          <div className="meta-item__label">Trigger</div>
          <div className="meta-item__value">{run.trigger}</div>
        </div>
      </div>

      {run.error && (
        <p className="alert alert--error" role="alert">
          {run.error}
        </p>
      )}

      <section style={{ marginBottom: 24 }}>
        <h2>Plants</h2>
        <StepRail plants={stepPlants} running={running} />
      </section>

      {run.skipped_plants && run.skipped_plants.length > 0 && (
        <section style={{ marginBottom: 24 }}>
          <h2>Skipped</h2>
          <ul className="skip-list">
            {run.skipped_plants.map((s) => (
              <li key={s.name}>
                <strong>{s.name}</strong> — <span className="cell-muted">{s.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {verifyMissingCount > 0 && (
        <p className="subtle-note">
          Note: {verifyMissingCount} metric{verifyMissingCount === 1 ? "" : "s"} could not be verified against source
          data.
        </p>
      )}

      <section style={{ marginBottom: 24 }}>
        <h2>Log</h2>
        <div className="log-console">
          <div className="log-console__bar">
            <span>{running ? "live" : "log"}</span>
            <span className="mono">{run.log_path}</span>
          </div>
          <pre ref={logRef}>{logText || (running ? "waiting for output…" : "(empty)")}</pre>
        </div>
      </section>

      {run.report_path && (
        <section>
          <div className="page-header" style={{ marginBottom: 12 }}>
            <h2 style={{ margin: 0 }}>Report</h2>
            <a className="btn btn--ghost btn--small" href={api.reportUrl(run.id)} target="_blank" rel="noreferrer">
              Open report ↗
            </a>
          </div>
          <div className="report-frame-wrap">
            <iframe
              className="report-frame"
              src={api.reportUrl(run.id)}
              sandbox="allow-same-origin"
              title={`Run ${run.id} report`}
            />
          </div>
        </section>
      )}
    </div>
  );
}
