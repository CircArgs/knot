import { ReactNode, useEffect } from "react";

interface Props {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  widthClass?: string;
}

/**
 * Plain Tailwind modal — single instance per page, escape to close, click
 * backdrop to close. Keep this small; no portal needed for our use case.
 */
export default function Modal({ open, title, onClose, children, widthClass }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={`bg-white rounded-lg shadow-xl ${widthClass ?? "w-[480px]"} max-h-[90vh] flex flex-col`}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-4 py-3 border-b border-slate-200 flex items-center justify-between">
          <h2 className="font-semibold text-slate-900">{title}</h2>
          <button
            className="text-slate-400 hover:text-slate-700 text-xl leading-none"
            onClick={onClose}
            aria-label="close"
          >
            ×
          </button>
        </header>
        <div className="p-4 overflow-y-auto flex-1">{children}</div>
      </div>
    </div>
  );
}
