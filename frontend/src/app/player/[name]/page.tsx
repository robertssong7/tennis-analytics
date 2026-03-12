'use client'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import PlayerCard, { CardData } from '@/components/PlayerCard'
import EloChart from '@/components/EloChart'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

const SURFACES = ['all', 'hard', 'clay', 'grass'] as const
type Surface = typeof SURFACES[number]

export default function PlayerProfile() {
  const params = useParams()
  const name = decodeURIComponent(params.name as string)

  const [surface, setSurface] = useState<Surface>('hard')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/player/${encodeURIComponent(name)}?surface=${surface}`)
      .then(r => r.json())
      .then(d => {
        if (d.detail) throw new Error(d.detail)
        setData(d)
        setLoading(false)
      })
      .catch(e => { setError(e.message); setLoading(false) })
  }, [name, surface])

  if (loading) return <PageShell><div style={{ padding: '80px', textAlign: 'center', color: 'var(--ink-soft)' }}>Loading…</div></PageShell>
  if (error) return <PageShell><div style={{ padding: '80px', textAlign: 'center', color: 'var(--ink-soft)' }}>{error}</div></PageShell>
  if (!data) return null

  const cardData: CardData = {
    name: data.name,
    fifa_rating: data.fifa_rating,
    card_tier: data.card_tier,
    elo_display: data.elo_display,
    elo_hard: data.elo_hard,
    elo_clay: data.elo_clay,
    elo_grass: data.elo_grass,
    elo_peak: data.elo_peak,
    elo_match_count: data.elo_match_count,
    country: data.country,
    card_attributes: data.card_attributes,
    elo_history: data.elo_history,
  }

  const profile = data.profile || {}

  return (
    <PageShell>
      {/* Header */}
      <div style={{
        background: 'linear-gradient(180deg, var(--bg-alt) 0%, var(--bg) 100%)',
        padding: '48px 64px',
        display: 'flex', gap: '48px', alignItems: 'flex-start',
      }}>
        {/* FIFA Card */}
        <PlayerCard data={cardData} />

        {/* Player info */}
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
            {data.card_tier && <span className={`tier-badge ${data.card_tier}`}>{data.card_tier}</span>}
            <span style={{
              fontFamily: 'DM Mono, monospace', fontSize: '10px',
              color: 'var(--ink-soft)', textTransform: 'uppercase',
            }}>
              {data.data_confidence === 'high' ? '✓ Strong Data' :
               data.data_confidence === 'moderate' ? '⚠ Moderate Data' :
               data.data_confidence === 'low' ? '⚠ Limited Data' : 'Insufficient Data'}
              {' · '}{data.elo_match_count || 0} matches
            </span>
          </div>

          <h1 style={{
            fontFamily: 'Playfair Display, serif',
            fontSize: '48px', fontWeight: 700,
            color: 'var(--ink)', lineHeight: 1.1, marginBottom: '16px',
          }}>{data.name}</h1>

          {/* Elo display */}
          <div style={{ display: 'flex', gap: '24px', marginBottom: '24px', alignItems: 'flex-end' }}>
            <div>
              <div style={{
                fontFamily: 'Playfair Display, serif',
                fontSize: '56px', fontWeight: 700,
                color: 'var(--ink)', lineHeight: 1,
              }}>
                {data.elo_display ? Math.round(data.elo_display) : '—'}
              </div>
              <div style={{
                fontFamily: 'DM Mono, monospace', fontSize: '10px',
                textTransform: 'uppercase', letterSpacing: '0.08em',
                color: 'var(--ink-soft)',
              }}>Elo Rating</div>
            </div>
            {data.fifa_rating && (
              <div>
                <div style={{
                  fontFamily: 'Playfair Display, serif', fontSize: '36px',
                  fontWeight: 700, color: 'var(--accent)', lineHeight: 1,
                }}>{data.fifa_rating}</div>
                <div style={{
                  fontFamily: 'DM Mono, monospace', fontSize: '10px',
                  textTransform: 'uppercase', color: 'var(--ink-soft)',
                }}>FIFA</div>
              </div>
            )}
          </div>

          {/* Surface Elo pills */}
          <div style={{ display: 'flex', gap: '10px' }}>
            {[
              { label: 'Hard', val: data.elo_hard, color: '#3A6EA5' },
              { label: 'Clay', val: data.elo_clay, color: '#C4673A' },
              { label: 'Grass', val: data.elo_grass, color: '#5A8A3C' },
            ].map(({ label, val, color }) => (
              <div key={label} style={{
                padding: '6px 14px', borderRadius: '20px',
                border: `1.5px solid ${color}30`,
                background: `${color}10`,
                display: 'flex', gap: '8px', alignItems: 'center',
              }}>
                <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: color }}/>
                <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '11px', color }}>
                  {label}: {val ? Math.round(val) : '—'}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Surface tabs */}
      <div style={{
        padding: '0 64px',
        borderBottom: '1px solid var(--border)',
        display: 'flex', gap: '4px', background: 'var(--bg)',
      }}>
        {SURFACES.map(s => (
          <button
            key={s}
            onClick={() => setSurface(s)}
            style={{
              padding: '12px 20px', border: 'none', cursor: 'pointer',
              background: surface === s ? 'var(--ink)' : 'transparent',
              color: surface === s ? 'var(--bg)' : 'var(--ink-mid)',
              fontFamily: 'DM Sans, sans-serif', fontSize: '14px', fontWeight: 500,
              borderRadius: '8px 8px 0 0',
              borderBottom: surface === s ? '2px solid var(--accent)' : '2px solid transparent',
              textTransform: 'capitalize',
            }}
          >{s}</button>
        ))}
      </div>

      {/* Content */}
      <div style={{ padding: '48px 64px', display: 'flex', flexDirection: 'column', gap: '40px' }}>
        {/* Elo history chart */}
        {data.elo_history && data.elo_history.length > 0 && (
          <Section title="Elo Rating History">
            <EloChart
              history={data.elo_history}
              peak={data.elo_peak}
              playerName={data.name}
            />
          </Section>
        )}

        {/* Stats grid */}
        {profile && Object.keys(profile).length > 0 && (
          <Section title="Serve Analytics">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: '16px' }}>
              {[
                { label: '1st Serve %', val: profile.first_serve_pct, pct: true },
                { label: '1st Serve Won', val: profile.first_serve_won, pct: true },
                { label: '2nd Serve Won', val: profile.second_serve_won, pct: true },
                { label: 'Ace Rate', val: profile.ace_rate, pct: true },
                { label: 'Serve Wide', val: profile.serve_wide_pct, pct: true },
                { label: 'Serve T', val: profile.serve_t_pct, pct: true },
              ].map(({ label, val, pct }) => (
                <StatChip key={label} label={label} value={val} pct={pct} />
              ))}
            </div>
          </Section>
        )}

        {profile && Object.keys(profile).length > 0 && (
          <Section title="Rally & Pressure">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: '16px' }}>
              {[
                { label: 'Avg Rally Length', val: profile.avg_rally_length, pct: false },
                { label: 'Winner Rate', val: profile.winner_rate, pct: true },
                { label: 'UF Error Rate', val: profile.uf_error_rate, pct: true },
                { label: 'BP Save %', val: profile.bp_save_pct, pct: true },
                { label: 'BP Convert %', val: profile.bp_convert_pct, pct: true },
                { label: 'Clutch Delta', val: profile.clutch_delta, pct: true },
              ].map(({ label, val, pct }) => (
                <StatChip key={label} label={label} value={val} pct={pct} />
              ))}
            </div>
          </Section>
        )}

        {/* No data state */}
        {(!profile || Object.keys(profile).length === 0) && (
          <div style={{
            padding: '40px', background: 'var(--bg-alt)',
            borderRadius: '12px', textAlign: 'center',
            color: 'var(--ink-soft)', fontFamily: 'DM Sans, sans-serif', fontSize: '15px',
          }}>
            No profile data available yet for {data.name} on {surface} courts.
            <br/>Run the data pipeline (Phase 1) and feature engine (Phase 3) to populate.
          </div>
        )}
      </div>
    </PageShell>
  )
}

function PageShell({ children }: { children: React.ReactNode }) {
  return <div style={{ minHeight: '100vh' }}>{children}</div>
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 style={{
        fontFamily: 'Playfair Display, serif', fontSize: '22px',
        fontWeight: 700, color: 'var(--ink)', marginBottom: '20px',
      }}>{title}</h2>
      {children}
    </div>
  )
}

function StatChip({ label, value, pct }: { label: string; value?: number | null; pct?: boolean }) {
  const formatted = value != null
    ? pct ? `${(value * 100).toFixed(1)}%` : value.toFixed(1)
    : '—'
  return (
    <div style={{
      background: 'var(--card-bg)',
      border: '1px solid var(--border)',
      borderRadius: '12px', padding: '16px',
    }}>
      <div style={{
        fontFamily: 'Playfair Display, serif',
        fontSize: '24px', fontWeight: 700, color: 'var(--ink)',
      }}>{formatted}</div>
      <div style={{
        fontFamily: 'DM Mono, monospace', fontSize: '9.5px',
        textTransform: 'uppercase', letterSpacing: '0.06em',
        color: 'var(--ink-soft)', marginTop: '4px',
      }}>{label}</div>
    </div>
  )
}
