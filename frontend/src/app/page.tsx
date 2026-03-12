'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import PlayerCard, { CardData } from '@/components/PlayerCard'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function Home() {
  const [topPlayers, setTopPlayers] = useState<CardData[]>([])
  const [stats, setStats] = useState({ players: 0, matches: 0, patterns: 0 })

  useEffect(() => {
    fetch(`${API}/cards?page_size=20`)
      .then(r => r.json())
      .then(d => {
        const players = d.players || []
        setTopPlayers(players.map((p: any) => ({
          name: p.name,
          fifa_rating: p.fifa_rating,
          card_tier: p.card_tier,
          elo_display: p.elo_display,
          elo_hard: p.elo_hard,
          elo_clay: p.elo_clay,
          elo_grass: p.elo_grass,
          elo_peak: p.elo_peak,
          elo_match_count: p.elo_match_count,
          country: p.country,
          card_attributes: p.card_attributes,
        })))
        setStats(s => ({ ...s, players: players.length > 0 ? d.total || players.length : 0 }))
      })
      .catch(() => {})
  }, [])

  return (
    <div>
      {/* Hero section */}
      <section style={{
        minHeight: 'calc(100vh - 68px)',
        display: 'grid',
        gridTemplateColumns: '400px 1fr',
        padding: '0 64px',
        gap: '64px',
        alignItems: 'center',
        position: 'relative',
        overflow: 'hidden',
      }}>
        {/* Ambient balls */}
        <AmbientBall x={70} y={20} size={400} />
        <AmbientBall x={20} y={60} size={300} />
        <AmbientBall x={85} y={75} size={250} />

        {/* Left column */}
        <div style={{ zIndex: 1 }}>
          {/* Eyebrow */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '8px',
            marginBottom: '24px',
          }}>
            <div style={{
              width: '6px', height: '6px',
              borderRadius: '50%',
              background: 'var(--accent)',
              animation: 'pulse 2s infinite',
            }}/>
            <span style={{
              fontFamily: 'DM Mono, monospace',
              fontSize: '11px', textTransform: 'uppercase',
              letterSpacing: '0.12em', color: 'var(--accent)',
            }}>ATP / WTA Intelligence Platform</span>
          </div>

          <style>{`
            @keyframes pulse {
              0%, 100% { opacity: 1; transform: scale(1); }
              50%       { opacity: 0.5; transform: scale(1.4); }
            }
          `}</style>

          <h1 style={{
            fontFamily: 'Playfair Display, serif',
            fontSize: 'clamp(44px, 4.5vw, 62px)',
            lineHeight: 1.08, fontWeight: 700,
            color: 'var(--ink)', marginBottom: '24px',
          }}>
            Tennis data,<br/>
            <em style={{ color: 'var(--accent)', fontStyle: 'italic' }}>re-imagined.</em>
          </h1>

          <p style={{
            fontFamily: 'DM Sans, sans-serif',
            fontSize: '15px', color: 'var(--ink-mid)',
            lineHeight: 1.8, maxWidth: '320px', marginBottom: '40px',
          }}>
            538-style pattern analytics. Matchup intelligence.
            Predictive modeling. Built on the most comprehensive
            shot-by-shot dataset in tennis.
          </p>

          {/* Stats row */}
          <div style={{ display: 'flex', gap: '32px', marginBottom: '40px' }}>
            {[
              { value: stats.players || '—', label: 'Players Rated' },
              { value: '3,400+', label: 'H2H Records' },
              { value: '500K+', label: 'Charted Points' },
            ].map(({ value, label }) => (
              <div key={label}>
                <div style={{
                  fontFamily: 'Playfair Display, serif',
                  fontSize: '26px', fontWeight: 700,
                  color: 'var(--ink)',
                }}>{value}</div>
                <div style={{
                  fontFamily: 'DM Mono, monospace',
                  fontSize: '10.5px', textTransform: 'uppercase',
                  letterSpacing: '0.06em', color: 'var(--ink-soft)',
                  marginTop: '2px',
                }}>{label}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: '12px' }}>
            <Link href="/compare" style={{
              background: 'var(--ink)', color: 'var(--bg)',
              padding: '12px 28px', borderRadius: '28px',
              fontFamily: 'DM Sans, sans-serif', fontSize: '15px', fontWeight: 500,
              textDecoration: 'none',
            }}>Compare Players →</Link>
            <Link href="/cards" style={{
              background: 'transparent',
              border: '1.5px solid var(--border)',
              color: 'var(--ink)',
              padding: '12px 28px', borderRadius: '28px',
              fontFamily: 'DM Sans, sans-serif', fontSize: '15px', fontWeight: 500,
              textDecoration: 'none',
            }}>View Cards</Link>
          </div>
        </div>

        {/* Right column — scrolling card strip */}
        <div style={{ overflow: 'hidden', zIndex: 1 }}>
          <CardStrip players={topPlayers} />
        </div>
      </section>

      {/* Analytics section */}
      <section style={{ background: 'var(--bg-alt)', padding: '80px 64px' }}>
        <h2 style={{
          fontFamily: 'Playfair Display, serif',
          fontSize: '36px', fontWeight: 700,
          color: 'var(--ink)', marginBottom: '8px',
        }}>What TennisIQ shows you</h2>
        <p style={{
          fontFamily: 'DM Sans, sans-serif', fontSize: '15px',
          color: 'var(--ink-mid)', marginBottom: '48px',
        }}>
          Three views. One platform.
        </p>
        <div style={{
          display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
          gap: '24px',
        }}>
          <FeatureCard
            title="Individual Athlete"
            description="FIFA-style player card with serve patterns, rally tendencies, pressure stats, and Elo rating history."
            href="/player/Novak Djokovic"
            tags={['Serve Analysis', 'Patterns', 'Elo Rating']}
            icon="🎾"
          />
          <FeatureCard
            title="Compare Players"
            description="Head-to-head win probability with top 3 prediction factors, pattern exploitation cards, and both FIFA cards side by side."
            href="/compare"
            tags={['Win Probability', 'Head-to-Head', 'Pattern Match']}
            icon="⚔️"
          />
          <FeatureCard
            title="Court Speed & Intel"
            description="Surface pace index, ball type, court-specific Elo, and weather context for every major tournament."
            href="/tournaments"
            tags={['Court Pace', 'Ball Type', 'Surface Elo']}
            icon="🏟️"
          />
        </div>
      </section>
    </div>
  )
}

