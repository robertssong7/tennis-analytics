import type { Metadata } from 'next'
import './globals.css'
import Nav from '@/components/Nav'

export const metadata: Metadata = {
  title: 'TennisIQ — 538-Style Tennis Analytics',
  description: 'Matchup intelligence, pattern analytics, and predictive modeling for ATP tennis.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main style={{ paddingTop: '68px' }}>
          {children}
        </main>
        <footer style={{
          background: 'var(--ink)',
          padding: '28px 48px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginTop: '80px',
        }}>
          <div style={{
            fontFamily: 'Playfair Display, serif',
            fontSize: '15px',
            color: 'rgba(233,229,220,0.7)',
            display: 'flex', alignItems: 'center', gap: '10px',
          }}>
            <span style={{ fontSize: '18px' }}>●</span>
            TennisIQ
          </div>
          <div style={{
            fontFamily: 'DM Mono, monospace',
            fontSize: '10.5px',
            color: 'rgba(233,229,220,0.3)',
          }}>
            Built on ATP/WTA charting data · Python · Next.js · D3
          </div>
        </footer>
      </body>
    </html>
  )
}
