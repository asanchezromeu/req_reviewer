import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import AppErrorBoundary from "@/components/AppErrorBoundary";
import Dashboard from "@/pages/Dashboard";

function App() {
  return (
    <AppErrorBoundary>
      <div className="App min-h-screen bg-[#F8F9FA] text-[#0A0D14]">
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<Dashboard />} />
          </Routes>
        </BrowserRouter>
        <Toaster position="bottom-right" richColors closeButton />
      </div>
    </AppErrorBoundary>
  );
}

export default App;
