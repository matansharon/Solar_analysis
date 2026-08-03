import { NavLink } from "react-router-dom";

const LINKS: { to: string; label: string; end?: boolean }[] = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/plants", label: "Plants" },
  { to: "/runs", label: "Runs" },
  { to: "/schedules", label: "Schedules" },
  { to: "/settings", label: "Settings" },
];

export function Nav() {
  return (
    <nav className="app-nav" aria-label="Primary">
      <div className="app-nav__brand">
        <span className="app-nav__brand-mark" aria-hidden="true" />
        <span className="app-nav__brand-text">
          SOLAR<b>OPS</b>
        </span>
      </div>
      <ul className="app-nav__links">
        {LINKS.map((link) => (
          <li key={link.to}>
            <NavLink
              to={link.to}
              end={link.end}
              className={({ isActive }) => "app-nav__link" + (isActive ? " is-active" : "")}
            >
              {link.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
