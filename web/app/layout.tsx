import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "IoT Micro-Climate & Precision Agriculture",
  description: "ESP32 + Firebase RTDB Smart Monitoring & Irrigation Dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
