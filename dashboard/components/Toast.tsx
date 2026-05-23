"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
} from "react";

/* ---- Types --------------------------------------------------------- */

type ToastType = "success" | "error" | "info" | "warning";

interface Toast {
  id: number;
  type: ToastType;
  message: string;
  leaving: boolean;
}

interface ToastContextValue {
  addToast: (opts: { type: ToastType; message: string }) => void;
}

/* ---- Context ------------------------------------------------------- */

const ToastContext = createContext<ToastContextValue>({
  addToast: () => {},
});

export function useToast() {
  return useContext(ToastContext);
}

/* ---- Colours per type ---------------------------------------------- */

const TYPE_STYLES: Record<
  ToastType,
  { bg: string; border: string; text: string; icon: string }
> = {
  success: {
    bg: "rgba(16, 185, 129, 0.10)",
    border: "rgba(16, 185, 129, 0.25)",
    text: "#6ee7b7",
    icon: "M5 13l4 4L19 7",
  },
  error: {
    bg: "rgba(239, 68, 68, 0.10)",
    border: "rgba(239, 68, 68, 0.25)",
    text: "#fca5a5",
    icon: "M6 18L18 6M6 6l12 12",
  },
  info: {
    bg: "rgba(59, 130, 246, 0.10)",
    border: "rgba(59, 130, 246, 0.25)",
    text: "#93c5fd",
    icon: "M12 8v4m0 4h.01",
  },
  warning: {
    bg: "rgba(245, 158, 11, 0.10)",
    border: "rgba(245, 158, 11, 0.25)",
    text: "#fcd34d",
    icon: "M12 9v4m0 4h.01",
  },
};

const AUTO_DISMISS_MS = 5000;
const SLIDE_DURATION_MS = 250;

/* ---- Provider ------------------------------------------------------ */

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const removeToast = useCallback((id: number) => {
    /* Mark as leaving (triggers slide-out), then remove after animation */
    setToasts((prev) =>
      prev.map((t) => (t.id === id ? { ...t, leaving: true } : t)),
    );
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, SLIDE_DURATION_MS);
  }, []);

  const addToast = useCallback(
    ({ type, message }: { type: ToastType; message: string }) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, type, message, leaving: false }]);
      setTimeout(() => removeToast(id), AUTO_DISMISS_MS);
    },
    [removeToast],
  );

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      {/* Toast container -- bottom-right, stacked */}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        style={{
          position: "fixed",
          bottom: 20,
          right: 20,
          zIndex: 9999,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          pointerEvents: "none",
        }}
      >
        {toasts.map((toast) => (
          <ToastItem
            key={toast.id}
            toast={toast}
            onClose={() => removeToast(toast.id)}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/* ---- Single toast item --------------------------------------------- */

function ToastItem({
  toast,
  onClose,
}: {
  toast: Toast;
  onClose: () => void;
}) {
  const style = TYPE_STYLES[toast.type];
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    /* Trigger enter animation on next frame */
    const raf = requestAnimationFrame(() => setMounted(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  const visible = mounted && !toast.leaving;

  return (
    <div
      role={toast.type === "error" ? "alert" : "status"}
      style={{
        pointerEvents: "auto",
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        minWidth: 300,
        maxWidth: 420,
        padding: "12px 14px",
        borderRadius: 8,
        border: `1px solid ${style.border}`,
        backgroundColor: style.bg,
        backdropFilter: "blur(12px)",
        color: style.text,
        fontSize: 13,
        lineHeight: 1.45,
        transform: visible ? "translateX(0)" : "translateX(120%)",
        opacity: visible ? 1 : 0,
        transition: `transform ${SLIDE_DURATION_MS}ms cubic-bezier(.4,0,.2,1), opacity ${SLIDE_DURATION_MS}ms ease`,
      }}
    >
      {/* Icon */}
      <svg
        width={16}
        height={16}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flexShrink: 0, marginTop: 2 }}
      >
        <path d={style.icon} />
      </svg>

      {/* Message */}
      <span style={{ flex: 1 }}>{toast.message}</span>

      {/* Close button */}
      <button
        type="button"
        onClick={onClose}
        style={{
          background: "none",
          border: "none",
          color: "inherit",
          cursor: "pointer",
          padding: 0,
          opacity: 0.6,
          flexShrink: 0,
          marginTop: 1,
        }}
        aria-label="Dismiss"
      >
        <svg
          width={14}
          height={14}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>
    </div>
  );
}
