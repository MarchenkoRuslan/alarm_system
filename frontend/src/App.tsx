import { BrowserRouter, Routes, Route } from "react-router-dom";
import AlertsPage from "./pages/AlertsPage/AlertsPage";
import CreateAlertPage from "./pages/CreateAlertPage/CreateAlertPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AlertsPage />} />
        <Route path="/create" element={<CreateAlertPage />} />
      </Routes>
    </BrowserRouter>
  );
}
