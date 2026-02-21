import type { Metadata } from 'next'
import { JetBrains_Mono, Syne } from 'next/font/google'
import './globals.css'
import { Sidebar } from '@/components/Sidebar'
import { Providers } from '@/components/Providers'

const jetbrains = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jetbrains',
  display: 'swap',
})

const syne = Syne({
  subsets: ['latin'],
  variable: '--font-syne',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Alphapoly - Polymarket Alpha Detection',
  description:
    'Real-time alpha opportunities from Polymarket prediction markets',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={`${jetbrains.variable} ${syne.variable}`}>
      <body className="font-mono bg-void text-text-primary antialiased">
        <Providers>
          <div className="flex h-screen">
            <Sidebar />
            <main className="flex-1 flex flex-col ml-48 p-6 min-h-0">
              {children}
            </main>
          </div>
        </Providers>
      </body>
    </html>
  )
}
