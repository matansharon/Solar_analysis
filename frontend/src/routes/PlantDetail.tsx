import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { LineChart } from "../lineChart";

function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function PlantDetail() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const idValid = Number.isFinite(id);

  const plantsQuery = useQuery({ queryKey: ["plants"], queryFn: api.plants });
  const plant = plantsQuery.data?.find((p) => p.id === id);

  const devicesQuery = useQuery({
    queryKey: ["plantDevices", id],
    queryFn: () => api.plantDevices(id),
    enabled: idValid,
  });
  const alertsQuery = useQuery({
    queryKey: ["plantAlerts", id],
    queryFn: () => api.plantAlerts(id),
    enabled: idValid,
  });
  const energyQuery = useQuery({
    queryKey: ["plantEnergy", id],
    queryFn: async () => {
      for (const granularity of ["day", "month", "year"] as const) {
        const points = await api.plantEnergy(id, undefined, granularity);
        if (points.length > 0) return points;
      }
      return [];
    },
    enabled: idValid,
  });
  const powerQuery = useQuery({
    queryKey: ["plantPower", id],
    queryFn: () => api.plantPower(id),
    enabled: idValid,
  });

  if (!idValid) {
    return <div className="empty-state">Invalid plant id.</div>;
  }
  if (plantsQuery.isLoading) {
    return <div className="empty-state">Loading…</div>;
  }
  if (!plant) {
    return <div className="empty-state">Plant not found.</div>;
  }

  const energyPoints = (energyQuery.data ?? [])
    .filter((p) => p.energy_kwh != null)
    .map((p) => ({ x: p.timestamp_local, y: p.energy_kwh as number }));
  const powerPoints = (powerQuery.data ?? [])
    .filter((p) => p.power_kw != null)
    .map((p) => ({ x: p.timestamp_local, y: p.power_kw as number }));

  return (
    <div>
      <div className="page-header">
        <div className="page-header__title">
          <h1>{plant.name}</h1>
          <p>
            <span className={`badge-platform badge-platform--${plant.platform}`}>{plant.platform}</span>
          </p>
        </div>
        <Link className="btn btn--ghost" to="/plants">
          Back to plants
        </Link>
      </div>

      <section style={{ marginBottom: 24 }}>
        <h2>Energy history</h2>
        {energyQuery.isLoading ? (
          <div className="empty-state">Loading…</div>
        ) : (
          <LineChart points={energyPoints} unit="kWh" />
        )}
      </section>

      {/* No adapter currently populates power_timeseries, so this chart is always
          empty on real runs today (infrastructure for future adapter work). */}
      <section style={{ marginBottom: 24 }}>
        <h2>Power history</h2>
        {powerQuery.isLoading ? (
          <div className="empty-state">Loading…</div>
        ) : (
          <LineChart points={powerPoints} color="var(--cyan)" unit="kW" />
        )}
      </section>

      <section style={{ marginBottom: 24 }}>
        <h2>Devices</h2>
        {devicesQuery.isLoading ? (
          <div className="empty-state">Loading…</div>
        ) : !devicesQuery.data || devicesQuery.data.length === 0 ? (
          <div className="empty-state">No device data yet. Run an analysis to populate this.</div>
        ) : (
          <div className="panel table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Model</th>
                  <th>Status</th>
                  <th>Power</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {devicesQuery.data.map((d) => (
                  <tr key={d.device_id}>
                    <td>{d.device_id}</td>
                    <td className="cell-muted">{d.model ?? "—"}</td>
                    <td>{d.status}</td>
                    <td className="mono">{d.current_power_kw != null ? `${d.current_power_kw} kW` : "—"}</td>
                    <td className="cell-muted">{formatTimestamp(d.last_seen_local)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2>Recent alerts</h2>
        {alertsQuery.isLoading ? (
          <div className="empty-state">Loading…</div>
        ) : !alertsQuery.data || alertsQuery.data.length === 0 ? (
          <div className="empty-state">No alerts recorded.</div>
        ) : (
          <ul className="skip-list">
            {alertsQuery.data.map((a, i) => (
              <li key={`${a.alert_id}-${i}`}>
                <strong>{a.severity}</strong> — {a.message ?? a.code ?? "alert"}{" "}
                <span className="cell-muted">{formatTimestamp(a.timestamp_local)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
