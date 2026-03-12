'use client'
import { useState } from 'react'
import PlayerCard, { CardData } from '@/components/PlayerCard'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

const SURFACES = ['hard', 'clay', 'grass'] as const

export default function ComparePage() {
  const [p1Name, setP1Name] = useState('')
  const [p2Name, setP2Name] = useState('')
  const [surface, setSurface] = useState<string>('hard')
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const compare = async () => {
    if (!p1Name.trim() || !p2Name.trim()) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const res = await fetch(
        `${API}/matchup?p1=${encodeURIComponent(p1Name)}&p2=${encodeURIComponent(p2Name)}&surface=${surface}`
      )
      const data = await res.json()
      if (data.detail) throw new Error(data.detail)
      setResult(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '48px 40px' }}>
      <h1 style={{
        fontFamily: 'Playfair Display, serif', fontSize: '40px',
        fontWeight: 700, color: 'var(--ink)', marginBottom: '8px',
      }}>Compare Players</h1>
      <p style={{
        fontFamily: 'DM Sans, sans-serif', fontSize: '15px',
        color: 'var(--ink-mid)', marginBottom: '40px',
      }}>
        Win probability, top prediction factors, head-to-head record, and both FIFA cards.
      </p>

      {/* Input row */}
      <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-end', marginBottom: '40px', flexWrap: 'wrap' }}>
        <PlayerInput label="Player A" value={p1Name} onChange={setP1Name} accentColor="var(--accent)" />
        <div style={{ fontFamily: 'Playfair Display, serif', fontSize: '24px', color: 'var(--ink-soft)', paddingBottom: '8px' }}>vs</div>
        <PlayerInput label="Player B" value={p2Name} onChange={setP2Name} accentColor="var(--warm)" />

        {/* Surface selector */}
        <div>
          <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--ink-soft)', marginBottom: '6px' }}>Surface</div>
          <div style={{ display: 'flex', gap: '4px' }}>
            {SURFACES.map(s => (
              <button
                key={s}
                onClick={() => setSurface(s)}
                style={{
                  padding: '9px 16px', border: 'none', borderRadius: '20px', cursor: 'pointer',
                  background: surface === s ? 'var(--ink)' : 'rgba(26,38,52,0.06)',
                  color: surface === s ? 'var(--bg)' : 'var(--ink-mid)',
                  fontFamily: 'DM Sans, sans-serif', fontSize: '13px', fontWeight: 500,
                  textTransform: 'capitalize',
                }}
              >{s}</button>
            ))}
          </div>
        </div>

        <button
          onClick={compare}
          disabled={loading || !p1Name || !p2Name}
          style={{
            padding: '10px 28px', borderRadius: '28px', border: 'none',
            background: 'var(--ink)', color: 'var(--bg)',
            fontFamily: 'DM Sans, sans-serif', fontSize: '15px', fontWeight: 500,
            cursor: loading || !p1Name || !p2Name ? 'not-allowed' : 'pointer',
            opacity: loading || !p1Name || !p2Name ? 0.5 : 1,
          }}
        >
          {loading ? 'Loading…' : 'Compare →'}
        </button>
      </div>

      {error && (
        <div style={{
          padding: '16px 20px', borderRadius: '10px',
          background: 'rgba(196,103,58,0.1)', border: '1px solid var(--surface-clay)',
          color: 'var(--surface-clay)', fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
          marginBottom: '32px',
        }}>{error}</div>
      )}

      {result && <MatchupResult data={result} surface={surface} />}
    </div>
  )
}

function PlayerInput({
  label, value, onChange, accentColor
}: {
  label: string; value: string; onChange: (v: string) => void; accentColor: string
}) {
  return (
    <div>
      <div style={{
        fontFamily: 'DM Mono, monospace', fontSize: '9px',
        textTransform: 'uppercase', letterSpacing: '0.08em',
        color: 'var(--ink-soft)', marginBottom: '6px',
      }}>{label}</div>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="Player name…"
        style={{
          padding: '9px 16px', width: '220px',
          border: `1.5px solid ${value ? accentColor : 'var(--border)'}`,
          borderRadius: '24px',
          fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
          background: 'var(--card-bg)', color: 'var(--ink)', outline: 'none',
        }}
      />
    </div>
  )
}

