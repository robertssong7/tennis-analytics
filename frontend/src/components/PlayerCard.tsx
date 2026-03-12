'use client'
import { useState } from 'react'

export interface CardData {
  name: string
  fifa_rating?: number | null
  card_tier?: string | null
  elo_display?: number | null
  elo_hard?: number | null
  elo_clay?: number | null
  elo_grass?: number | null
  elo_peak?: number | null
  elo_match_count?: number | null
  country?: string
  best_surface?: string
  card_attributes?: {
    srv: number; ret: number; pat: number
    spd: number; hrd: number; cly: number
  }
  elo_history?: Array<{ elo_after: number; match_date: string; surface: string }>
}

const TIER_STYLES: Record<string, {
  bg: string; border: string; text: string; attrBg: string
}> = {
  legendary: {
    bg:     'linear-gradient(135deg, #1A1A2E 0%, #16213E 50%, #0F3460 100%)',
    border: '2px solid #E94560',
    text:   '#fff',
    attrBg: 'rgba(255,255,255,0.07)',
  },
  gold: {
    bg:     'linear-gradient(135deg, #FFD700 0%, #FFC200 100%)',
    border: '2px solid #B8860B',
    text:   '#4A3000',
    attrBg: 'rgba(0,0,0,0.08)',
  },
  silver: {
    bg:     'linear-gradient(135deg, #D4D4D4 0%, #B0B0B0 100%)',
    border: '2px solid #808080',
    text:   '#2A2A2A',
    attrBg: 'rgba(0,0,0,0.08)',
  },
  bronze: {
    bg:     'linear-gradient(135deg, #CD7F32 0%, #A05C20 100%)',
    border: '2px solid #8B4513',
    text:   '#fff',
    attrBg: 'rgba(0,0,0,0.12)',
  },
  unrated: {
    bg:     'linear-gradient(135deg, #3D4F5E 0%, #1A2634 100%)',
    border: '2px solid rgba(255,255,255,0.1)',
    text:   'rgba(255,255,255,0.7)',
    attrBg: 'rgba(255,255,255,0.05)',
  },
}

const SURFACE_COLORS: Record<string, string> = {
  hard:  '#3A6EA5',
  clay:  '#C4673A',
  grass: '#5A8A3C',
}

const ATTR_LABELS = [
  { key: 'srv', label: 'SRV', title: 'Serve' },
  { key: 'ret', label: 'RET', title: 'Return' },
  { key: 'pat', label: 'PAT', title: 'Patterns' },
  { key: 'spd', label: 'SPD', title: 'Speed' },
  { key: 'hrd', label: 'HRD', title: 'Hard Ct' },
  { key: 'cly', label: 'CLY', title: 'Clay Ct' },
]

function getInitials(name: string): string {
  return name.split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase()
}

function MiniBar({ value, color }: { value: number; color: string }) {
  return (
    <div style={{
      width: '40px', height: '3px',
      background: 'rgba(255,255,255,0.15)',
      borderRadius: '2px', overflow: 'hidden',
    }}>
      <div style={{
        width: `${(value / 99) * 100}%`,
        height: '100%',
        background: color,
        borderRadius: '2px',
      }} />
    </div>
  )
}

