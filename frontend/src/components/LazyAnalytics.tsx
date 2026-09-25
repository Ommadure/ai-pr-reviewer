import { lazy, Suspense } from "react";
import type { Analytics } from "../api/types";
import { Loading } from "./States";

// Recharts is most of the bundle's weight: load it only on pages that draw charts.
const AnalyticsView = lazy(() => import("./AnalyticsView").then((m) => ({ default: m.AnalyticsView })));

export function Charts({ data }: { data: Analytics }) {
  return (
    <Suspense fallback={<Loading label="Loading charts" />}>
      <AnalyticsView data={data} />
    </Suspense>
  );
}
