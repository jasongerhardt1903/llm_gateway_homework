import { useState } from "react";
import ModelsPage from "./pages/ModelsPage.jsx";
import ProfilesPage from "./pages/ProfilesPage.jsx";
import ChatPage from "./pages/ChatPage.jsx";
import DashboardPage from "./pages/DashboardPage.jsx";
import TracePage from "./pages/TracePage.jsx";

/** 页面与需求中"业务与交互层"的功能一一对应。 */
const TABS = [
  { key: "models", label: "模型定义", component: ModelsPage },
  { key: "profiles", label: "Profile", component: ProfilesPage },
  { key: "chat", label: "Chat", component: ChatPage },
  { key: "dashboard", label: "Dashboard", component: DashboardPage },
  { key: "trace", label: "Trace", component: TracePage },
];

export default function App() {
  const [active, setActive] = useState("models");
  const current = TABS.find((tab) => tab.key === active) ?? TABS[0];
  const Page = current.component;

  return (
    <div className="app">
      <header className="app-header">
        <h1>LLM Gateway 控制台</h1>
        <nav className="tabs">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={tab.key === active ? "tab tab-active" : "tab"}
              onClick={() => setActive(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="app-body">
        <Page />
      </main>
    </div>
  );
}
