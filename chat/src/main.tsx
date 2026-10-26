// A chat with the TPC-H agent. CopilotKit talks AG-UI straight to the Python app's /agent
// route, with no CopilotKit runtime in between, and draws each show_chart call as a chart.
import "@copilotkit/react-core/v2/styles.css";
import { HttpAgent } from "@ag-ui/client";
import { CopilotChat, CopilotKitProvider } from "@copilotkit/react-core/v2";
import { showChartRenderer } from "@s-layer/flint-charts/copilotkit";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

const agent = new HttpAgent({ url: "/agent" });
const renderers = [showChartRenderer({ height: 360 })];

function App() {
  return (
    <CopilotKitProvider selfManagedAgents={{ default: agent }} renderToolCalls={renderers}>
      <main style={{ height: "100vh", maxWidth: 960, margin: "0 auto" }}>
        <CopilotChat
          labels={{
            chatInputPlaceholder: "Ask about TPC-H sales, e.g. net revenue by region and year",
          }}
        />
      </main>
    </CopilotKitProvider>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("no #root element");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
