import { Metadata } from "next";

export const metadata: Metadata = {
  title: "Agent Conversation |  Neo 🪄",
  description: "Interactive agent conversation powered by  Neo 🪄",
  openGraph: {
    title: "Agent Conversation |  Neo 🪄",
    description: "Interactive agent conversation powered by  Neo 🪄",
    type: "website",
  },
};

export default function AgentsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
} 