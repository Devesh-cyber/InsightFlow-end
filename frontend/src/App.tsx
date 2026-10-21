import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { RequireAuth } from "./auth/AuthProvider";
import { PasswordRecovery } from "./auth/PasswordRecovery";
import { DatasetLayout, HomeLayout } from "./components/Layout";
import { Loading } from "./components/ui";
import Datasets from "./pages/Datasets";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Overview from "./pages/Overview";

// Less-visited pages load on demand, so the first screen arrives faster.
const Health = lazy(() => import("./pages/Health"));
const Columns = lazy(() => import("./pages/Columns"));
const Relationships = lazy(() => import("./pages/Relationships"));
const Charts = lazy(() => import("./pages/Charts"));
const Clean = lazy(() => import("./pages/Clean"));
const History = lazy(() => import("./pages/History"));
const Export = lazy(() => import("./pages/Export"));
const MLReady = lazy(() => import("./pages/MLReady"));

export default function App() {
  return (
    <Suspense fallback={<Loading />}>
      <PasswordRecovery />
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth><HomeLayout /></RequireAuth>}>
          <Route path="/app" element={<Datasets />} />
        </Route>
        <Route path="/d/:id" element={<RequireAuth><DatasetLayout /></RequireAuth>}>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview" element={<Overview />} />
          <Route path="health" element={<Health />} />
          <Route path="columns" element={<Columns />} />
          <Route path="relationships" element={<Relationships />} />
          <Route path="charts" element={<Charts />} />
          <Route path="clean" element={<Clean />} />
          <Route path="ml" element={<MLReady />} />
          <Route path="history" element={<History />} />
          <Route path="export" element={<Export />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}