function MatchupResult({ data, surface }: { data: any; surface: string }) {
  const p1 = data.p1
  const p2 = data.p2
  const prob = data.win_probability
  const h2h  = data.head_to_head_record

  const p1Card: CardData = {
    name: p1.name,
    fifa_rating: p1.fifa_rating,
    card_tier: p1.card_tier,
    elo_display: p1.elo_display,
    elo_hard: p1.elo_hard, elo_clay: p1.elo_clay, elo_grass: p1.elo_grass,
    elo_peak: p1.elo_peak, elo_match_count: p1.elo_match_count,
    card_attributes: p1.card_attributes,
  }
  const p2Card: CardData = {
    name: p2.name,
    fifa_rating: p2.fifa_rating,
    card_tier: p2.card_tier,
    elo_display: p2.elo_display,
    elo_hard: p2.elo_hard, elo_clay: p2.elo_clay, elo_grass: p2.elo_grass,
    elo_peak: p2.elo_peak, elo_match_count: p2.elo_match_count,
    card_attributes: p2.card_attributes,
  }

  const p1Pct = Math.round((prob.p1_win_prob || 0) * 100)
  const p2Pct = 100 - p1Pct

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '40px' }}>
      {/* Cards + Elo comparison */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '32px', flexWrap: 'wrap' }}>
        <PlayerCard data={p1Card} />

        {/* Elo comparison bar */}
        <div style={{ flex: 1, minWidth: '200px' }}>
          <EloBar p1={p1} p2={p2} surface={surface} />
        </div>

        <PlayerCard data={p2Card} mirrorX />
      </div>

      {/* Win probability bar */}
      <div>
        <div style={{
          fontFamily: 'DM Mono, monospace', fontSize: '9px',
          textTransform: 'uppercase', letterSpacing: '0.08em',
          color: 'var(--ink-soft)', marginBottom: '12px',
        }}>Win Probability ({surface})</div>
        <div style={{
          height: '48px', borderRadius: '8px', overflow: 'hidden',
          display: 'flex', position: 'relative',
        }}>
          {/* P1 */}
          <div style={{
            width: `${p1Pct}%`, background: 'var(--accent)',
            display: 'flex', alignItems: 'center', justifyContent: 'flex-start',
            padding: '0 16px',
          }}>
            <span style={{
              fontFamily: 'Playfair Display, serif', fontSize: '20px',
              fontWeight: 700, color: '#fff',
            }}>{p1Pct}%</span>
          </div>
          {/* P2 */}
          <div style={{
            width: `${p2Pct}%`, background: 'var(--warm)',
            display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
            padding: '0 16px',
          }}>
            <span style={{
              fontFamily: 'Playfair Display, serif', fontSize: '20px',
              fontWeight: 700, color: '#fff',
            }}>{p2Pct}%</span>
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '8px' }}>
          <span style={{ fontFamily: 'DM Sans, sans-serif', fontSize: '13px', color: 'var(--ink-mid)', fontWeight: 500 }}>{p1.name}</span>
          <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '10px', color: 'var(--ink-soft)' }}>
            CI: {Math.round((prob.ci_lower || 0) * 100)}–{Math.round((prob.ci_upper || 0) * 100)}%
          </span>
          <span style={{ fontFamily: 'DM Sans, sans-serif', fontSize: '13px', color: 'var(--ink-mid)', fontWeight: 500 }}>{p2.name}</span>
        </div>
      </div>

      {/* Why this prediction */}
      {data.top_3_factors?.length > 0 && (
        <div>
          <h3 style={{
            fontFamily: 'Playfair Display, serif', fontSize: '20px',
            fontWeight: 700, color: 'var(--ink)', marginBottom: '16px',
          }}>Why this prediction</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
            {data.top_3_factors.map((f: any, i: number) => (
              <div key={i} style={{
                padding: '20px', background: 'var(--card-bg)',
                border: '1px solid var(--border)', borderRadius: '12px',
              }}>
                <div style={{ fontSize: '24px', marginBottom: '8px' }}>{f.icon}</div>
                <div style={{
                  fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
                  fontWeight: 500, color: 'var(--ink)', marginBottom: '4px',
                }}>{f.factor}</div>
                <div style={{
                  fontFamily: 'DM Mono, monospace', fontSize: '10px',
                  color: 'var(--accent)', textTransform: 'uppercase',
                }}>Favors {f.favors}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* H2H */}
      {h2h && h2h.total > 0 && (
        <div>
          <h3 style={{
            fontFamily: 'Playfair Display, serif', fontSize: '20px',
            fontWeight: 700, color: 'var(--ink)', marginBottom: '16px',
          }}>
            Head-to-Head — {h2h.p1_wins}–{h2h.p2_wins} ({h2h.total} matches)
          </h3>
          <H2HTimeline matches={h2h.matches} p1Id={p1.player_id} p2Id={p2.player_id}
            p1Name={p1.name} p2Name={p2.name} />
        </div>
      )}
    </div>
  )
}

function EloBar({ p1, p2, surface }: { p1: any; p2: any; surface: string }) {
  const surfKey = `elo_${surface}`
  const r1 = p1[surfKey] || p1.elo_display || 1500
  const r2 = p2[surfKey] || p2.elo_display || 1500
  const max = Math.max(r1, r2, 2200)
  const min = Math.min(r1, r2, 1200)

  return (
    <div>
      <div style={{
        fontFamily: 'DM Mono, monospace', fontSize: '9px',
        textTransform: 'uppercase', letterSpacing: '0.08em',
        color: 'var(--ink-soft)', marginBottom: '12px', textAlign: 'center',
      }}>Elo Comparison ({surface})</div>

      {[
        { label: p1.name, val: r1, color: 'var(--accent)' },
        { label: p2.name, val: r2, color: 'var(--warm)' },
      ].map(({ label, val, color }) => (
        <div key={label} style={{ marginBottom: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
            <span style={{ fontFamily: 'DM Sans, sans-serif', fontSize: '12px', color: 'var(--ink-mid)', fontWeight: 500 }}>
              {label.split(' ').slice(-1)[0]}
            </span>
            <span style={{ fontFamily: 'Playfair Display, serif', fontSize: '16px', fontWeight: 700, color }}>
              {Math.round(val)}
            </span>
          </div>
          <div style={{ height: '6px', background: 'rgba(26,38,52,0.08)', borderRadius: '3px', overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: '3px', background: color,
              width: `${((val - min) / (max - min + 1)) * 100}%`,
            }}/>
          </div>
        </div>
      ))}
    </div>
  )
}

function H2HTimeline({
  matches, p1Id, p2Id, p1Name, p2Name
}: {
  matches: any[]; p1Id: number; p2Id: number; p1Name: string; p2Name: string
}) {
  const SURF_COLORS: Record<string, string> = {
    hard: '#3A6EA5', clay: '#C4673A', grass: '#5A8A3C'
  }

  return (
    <div style={{
      background: 'var(--card-bg)', border: '1px solid var(--border)',
      borderRadius: '12px', padding: '20px',
    }}>
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
        {matches.slice(0, 30).map((m: any, i: number) => {
          const p1Won = m.winner_id === p1Id
          const surf = (m.surface || 'hard').toLowerCase()
          const color = p1Won ? 'var(--accent)' : 'var(--warm)'
          const surfColor = SURF_COLORS[surf] || '#7A8A96'
          return (
            <div
              key={i}
              title={`${m.tournament || ''} ${m.match_date?.slice(0, 10) || ''} | ${p1Won ? p1Name : p2Name} won | ${m.score || ''}`}
              style={{
                width: '16px', height: '16px', borderRadius: '50%',
                background: color,
                border: `2px solid ${surfColor}`,
                cursor: 'default',
              }}
            />
          )
        })}
      </div>
      <div style={{ display: 'flex', gap: '20px', marginTop: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: 'var(--accent)' }}/>
          <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '9px', color: 'var(--ink-soft)' }}>{p1Name.split(' ').slice(-1)[0]} wins</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: 'var(--warm)' }}/>
          <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '9px', color: 'var(--ink-soft)' }}>{p2Name.split(' ').slice(-1)[0]} wins</span>
        </div>
        <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '9px', color: 'var(--ink-soft)', marginLeft: 'auto' }}>
          Dot border = surface
        </span>
      </div>
    </div>
  )
}
