import type { Metadata, Viewport } from "next";
import "./globals.css";
import LayoutShell from "@/components/LayoutShell";

export const metadata: Metadata = {
  title: "Lerim Dashboard",
  description: "Local dashboard for Lerim runtime and context activity",
};

export const viewport: Viewport = {
  colorScheme: "dark",
  themeColor: "#0a0f1e",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[var(--bg)] text-[var(--text)] antialiased">
        <LayoutShell>{children}</LayoutShell>
      </body>
    </html>
  );
}
