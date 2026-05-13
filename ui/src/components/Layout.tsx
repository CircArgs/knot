import { ReactNode } from "react";
import { Link } from "react-router-dom";

interface Props { children: ReactNode }

export default function Layout({ children }: Props) {
  return (
    <div className="flex flex-col h-full">
      <header className="border-b px-6 py-3 flex items-center gap-6">
        <Link to="/" className="font-semibold text-lg">knot</Link>
        <nav className="flex gap-4 text-sm text-knot-muted">
          <Link to="/" className="hover:text-knot-ink">home</Link>
          <Link to="/spec-graph" className="hover:text-knot-ink">spec graph</Link>
          <Link to="/query" className="hover:text-knot-ink">query</Link>
          <Link to="/corrections" className="hover:text-knot-ink">corrections</Link>
        </nav>
      </header>
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}
