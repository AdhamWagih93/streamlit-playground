import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/shell";
import { Spinner } from "./components/ui";
import { useAuth } from "./lib/auth";
import Login from "./pages/Login";

const Overview = lazy(() => import("./pages/Overview"));
const Fleet = lazy(() => import("./pages/Fleet"));
const Events = lazy(() => import("./pages/Events"));
const Actions = lazy(() => import("./pages/Actions"));
const Security = lazy(() => import("./pages/Security"));
const Incidents = lazy(() => import("./pages/Incidents"));
const Assistant = lazy(() => import("./pages/Assistant"));
const Architecture = lazy(() => import("./pages/Architecture"));
const Technology = lazy(() => import("./pages/Technology"));
const Teams = lazy(() => import("./pages/Teams"));
const People = lazy(() => import("./pages/People"));
const Governance = lazy(() => import("./pages/Governance"));
const Settings = lazy(() => import("./pages/Settings"));

export default function App() {
  const { me, loading } = useAuth();
  if (loading) return <Spinner label="Signing you in…" />;
  if (!me) return <Login />;

  const admin = me.is_admin;
  return (
    <Shell>
      <Suspense fallback={<Spinner />}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/fleet" element={<Fleet />} />
          <Route path="/events" element={<Events />} />
          <Route path="/actions" element={<Actions />} />
          <Route path="/security" element={<Security />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/assistant" element={<Assistant />} />
          <Route path="/architecture" element={admin ? <Architecture /> : <Navigate to="/" />} />
          <Route path="/technology" element={admin ? <Technology /> : <Navigate to="/" />} />
          <Route path="/teams" element={admin ? <Teams /> : <Navigate to="/" />} />
          <Route path="/people" element={admin ? <People /> : <Navigate to="/" />} />
          <Route path="/governance" element={admin ? <Governance /> : <Navigate to="/" />} />
          <Route path="/settings" element={admin ? <Settings /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Suspense>
    </Shell>
  );
}
