import { Routes, Route } from "react-router-dom";

import Layout from "./components/Layout";
import Home from "./pages/Home";
import SpecGraph from "./pages/SpecGraph";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/spec-graph" element={<SpecGraph />} />
        <Route path="/spec/draft/:draftId" element={<SpecGraph />} />
      </Routes>
    </Layout>
  );
}
