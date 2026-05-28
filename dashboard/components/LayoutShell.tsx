"use client";

import { useEffect, useLayoutEffect, useState } from "react";
import { usePathname } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import { ToastProvider } from "@/components/Toast";

export default function LayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarPreferenceLoaded, setSidebarPreferenceLoaded] = useState(false);

  useLayoutEffect(() => {
    setSidebarCollapsed(localStorage.getItem("lerim_sidebar_collapsed") === "1");
    setSidebarPreferenceLoaded(true);
  }, []);

  useEffect(() => {
    if (!sidebarPreferenceLoaded) return;
    localStorage.setItem("lerim_sidebar_collapsed", sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed, sidebarPreferenceLoaded]);

  const contentClassName = pathname === "/context-graph"
      ? "w-full px-4 py-5 pb-24 sm:px-6 md:p-0"
      : pathname === "/memory"
        ? "mx-auto max-w-7xl px-4 py-5 pb-24 sm:px-6 md:py-8 md:pb-8"
        : "mx-auto max-w-6xl px-4 py-5 pb-24 sm:px-6 md:py-8 md:pb-8";

  return (
    <ToastProvider>
      <div className="dashboard-shell" data-sidebar-state={sidebarCollapsed ? "collapsed" : "expanded"}>
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggleCollapsed={() => setSidebarCollapsed((current) => !current)}
        />
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-md focus:bg-[var(--accent-blue)] focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-white"
        >
          Skip to Content
        </a>
        <main id="main-content" className="dashboard-main min-h-screen">
          <div className={contentClassName}>
            {children}
          </div>
        </main>
      </div>
    </ToastProvider>
  );
}
