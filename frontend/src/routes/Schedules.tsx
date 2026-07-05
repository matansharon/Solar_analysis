import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Schedule, TimeRange } from "../api";

export const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export const RANGE_OPTIONS: { value: TimeRange; label: string }[] = [
  { value: "snapshot", label: "Snapshot" },
  { value: "30d", label: "Last 30 days" },
  { value: "12mo", label: "Last 12 months" },
  { value: "all", label: "All time" },
];

export function rangeLabel(range: TimeRange): string {
  return RANGE_OPTIONS.find((o) => o.value === range)?.label ?? range;
}

/** Parses the stored "days_of_week" CSV (Mon=0..Sun=6) into sorted day indices. */
export function parseDaysOfWeek(csv: string): number[] {
  return [
    ...new Set(
      csv
        .split(",")
        .map((s) => Number(s.trim()))
        .filter((n) => Number.isInteger(n) && n >= 0 && n <= 6)
    ),
  ].sort((a, b) => a - b);
}

/** Human weekday summary, e.g. "Weekdays", "Weekend", "Every day", "Mon, Wed". */
export function formatDaysOfWeek(csv: string): string {
  const days = parseDaysOfWeek(csv);
  if (days.length === 0) return "never";
  if (days.length === 7) return "Every day";
  if (days.length === 5 && [0, 1, 2, 3, 4].every((d) => days.includes(d))) return "Weekdays";
  if (days.length === 2 && days.includes(5) && days.includes(6)) return "Weekend";
  return days.map((d) => WEEKDAY_LABELS[d]).join(", ");
}

/**
 * Computes the soonest upcoming occurrence of `schedule` strictly after `now`,
 * or null if the schedule has no days selected / an unparsable time.
 *
 * `days_of_week` uses Mon=0..Sun=6 (matching the server/APScheduler cron
 * convention); JS `Date.getDay()` uses Sun=0..Sat=6, so we remap with
 * `(getDay() + 6) % 7`. We scan today..+7 days so a same-weekday match a
 * full week out is still found once today's time has already passed.
 */
export function nextOccurrence(schedule: Schedule, now: Date): Date | null {
  const days = parseDaysOfWeek(schedule.days_of_week);
  if (days.length === 0) return null;
  const m = /^(\d{1,2}):(\d{2})$/.exec(schedule.time_of_day.trim());
  if (!m) return null;
  const hour = Number(m[1]);
  const minute = Number(m[2]);
  if (hour > 23 || minute > 59) return null;

  for (let offset = 0; offset <= 7; offset++) {
    const candidate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset, hour, minute, 0, 0);
    const candidateDow = (candidate.getDay() + 6) % 7;
    if (!days.includes(candidateDow)) continue;
    if (candidate.getTime() <= now.getTime()) continue;
    return candidate;
  }
  return null;
}

export interface NextScheduledRun {
  schedule: Schedule;
  at: Date;
}

/** Soonest upcoming run across all enabled schedules, or null if none qualify. */
export function nextScheduledRun(schedules: Schedule[] | undefined, now: Date = new Date()): NextScheduledRun | null {
  if (!schedules) return null;
  let best: NextScheduledRun | null = null;
  for (const s of schedules) {
    if (!s.enabled) continue;
    const at = nextOccurrence(s, now);
    if (!at) continue;
    if (!best || at.getTime() < best.at.getTime()) best = { schedule: s, at };
  }
  return best;
}

function daysToCsv(days: boolean[]): string {
  return days.reduce<number[]>((acc, on, i) => (on ? [...acc, i] : acc), []).join(",");
}

interface FormValues {
  time: string;
  days: boolean[];
  range: TimeRange;
  enabled: boolean;
}

function initialValues(schedule: Schedule | null): FormValues {
  if (schedule) {
    const selected = new Set(parseDaysOfWeek(schedule.days_of_week));
    return {
      time: schedule.time_of_day,
      days: Array.from({ length: 7 }, (_, i) => selected.has(i)),
      range: schedule.time_range,
      enabled: schedule.enabled,
    };
  }
  return { time: "06:00", days: Array(7).fill(false), range: "snapshot", enabled: true };
}

function validate(values: FormValues): string | null {
  if (!/^\d{2}:\d{2}$/.test(values.time)) return "a valid time is required";
  if (!values.days.some(Boolean)) return "select at least one day of the week";
  return null;
}

