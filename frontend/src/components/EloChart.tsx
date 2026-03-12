'use client'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, Dot,
} from 'recharts'

interface HistoryPoint {
  match_date: string
  surface: string
  elo_after: number
  elo_before: number
  tournament_level?: string
}

const SURFACE_COLORS: Record<string, string> = {
  hard:  '#3A6EA5',
  clay:  '#C4673A',
  grass: '#5A8A3C',
}

function CustomDot(props: any) {
  const { cx, cy, payload } = props
  const color = SURFACE_COLORS[payload.surface?.toLowerCase()] || '#7A8A96'
  return <circle cx={cx} cy={cy} r={3} fill={color} stroke="#fff" strokeWidth={1} />
}

function CustomTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload as HistoryPoint
  const color = SURFACE_COLORS[p.surface?.toLowerCase()] || '#7A8A96'
  return (
    <div style={{
      background: 'var(--ink)', borderRadius: '8px',
      padding: '10px 14px', boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
    }}>
      <div style={{
        fontFamily: 'DM Mono, monospace', fontSize: '9px',
        color: 'rgba(233,229,220,0.5)', textTransform: 'uppercase',
        marginBottom: '4px',
      }}>
        {p.match_date?.slice(0, 10)}
      </div>
      <div style={{
        fontFamily: 'Playfair Display, serif', fontSize: '20px',
        fontWeight: 700, color: '#fff',
      }}>
        {p.elo_after?.toFixed(0)}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '4px' }}>
        <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: color }}/>
        <span style={{
          fontFamily: 'DM Mono, monospace', fontSize: '9px',
          color: 'rgba(233,229,220,0.6)', textTransform: 'capitalize',
        }}>{p.surface}</span>
        {p.tournament_level && (
          <span style={{
            fontFamily: 'DM Mono, monospace', fontSize: '8px',
            color: 'rgba(233,229,220,0.4)',
          }}>{p.tournament_level.replace('_', ' ')}</span>
        )}
      </div>
    </div>
  )
}

export default function EloChart({
  history,
  peak,
  playerName,
}: {
  history: HistoryPoint[]
  peak?: number | null
  playerName?: string
}) {
  if (!history || history.length === 0) {
    return (
      <div style={{
        height: '200px', display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--ink-soft)', fontFamily: 'DM Sans, sans-serif', fontSize: '14px',
        background: 'var(--bg-alt)', borderRadius: '12px',
      }}>
        No Elo history available
      </div>
    )
  }

  const data = [...history].sort((a, b) =>
    new Date(a.match_date).getTime() - new Date(b.match_date).getTime()
  )

  const vals = data.map(d => d.elo_after)
  const minVal = Math.floor(Math.min(...vals) / 50) * 50 - 50
  const maxVal = Math.ceil(Math.max(...vals) / 50) * 50 + 50

  return (
    <div style={{
      background: 'var(--card-bg)', borderRadius: '12px',
      border: '1px solid var(--border)', padding: '24px',
    }}>
      {/* Surface legend */}
      <div style={{ display: 'flex', gap: '16px', marginBottom: '20px' }}>
        {Object.entries(SURFACE_COLORS).map(([surf, color]) => (
          <div key={surf} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <div style={{ width: '8px', height: '8px', borderRadius: '50%', background: color }}/>
            <span style={{
              fontFamily: 'DM Mono, monospace', fontSize: '9px',
              textTransform: 'capitalize', color: 'var(--ink-mid)',
            }}>{surf}</span>
          </div>
        ))}
        {peak && (
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <div style={{ width: '20px', borderTop: '1.5px dashed var(--warm)' }}/>
            <span style={{
              fontFamily: 'DM Mono, monospace', fontSize: '9px', color: 'var(--warm)',
            }}>Career Peak: {peak.toFixed(0)}</span>
          </div>
        )}
      </div>

      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: 8 }}>
          <CartesianGrid
            strokeDasharray="3 3"
            stroke="rgba(26,38,52,0.06)"
            vertical={false}
          />
          <XAxis
            dataKey="match_date"
            tickFormatter={v => v?.slice(0, 4)}
            tick={{ fontFamily: 'DM Mono, monospace', fontSize: 10, fill: 'var(--ink-soft)' }}
            axisLine={false} tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            domain={[minVal, maxVal]}
            tick={{ fontFamily: 'DM Mono, monospace', fontSize: 10, fill: 'var(--ink-soft)' }}
            axisLine={false} tickLine={false}
            width={45}
          />
          <Tooltip content={<CustomTooltip />} />
          {peak && (
            <ReferenceLine
              y={peak}
              stroke="var(--warm)"
              strokeDasharray="4 4"
              strokeWidth={1.5}
            />
          )}
          <Line
            type="monotone"
            dataKey="elo_after"
            stroke="var(--accent)"
            strokeWidth={2}
            dot={<CustomDot />}
            activeDot={{ r: 5, stroke: '#fff', strokeWidth: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