function AmbientBall({ x, y, size }: { x: number; y: number; size: number }) {
  return (
    <div style={{
      position: 'absolute',
      left: `${x}%`, top: `${y}%`,
      width: size, height: size,
      borderRadius: '50%',
      background: 'radial-gradient(circle, rgba(74,124,111,0.12) 0%, transparent 70%)',
      transform: 'translate(-50%, -50%)',
      pointerEvents: 'none',
    }}/>
  )
}

function CardStrip({ players }: { players: CardData[] }) {
  if (players.length === 0) {
    return (
      <div style={{ color: 'var(--ink-soft)', fontSize: '14px', padding: '40px', textAlign: 'center' }}>
        Connect the database to see player cards
      </div>
    )
  }

  return (
    <div style={{ position: 'relative', overflow: 'hidden' }}>
      <div style={{
        display: 'flex',
        gap: '20px',
        animation: 'scroll-strip 36s linear infinite',
        width: 'max-content',
      }}>
        {[...players, ...players].map((p, i) => (
          <Link key={i} href={`/player/${encodeURIComponent(p.name)}`} style={{ textDecoration: 'none' }}>
            <PlayerCard data={p} />
          </Link>
        ))}
      </div>
      <style>{`
        @keyframes scroll-strip {
          0%   { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        .scroll-strip-wrapper:hover div {
          animation-play-state: paused;
        }
      `}</style>
    </div>
  )
}

function FeatureCard({
  title, description, href, tags, icon
}: {
  title: string; description: string; href: string; tags: string[]; icon: string
}) {
  return (
    <Link href={href} style={{ textDecoration: 'none' }}>
      <div className="card-lift" style={{
        background: 'var(--card-bg)',
        borderRadius: '16px',
        border: '1px solid var(--border)',
        padding: '28px',
        cursor: 'pointer',
        boxShadow: '0 2px 8px rgba(26,38,52,0.06)',
      }}>
        <div style={{ fontSize: '32px', marginBottom: '16px' }}>{icon}</div>
        <h3 style={{
          fontFamily: 'Playfair Display, serif',
          fontSize: '20px', fontWeight: 700,
          color: 'var(--ink)', marginBottom: '12px',
        }}>{title}</h3>
        <p style={{
          fontFamily: 'DM Sans, sans-serif',
          fontSize: '14px', color: 'var(--ink-mid)',
          lineHeight: 1.7, marginBottom: '20px',
        }}>{description}</p>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {tags.map(tag => (
            <span key={tag} style={{
              fontFamily: 'DM Mono, monospace',
              fontSize: '9.5px', textTransform: 'uppercase',
              letterSpacing: '0.08em',
              padding: '3px 8px',
              borderRadius: '12px',
              background: 'rgba(74,124,111,0.1)',
              color: 'var(--accent)',
            }}>{tag}</span>
          ))}
        </div>
      </div>
    </Link>
  )
}