function ScheduleFormModal({
  schedule,
  onClose,
  onSaved,
}: {
  schedule: Schedule | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [values, setValues] = useState<FormValues>(() => initialValues(schedule));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function patch(partial: Partial<FormValues>) {
    setValues((v) => ({ ...v, ...partial }));
  }

  function toggleDay(index: number) {
    setValues((v) => ({ ...v, days: v.days.map((on, i) => (i === index ? !on : on)) }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const validationError = validate(values);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const payload = {
        time_of_day: values.time,
        days_of_week: daysToCsv(values.days),
        time_range: values.range,
        enabled: values.enabled,
      };
      if (schedule) {
        await api.updateSchedule(schedule.id, payload);
      } else {
        await api.createSchedule(payload);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <h2>{schedule ? "Edit schedule" : "Add schedule"}</h2>
          <button type="button" className="modal__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <form onSubmit={(e) => void handleSubmit(e)}>
          <div className="field-row">
            <label className="field">
              <span className="field__label">Time of day</span>
              <input
                type="time"
                className="field__input field__input--mono"
                value={values.time}
                onChange={(e) => patch({ time: e.target.value })}
                required
              />
            </label>
            <label className="field">
              <span className="field__label">Range</span>
              <select
                className="field__select"
                value={values.range}
                onChange={(e) => patch({ range: e.target.value as TimeRange })}
              >
                {RANGE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="field">
            <span className="field__label">Days of week</span>
            <div className="weekday-picker" role="group" aria-label="Days of week">
              {WEEKDAY_LABELS.map((label, i) => (
                <label key={label} className={`weekday-pill${values.days[i] ? " is-on" : ""}`}>
                  <input
                    type="checkbox"
                    checked={values.days[i]}
                    onChange={() => toggleDay(i)}
                    aria-label={label}
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>

          <label className="checkbox-field">
            <input type="checkbox" checked={values.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
            Enabled
          </label>

          {error && (
            <p className="alert alert--error" role="alert">
              {error}
            </p>
          )}

          <div className="btn-row">
            <button type="submit" className="btn btn--primary" disabled={saving}>
              {saving ? "Saving…" : schedule ? "Save changes" : "Create schedule"}
            </button>
            <button type="button" className="btn btn--ghost" onClick={onClose} disabled={saving}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ScheduleRow({
  schedule,
  confirmingDelete,
  deleting,
  onToggleEnabled,
  onEdit,
  onDeleteRequest,
  onDeleteCancel,
  onDeleteConfirm,
}: {
  schedule: Schedule;
  confirmingDelete: boolean;
  deleting: boolean;
  onToggleEnabled: () => void;
  onEdit: () => void;
  onDeleteRequest: () => void;
  onDeleteCancel: () => void;
  onDeleteConfirm: () => void;
}) {
  return (
    <tr>
      <td className="mono">{schedule.time_of_day}</td>
      <td className="cell-muted">{formatDaysOfWeek(schedule.days_of_week)}</td>
      <td className="cell-muted">{rangeLabel(schedule.time_range)}</td>
      <td>
        <button
          type="button"
          role="switch"
          aria-checked={schedule.enabled}
          className={`toggle${schedule.enabled ? " is-on" : ""}`}
          onClick={onToggleEnabled}
          title={schedule.enabled ? "Disable schedule" : "Enable schedule"}
        />
      </td>
      <td>
        {confirmingDelete ? (
          <span className="btn-row">
            <span className="cell-muted">Delete this schedule?</span>
            <button type="button" className="btn btn--danger btn--small" onClick={onDeleteConfirm} disabled={deleting}>
              {deleting ? "Deleting…" : "Confirm"}
            </button>
            <button type="button" className="btn btn--ghost btn--small" onClick={onDeleteCancel} disabled={deleting}>
              Cancel
            </button>
          </span>
        ) : (
          <span className="btn-row">
            <button type="button" className="btn btn--ghost btn--small" onClick={onEdit}>
              Edit
            </button>
            <button type="button" className="btn btn--danger btn--small" onClick={onDeleteRequest}>
              Delete
            </button>
          </span>
        )}
      </td>
    </tr>
  );
}

export default function Schedules() {
  const qc = useQueryClient();
  const { data: schedules, isLoading, error } = useQuery({ queryKey: ["schedules"], queryFn: api.schedules });

  const [formTarget, setFormTarget] = useState<"create" | Schedule | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => api.updateSchedule(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schedules"] }),
  });

  async function handleDelete(id: number) {
    setDeletingId(id);
    try {
      await api.deleteSchedule(id);
      void qc.invalidateQueries({ queryKey: ["schedules"] });
    } finally {
      setDeletingId(null);
      setConfirmDeleteId(null);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-header__title">
          <h1>Schedules</h1>
          <p>Automatic analysis runs, triggered on a recurring weekly cadence.</p>
        </div>
        <button type="button" className="btn btn--primary" onClick={() => setFormTarget("create")}>
          + Add schedule
        </button>
      </div>

      <div className="panel">
        {isLoading ? (
          <div className="empty-state">Loading schedules…</div>
        ) : error ? (
          <div className="empty-state">{error instanceof Error ? error.message : "failed to load schedules"}</div>
        ) : !schedules || schedules.length === 0 ? (
          <div className="empty-state">No schedules configured yet. Add one to run analyses automatically.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Days</th>
                  <th>Range</th>
                  <th>Enabled</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {schedules.map((s) => (
                  <ScheduleRow
                    key={s.id}
                    schedule={s}
                    confirmingDelete={confirmDeleteId === s.id}
                    deleting={deletingId === s.id}
                    onToggleEnabled={() => toggleEnabled.mutate({ id: s.id, enabled: !s.enabled })}
                    onEdit={() => setFormTarget(s)}
                    onDeleteRequest={() => setConfirmDeleteId(s.id)}
                    onDeleteCancel={() => setConfirmDeleteId(null)}
                    onDeleteConfirm={() => void handleDelete(s.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {formTarget && (
        <ScheduleFormModal
          schedule={formTarget === "create" ? null : formTarget}
          onClose={() => setFormTarget(null)}
          onSaved={() => {
            setFormTarget(null);
            void qc.invalidateQueries({ queryKey: ["schedules"] });
          }}
        />
      )}
    </div>
  );
}
