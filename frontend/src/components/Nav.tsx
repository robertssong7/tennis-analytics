'use client'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState, useRef, useEffect } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function Nav() {
  const router = useRouter()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [focused, setFocused] = useState(false)
  const debounceRef = useRef<NodeJS.Timeout>()

  useEffect(() => {
    if (query.length < 2) { setResults([]); return }
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await fetch(`${API}/players/search?q=${encodeURIComponent(query)}`)
        const data = await res.json()
        setResults(data.results || [])
      } catch { setResults([]) }
    }, 200)
  }, [query])

  return (
    <nav style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
      height: '68px',
      background: 'rgba(233,229,220,0.85)',
      backdropFilter: 'blur(14px)',
      WebkitBackdropFilter: 'blur(14px)',
      borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center',
      padding: '0 32px', gap: '24px',
    }}>
      {/* Logo */}
      <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: '8px', textDecoration: 'none' }}>
        <TennisBall />
        <span style={{
          fontFamily: 'Playfair Display, serif',
          fontSize: '17px', fontWeight: 700,
          color: 'var(--ink)', letterSpacing: '-0.02em',
        }}>TennisIQ</span>
      </Link>

      {/* Search */}
      <div style={{ flex: 1, maxWidth: '340px', position: 'relative' }}>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setTimeout(() => setFocused(false), 150)}
          placeholder="Search players…"
          style={{
            width: '100%',
            padding: '8px 16px',
            border: `1.5px solid ${focused ? 'var(--accent)' : 'var(--border)'}`,
            borderRadius: '24px',
            background: focused ? 'var(--card-bg)' : 'rgba(255,255,255,0.5)',
            fontFamily: 'DM Sans, sans-serif',
            fontSize: '14px',
            color: 'var(--ink)',
            outline: 'none',
            boxShadow: focused ? '0 0 0 3px rgba(74,124,111,0.15)' : 'none',
          }}
        />
        {focused && results.length > 0 && (
          <div style={{
            position: 'absolute', top: '42px', left: 0, right: 0,
            background: 'var(--card-bg)',
            border: '1px solid var(--border)',
            borderRadius: '12px',
            boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
            overflow: 'hidden', zIndex: 200,
          }}>
            {results.map((p: any) => (
              <div
                key={p.player_id}
                onClick={() => { router.push(`/player/${encodeURIComponent(p.name)}`); setQuery('') }}
                style={{
                  padding: '10px 16px', cursor: 'pointer',
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  borderBottom: '1px solid var(--border)',
                  fontSize: '14px',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-alt)')}
                onMouseLeave={e => (e.currentTarget.style.background = '')}
              >
                <span style={{ fontWeight: 500 }}>{p.name}</span>
                {p.card_tier && <span className={`tier-badge ${p.card_tier}`}>{p.card_tier}</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Links */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '20px', marginLeft: 'auto' }}>
        {[
          ['Compare Players', '/compare'],
          ['Tournaments', '/tournaments'],
          ['Cards', '/cards'],
          ['About', '/about'],
        ].map(([label, href]) => (
          <Link key={href} href={href} style={{
            fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
            color: 'var(--ink-mid)', textDecoration: 'none', fontWeight: 500,
          }}>{label}</Link>
        ))}
        <Link href="/compare" style={{
          background: 'var(--ink)', color: 'var(--bg)',
          padding: '7px 18px', borderRadius: '24px',
          fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
          fontWeight: 500, textDecoration: 'none',
        }}>Explore →</Link>
      </div>
    </nav>
  )
}

function TennisBall() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28">
      <circle cx="14" cy="14" r="13" fill="var(--accent)" opacity="0.15" stroke="var(--accent)" strokeWidth="1.5"/>
      <path d="M6 8 Q14 12 22 8" stroke="var(--accent)" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
      <path d="M6 20 Q14 16 22 20" stroke="var(--accent)" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
    </svg>
  )
}
