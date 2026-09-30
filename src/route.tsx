import { createBrowserRouter } from "react-router-dom";
import { lazy } from "react";

/* ========================================================
   ReactKit Routing
   - This project exposes ONLY the SQL Query Optimizer page.
   - All other feature routes are commented out: the SQL
     Optimizer is the single home route (/ and /sql-optimizer).
   ======================================================== */

// SQL Optimizer — the only page in this build; also the home route.
const SqlOptimizer = lazy(() => import("./features/sql-optimizer/SqlOptimizer"));

/*
   Routes below are commented out and not bundled.
   Re-enable any entry (uncomment the import + route) to restore it.

   const AppLayout = lazy(() => import("./components/layout/AppLayout"));
   const AuthLayout = lazy(() => import("./features/auth"));
   const Home = lazy(() => import("./features/Dashboard/Home"));
   const SignIn = lazy(() => import("./features/auth/components/signin-form"));
   const SignUp = lazy(() => import("./features/auth/components/signup-form"));
   const NotFound = lazy(() => import("./features/OtherPage"));
   const UserProfiles = lazy(() => import("./features/UserProfile/layout"));
   const FormElements = lazy(() => import("./features/Forms/FormElements"));
   const BasicTables = lazy(() => import("./features/Tables/BasicTables"));
*/

export const router = createBrowserRouter([
  {
    // Home route: the SQL Optimizer is the only page in this build.
    // Visiting "/" lands here directly (no app shell, no auth gate).
    path: "/",
    element: <SqlOptimizer />,
  },
  {
    // Explicit SQL Optimizer route in case the user navigates here directly.
    path: "/sql-optimizer",
    element: <SqlOptimizer />,
  },
  {
    // Catch-all: since the rest of the app is commented out, show a clean
    // 404 page that still fits the UI.
    path: "*",
    element: (
      <div className="flex min-h-screen w-full flex-col items-center justify-center gap-3 px-6 py-24 text-center">
        <span className="text-6xl" aria-hidden="true">404</span>
        <h2 className="text-2xl font-semibold text-gray-900 dark:text-gray-50">
          Page not found
        </h2>
        <p className="max-w-sm text-gray-500 dark:text-gray-400">
          The only available route in this build is{" "}
          <code className="rounded bg-gray-100 px-1.5 py-0.5 text-xs font-mono text-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:ring-1 dark:ring-gray-700/50">
            /sql-optimizer
          </code>
          .
        </p>
        <a
          href="/sql-optimizer"
          className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-brand-500 px-5 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-brand-600"
        >
          Go to the SQL Optimizer
        </a>
      </div>
    ),
  },
]);
