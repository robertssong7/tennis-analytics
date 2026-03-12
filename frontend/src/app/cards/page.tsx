'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'
import PlayerCard, { CardData } from '@/components/PlayerCard'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function CardsPage() {
  const [players, setPlayers] = useState<CardData[]>([])
  const [loading, setLoading] = useState(true)
  const [tier, setTier] = useState('')
  const [surface, setSurface] = useState('')
  const [sort, setSort] = useState('fifa_rating')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)

  useEffect(() => {
    setLoading(true)
    const params = new URLSearchParams({ sort, page: String(page), page_size: '50' })
    if (tier) params.set('tier', tier)
    if (surface) params.set('surface', surface)

    fetch(`${API}/cards?${params}`)
      .then(r => r.json())
      .then(d => {
        setPlayers(d.players || [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [tier, surface, sort, page])

  const filtered = players.filter(p =>
    !search || p.name.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div style={{ padding: '48px 64px', maxWidth: '1400px', margin: '0 auto' }}>
      <h1 style={{
        fontFamily: 'Playfair Display, serif',
        fontSize: '40px', fontWeight: 700,
        color: 'var(--ink)', marginBottom: '8px',
      }}>Player Cards</h1>
      <p style={{
        fontFamily: 'DM Sans, sans-serif', fontSize: '15px',
        color: 'var(--ink-mid)', marginBottom: '40px',
      }}>
        FIFA-style cards ranked by Elo rating. Hover to flip — see Elo history and career peak.
      </p>

      {/* Filter bar */}
      <div style={{
        display: 'flex', gap: '12px', alignItems: 'center',
        marginBottom: '40px', flexWrap: 'wrap',
      }}>
        {/* Search */}
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search players…"
          style={{
            padding: '8px 16px',
            border: '1.5px solid var(--border)',
            borderRadius: '24px',
            fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
            background: 'var(--card-bg)', color: 'var(--ink)',
            outline: 'none', width: '200px',
          }}
        />

        {/* Surface tabs */}
        <PillGroup
          options={[
            { value: '', label: 'All Surfaces' },
            { value: 'hard', label: 'Hard' },
            { value: 'clay', label: 'Clay' },
            { value: 'grass', label: 'Grass' },
          ]}
          value={surface}
          onChange={setSurface}
        />

        {/* Tier filter */}
        <PillGroup
          options={[
            { value: '', label: 'All Tiers' },
            { value: 'legendary', label: '⚡ Legendary' },
            { value: 'gold', label: '🥇 Gold' },
            { value: 'silver', label: '🥈 Silver' },
            { value: 'bronze', label: '🥉 Bronze' },
          ]}
          value={tier}
          onChange={setTier}
        />

        {/* Sort */}
        <select
          value={sort}
          onChange={e => setSort(e.target.value)}
          style={{
            padding: '8px 16px',
            border: '1.5px solid var(--border)',
            borderRadius: '24px',
            fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
            background: 'var(--card-bg)', color: 'var(--ink)', cursor: 'pointer',
          }}
        >
          <option value="fifa_rating">Sort: FIFA Rating</option>
          <option value="elo">Sort: Elo</option>
          <option value="name">Sort: Name</option>
          <option value="recent">Sort: Recent</option>
        </select>
      </div>

      {/* Cards grid */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: '80px', color: 'var(--ink-soft)' }}>
          Loading cards…
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '80px', color: 'var(--ink-soft)' }}>
          {players.length === 0
            ? 'No cards yet — run the data pipeline and Elo engine first.'
            : 'No players match your search.'}
        </div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
          gap: '24px',
        }}>
          {filtered.map((p, i) => (
            <Link
              key={i}
              href={`/player/${encodeURIComponent(p.name)}`}
              style={{ textDecoration: 'none', display: 'flex', justifyContent: 'center' }}
            >
              <PlayerCard data={p} />
            </Link>
          ))}
        </div>
      )}

      {/* Pagination */}
      {filtered.length > 0 && (
        <div style={{ display: 'flex', justifyContent: 'center', gap: '12px', marginTop: '48px' }}>
          <button
            onClick={() => setPage(p => Math.max(1, p - 1))}
            disabled={page === 1}
            style={{
              padding: '8px 20px', borderRadius: '20px',
              border: '1.5px solid var(--border)',
              background: page === 1 ? 'transparent' : 'var(--ink)',
              color: page === 1 ? 'var(--ink-soft)' : 'var(--bg)',
              cursor: page === 1 ? 'default' : 'pointer',
              fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
            }}
          >← Prev</button>
          <span style={{ padding: '8px 16px', color: 'var(--ink-mid)', fontSize: '14px' }}>
            Page {page}
          </span>
          <button
            onClick={() => setPage(p => p + 1)}
            disabled={filtered.length < 50}
            style={{
              padding: '8px 20px', borderRadius: '20px',
              border: '1.5px solid var(--border)',
              background: filtered.length < 50 ? 'transparent' : 'var(--ink)',
              color: filtered.length < 50 ? 'var(--ink-soft)' : 'var(--bg)',
              cursor: filtered.length < 50 ? 'default' : 'pointer',
              fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
            }}
          >Next →</button>
        </div>
      )}
    </div>
  )
}

function PillGroup({
  options, value, onChange,
}: {
  options: { value: string; label: string }[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div style={{
      display: 'flex', gap: '4px',
      background: 'rgba(26,38,52,0.06)',
      borderRadius: '24px', padding: '3px',
    }}>
      {options.map(opt => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          style={{
            padding: '5px 14px', borderRadius: '20px', border: 'none',
            background: value === opt.value ? 'var(--ink)' : 'transparent',
            color: value === opt.value ? 'var(--bg)' : 'var(--ink-mid)',
            fontFamily: 'DM Sans, sans-serif', fontSize: '13px', fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