function SparkLine({ history }: { history?: Array<{ elo_after: number }> }) {
  if (!history || history.length < 2) {
    return <div style={{ color: 'rgba(255,255,255,0.4)', fontSize: '11px', textAlign: 'center', paddingTop: '20px' }}>
      No history yet
    </div>
  }
  const vals = history.slice(-20).map(h => h.elo_after)
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const range = max - min || 1
  const W = 160, H = 50
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * W
    const y = H - ((v - min) / range) * H
    return `${x},${y}`
  }).join(' ')

  return (
    <svg width={W} height={H} style={{ overflow: 'visible' }}>
      <polyline points={pts} fill="none" stroke="#6FAF9E" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

export default function PlayerCard({
  data,
  flipped: defaultFlipped = false,
  mini = false,
  mirrorX = false,
}: {
  data: CardData
  flipped?: boolean
  mini?: boolean
  mirrorX?: boolean
}) {
  const [isFlipped, setIsFlipped] = useState(defaultFlipped)

  const tier = data.card_tier || 'unrated'
  const style = TIER_STYLES[tier] || TIER_STYLES.unrated
  const attrs = data.card_attributes || { srv: 50, ret: 50, pat: 50, spd: 50, hrd: 50, cly: 50 }
  const fifa  = data.fifa_rating ?? null
  const bestSurf = data.best_surface || 'hard'

  const W = mini ? 120 : 200
  const H = mini ? 192 : 320

  const isLegendary = tier === 'legendary'

  return (
    <div
      onClick={() => !defaultFlipped && setIsFlipped(f => !f)}
      style={{
        width: W, height: H,
        perspective: '1000px',
        cursor: 'pointer',
        flexShrink: 0,
        transform: mirrorX ? 'scaleX(-1)' : undefined,
      }}
    >
      <div style={{
        width: '100%', height: '100%',
        position: 'relative',
        transformStyle: 'preserve-3d',
        transform: isFlipped ? 'rotateY(180deg)' : 'rotateY(0)',
        transition: 'transform 0.4s ease',
      }}>
        {/* FRONT */}
        <div style={{
          position: 'absolute', inset: 0,
          backfaceVisibility: 'hidden',
          WebkitBackfaceVisibility: 'hidden',
          borderRadius: '12px',
          background: style.bg,
          border: style.border,
          boxShadow: isLegendary
            ? '0 8px 32px rgba(0,0,0,0.4), 0 0 20px rgba(233,69,96,0.3)'
            : '0 8px 32px rgba(0,0,0,0.25)',
          overflow: 'hidden',
          display: 'flex', flexDirection: 'column',
          userSelect: 'none',
        }}>
          {/* Legendary shimmer */}
          {isLegendary && <LegendaryShimmer />}

          {/* Tier banner */}
          <div style={{
            height: mini ? '24px' : '30px',
            padding: '0 12px',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <div>
              {fifa !== null && (
                <span style={{
                  fontFamily: 'Playfair Display, serif',
                  fontSize: mini ? '22px' : '36px',
                  fontWeight: 700, color: style.text, lineHeight: 1,
                }}>
                  {mini ? '' : fifa}
                </span>
              )}
            </div>
            {!mini && (
              <div style={{ textAlign: 'right' }}>
                <div style={{
                  fontFamily: 'DM Mono, monospace',
                  fontSize: '9px', textTransform: 'uppercase',
                  color: `${style.text}B3`, letterSpacing: '0.05em',
                }}>
                  {tier}
                </div>
                <div style={{
                  width: '8px', height: '8px',
                  borderRadius: '2px',
                  background: SURFACE_COLORS[bestSurf] || SURFACE_COLORS.hard,
                  marginLeft: 'auto', marginTop: '2px',
                }} />
              </div>
            )}
          </div>

          {/* Avatar area */}
          <div style={{
            height: mini ? '60px' : '120px',
            background: 'rgba(0,0,0,0.25)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>
            <span style={{
              fontFamily: 'Playfair Display, serif',
              fontSize: mini ? '28px' : '48px',
              color: style.text,
              opacity: 0.9,
            }}>
              {getInitials(data.name)}
            </span>
          </div>

          {/* Name bar */}
          {!mini && (
            <div style={{
              height: '32px', padding: '0 8px',
              display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center',
              background: 'rgba(0,0,0,0.1)',
            }}>
              <div style={{
                fontFamily: 'DM Sans, sans-serif',
                fontSize: '13px', fontWeight: 500,
                color: style.text, textAlign: 'center',
                whiteSpace: 'nowrap', overflow: 'hidden',
                textOverflow: 'ellipsis', maxWidth: '180px',
              }}>
                {data.name}
              </div>
            </div>
          )}

          {/* Attribute grid */}
          {!mini && (
            <div style={{
              flex: 1, padding: '10px 12px',
              background: style.attrBg,
              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px',
            }}>
              {ATTR_LABELS.map(({ key, label }) => {
                const val = (attrs as any)[key] ?? 50
                return (
                  <div key={key} style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                      <span style={{
                        fontFamily: 'Playfair Display, serif',
                        fontSize: '18px', fontWeight: 700,
                        color: style.text, lineHeight: 1,
                      }}>{val}</span>
                      <span style={{
                        fontFamily: 'DM Mono, monospace',
                        fontSize: '7px', textTransform: 'uppercase',
                        color: `${style.text}80`, letterSpacing: '0.05em',
                      }}>{label}</span>
                    </div>
                    <MiniBar value={val} color={`${style.text}60`} />
                  </div>
                )
              })}
            </div>
          )}

          {/* Mini name */}
          {mini && (
            <div style={{
              flex: 1, padding: '4px 6px',
              display: 'flex', flexDirection: 'column', justifyContent: 'center',
            }}>
              <div style={{
                fontFamily: 'DM Sans, sans-serif', fontSize: '9px',
                fontWeight: 500, color: style.text, textAlign: 'center',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {data.name.split(' ').slice(-1)[0]}
              </div>
              {fifa !== null && (
                <div style={{
                  fontFamily: 'DM Mono, monospace', fontSize: '8px',
                  color: `${style.text}80`, textAlign: 'center',
                }}>{fifa}</div>
              )}
            </div>
          )}
        </div>

        {/* BACK */}
        <div style={{
          position: 'absolute', inset: 0,
          backfaceVisibility: 'hidden',
          WebkitBackfaceVisibility: 'hidden',
          transform: 'rotateY(180deg)',
          borderRadius: '12px',
          background: '#1A2634',
          border: style.border,
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
          padding: '16px 12px',
          display: 'flex', flexDirection: 'column', gap: '12px',
        }}>
          <div style={{
            fontFamily: 'DM Sans, sans-serif', fontSize: '11px',
            fontWeight: 500, color: 'rgba(255,255,255,0.8)', textAlign: 'center',
          }}>
            {data.name}
          </div>

          {/* Elo sparkline */}
          <div>
            <div style={{
              fontFamily: 'DM Mono, monospace', fontSize: '8px',
              textTransform: 'uppercase', color: 'rgba(255,255,255,0.4)',
              marginBottom: '6px', letterSpacing: '0.08em',
            }}>Elo History</div>
            <SparkLine history={data.elo_history} />
          </div>

          {/* Peak Elo */}
          {data.elo_peak && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '8px', color: 'rgba(255,255,255,0.4)', textTransform: 'uppercase' }}>
                Career Peak
              </span>
              <span style={{ fontFamily: 'Playfair Display, serif', fontSize: '14px', fontWeight: 700, color: '#6FAF9E' }}>
                {data.elo_peak.toFixed(0)}
              </span>
            </div>
          )}

          {/* Match count */}
          {data.elo_match_count != null && (
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '8px', color: 'rgba(255,255,255,0.4)', textTransform: 'uppercase' }}>
                Matches Rated
              </span>
              <span style={{ fontFamily: 'DM Mono, monospace', fontSize: '11px', color: 'rgba(255,255,255,0.7)' }}>
                {data.elo_match_count}
              </span>
            </div>
          )}

          {/* Surface Elos */}
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {[
              { label: 'H', val: data.elo_hard, color: '#3A6EA5' },
              { label: 'C', val: data.elo_clay, color: '#C4673A' },
              { label: 'G', val: data.elo_grass, color: '#5A8A3C' },
            ].map(({ label, val, color }) => val ? (
              <div key={label} style={{
                flex: 1, textAlign: 'center',
                background: 'rgba(255,255,255,0.06)',
                borderRadius: '6px', padding: '4px',
              }}>
                <div style={{ fontFamily: 'DM Mono, monospace', fontSize: '7px', color: 'rgba(255,255,255,0.4)', textTransform: 'uppercase' }}>{label}</div>
                <div style={{ fontFamily: 'Playfair Display, serif', fontSize: '12px', fontWeight: 700, color }}>{val.toFixed(0)}</div>
              </div>
            ) : null)}
          </div>
        </div>
      </div>
    </div>
  )
}

function LegendaryShimmer() {
  return (
    <div style={{
      position: 'absolute', inset: 0, zIndex: 1,
      background: 'linear-gradient(105deg, transparent 40%, rgba(255,255,255,0.08) 50%, transparent 60%)',
      backgroundSize: '200% 200%',
      animation: 'shimmer 3s infinite linear',
      pointerEvents: 'none',
    }}>
      <style>{`
        @keyframes shimmer {
          0%   { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </div>
  )
}
