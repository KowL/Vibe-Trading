import { Suspense, lazy, type ComponentType } from "react";
import { createBrowserRouter } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";

const Home = lazy(() => import("@/pages/Home").then((m) => ({ default: m.Home })));
const Agent = lazy(() => import("@/pages/Agent").then((m) => ({ default: m.Agent })));
const RunDetail = lazy(() =>
  import("@/pages/RunDetail").then((m) => ({ default: m.RunDetail })),
);
const Compare = lazy(() =>
  import("@/pages/Compare").then((m) => ({ default: m.Compare })),
);
const Settings = lazy(() =>
  import("@/pages/Settings").then((m) => ({ default: m.Settings })),
);
const Runtime = lazy(() =>
  import("@/pages/Runtime").then((m) => ({ default: m.Runtime })),
);
const Correlation = lazy(() =>
  import("@/pages/Correlation").then((m) => ({ default: m.Correlation })),
);
const AlphaZoo = lazy(() =>
  import("@/pages/AlphaZoo").then((m) => ({ default: m.AlphaZoo })),
);

function PageLoader() {
  return (
    <div className="flex h-[60vh] items-center justify-center text-muted-foreground">
      Loading…
    </div>
  );
}

function wrap(Component: ComponentType) {
  return (
    <Suspense fallback={<PageLoader />}>
      <Component />
    </Suspense>
  );
}

const AShare = lazy(() =>
  import("@/pages/ashare/ASharePage").then((m) => ({ default: m.ASharePage })),
);
const LimitUp = lazy(() =>
  import("@/pages/ashare/LimitUpPage").then((m) => ({ default: m.LimitUpPage })),
);
const Portfolio = lazy(() =>
  import("@/pages/ashare/PortfolioPage").then((m) => ({ default: m.PortfolioPage })),
);
const Report = lazy(() =>
  import("@/pages/ashare/ReportPage").then((m) => ({ default: m.ReportPage })),
);

const DecisionTree = lazy(() =>
  import("@/pages/DecisionTree").then((m) => ({ default: m.DecisionTree })),
);
const Strategy = lazy(() =>
  import("@/pages/ashare/StrategyPage").then((m) => ({ default: m.default })),
);
const StrategyCompare = lazy(() =>
  import("@/pages/ashare/ComparePage").then((m) => ({ default: m.default })),
);
const StrategyMarket = lazy(() =>
  import("@/pages/ashare/StrategyMarketPage").then((m) => ({ default: m.default })),
);

export const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: wrap(Home) },
      { path: "/agent", element: wrap(Agent) },
      { path: "/runtime", element: wrap(Runtime) },
      { path: "/alpha-zoo", element: wrap(AlphaZoo) },
      { path: "/settings", element: wrap(Settings) },
      { path: "/correlation", element: wrap(Correlation) },
      { path: "/ashare", element: wrap(AShare) },
      { path: "/ashare/limit-up", element: wrap(LimitUp) },
      { path: "/ashare/portfolio", element: wrap(Portfolio) },
      { path: "/ashare/report", element: wrap(Report) },
      { path: "/ashare/strategy", element: wrap(Strategy) },
      { path: "/ashare/strategy/compare", element: wrap(StrategyCompare) },
      { path: "/ashare/strategy/market", element: wrap(StrategyMarket) },
      { path: "/decision-tree", element: wrap(DecisionTree) },
      { path: "/runs/:runId", element: wrap(RunDetail) },
      { path: "/compare", element: wrap(Compare) },
      { path: "/alpha-zoo/bench", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/compare", element: wrap(AlphaZoo) },
      { path: "/alpha-zoo/:alphaId", element: wrap(AlphaZoo) },
    ],
  },
]);
