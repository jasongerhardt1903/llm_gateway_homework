import { useState } from "react";
import {
  Activity,
  Boxes,
  LayoutDashboard,
  MessagesSquare,
  Route,
  ScrollText,
} from "lucide-react";
import ModelsPage from "./pages/ModelsPage.jsx";
import ProfilesPage from "./pages/ProfilesPage.jsx";
import ChatPage from "./pages/ChatPage.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import TracePage from "./pages/TracePage.jsx";
import { cn } from "./lib/utils.js";

/** 导航与需求中"业务与交互层"的功能一一对应；文案与既有版本保持一致。 */
const TABS = [
  { key: "models", label: "模型定义", icon: Boxes, component: ModelsPage },
  { key: "profiles", label: "Profile", icon: Route, component: ProfilesPage },
  { key: "chat", label: "Chat", icon: MessagesSquare, component: ChatPage },
  { key: "dashboard", label: "Dashboard", icon: LayoutDashboard, component: DashboardPage },
  { key: "trace", label: "Trace", icon: ScrollText, component: TracePage },
];

export default function App() {
  const [active, setActive] = useState("models");
  const current = TABS.find((tab) => tab.key === active) ?? TABS[0];
  const Page = current.component;

  return (
    <div className="flex min-h-screen bg-bg">
      <aside className="sticky top-0 flex h-screen w-[212px] shrink-0 flex-col border-r border-border bg-panel/60 px-3 py-5">
        <div className="mb-6 flex items-center gap-2 px-2">
          <Activity size={18} className="shrink-0 text-accent" />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-fg">LLM Gateway</div>
            <div className="truncate text-xs text-faint">控制台</div>
          </div>
        </div>
        <nav className="flex flex-col gap-1">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = tab.key === active;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActive(tab.key)}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "flex cursor-pointer items-center gap-2.5 rounded-md border border-transparent px-2.5 py-2 text-left text-sm font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-hover",
                  isActive
                    ? "border-accent/25 bg-accent/12 text-fg"
                    : "text-muted hover:bg-hover hover:text-fg2"
                )}
              >
                <Icon size={15} className={cn("shrink-0", isActive && "text-accent")} />
                {tab.label}
              </button>
            );
          })}
        </nav>
        <div className="mt-auto px-2 text-xs text-faint">
          模型定义 · Profile · Chat · Dashboard · Trace
        </div>
      </aside>

      <main className="min-w-0 flex-1 px-6 py-6">
        <div className="mx-auto max-w-[1180px]">
          <Page />
        </div>
      </main>
    </div>
  );
}
