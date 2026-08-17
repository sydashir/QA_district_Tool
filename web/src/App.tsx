import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./screens/Dashboard";
import Findings from "./screens/Findings";
import FindingDetail from "./screens/FindingDetail";
import Runs from "./screens/Runs";
import Changes from "./screens/Changes";

const tabs = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/findings", label: "Findings", end: false },
  { to: "/changes", label: "What changed", end: false },
  { to: "/runs", label: "Runs", end: false },
];

export default function App() {
  return (
    <>
      <header className="top">
        <div className="inner">
          <h1>District Site Auditor</h1>
          <nav style={{ display: "flex", gap: 18 }}>
            {tabs.map((t) => (
              <NavLink key={t.to} to={t.to} end={t.end}
                className={({ isActive }) => (isActive ? "active" : "")}>
                {t.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <div className="wrap">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/findings" element={<Findings />} />
          <Route path="/findings/:hash" element={<FindingDetail />} />
          <Route path="/changes" element={<Changes />} />
          <Route path="/runs" element={<Runs />} />
        </Routes>
      </div>
    </>
  );
}
