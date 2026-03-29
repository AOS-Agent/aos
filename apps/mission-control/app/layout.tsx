"use client";

import "./globals.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import Sidebar from "@/components/layout/Sidebar";
import Topbar from "@/components/layout/Topbar";
import CommandPalette from "@/components/layout/CommandPalette";
import { useSSE } from "@/hooks/useSSE";
import { useNotifications } from "@/hooks/useNotifications";

function SSEProvider({ children }: { children: ReactNode }) {
  useSSE();
  useNotifications();
  return <>{children}</>;
}

export default function RootLayout({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return (
    <html lang="en" className="dark">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Mission Control</title>
        <meta name="description" content="AOS Control Plane" />
      </head>
      <body className="bg-bg text-text">
        <QueryClientProvider client={queryClient}>
          <SSEProvider>
          <div className="flex h-screen overflow-hidden">
            <Sidebar />
            <div className="flex-1 flex flex-col min-w-0">
              <Topbar />
              <main className="flex-1 overflow-y-auto px-8 py-6">
                {children}
              </main>
            </div>
            <CommandPalette />
          </div>
        </SSEProvider>
        </QueryClientProvider>
      </body>
    </html>
  );
}
