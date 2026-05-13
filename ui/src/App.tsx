import { Routes, Route } from "react-router-dom";

import Layout from "./components/Layout";
import Corrections from "./pages/Corrections";
import Home from "./pages/Home";
import Query from "./pages/Query";
import SpecGraph from "./pages/SpecGraph";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/spec-graph" element={<SpecGraph />} />
        <Route path="/spec/draft/:draftId" element={<SpecGraph />} />
        <Route path="/query" element={<Query />} />
        <Route path="/corrections" element={<Corrections />} />
      </Routes>
    </Layout>
  );
}
