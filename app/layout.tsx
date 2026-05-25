import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Swing Coach — Score your golf swing",
  description:
    "Upload or record a golf swing and get an instant, on-device scorecard: phase timing, body rotation, and ball-strike factors graded against tour biomechanics. Nothing leaves your device.",
  openGraph: {
    title: "Swing Coach — Score your golf swing",
    description:
      "Upload or record a swing and get an instant scorecard, graded against tour biomechanics. Runs entirely in your browser.",
    type: "website",
  },
  twitter: {
    card: "summary",
    title: "Swing Coach — Score your golf swing",
    description:
      "Instant, on-device golf swing scorecard graded against tour biomechanics.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
