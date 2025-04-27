import { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Shared Conversation',
  description: 'View a shared AI conversation',
  openGraph: {
    title: 'Shared AI Conversation',
    description: 'View a shared AI conversation from  Neo 🪄',
    images: ['/logo.svg'],
  },
};

export default function ThreadLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
} 