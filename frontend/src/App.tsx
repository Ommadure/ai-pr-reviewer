import { QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { createBrowserRouter, RouterProvider } from "react-router";
import { makeQueryClient, routes } from "./routes";

const router = createBrowserRouter(routes);
const queryClient = makeQueryClient();

export default function App() {
  return (
    // reducedMotion="user": with the OS "reduce motion" setting on, Motion drops movement
    // (slides, springs, rolls) and keeps only opacity changes. CSS animations follow the same
    // setting in index.css.
    <MotionConfig reducedMotion="user">
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </MotionConfig>
  );
}
