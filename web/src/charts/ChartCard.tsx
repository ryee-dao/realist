import { ReactNode, useState } from 'react'

interface Props {
  title: ReactNode
  subtitle?: string
  children: ReactNode
  /** Rows for the "view as table" toggle: the same numbers the chart draws. */
  table?: { head: string[]; rows: (string | number)[][] }
  className?: string
}

export default function ChartCard({ title, subtitle, children, table, className = '' }: Props) {
  const [asTable, setAsTable] = useState(false)
  return (
    <section className={`card ${className}`}>
      <header className="card-head">
        <div>
          <h3>{title}</h3>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {table && (
          <button className="ghost small" onClick={() => setAsTable(!asTable)} aria-pressed={asTable}>
            {asTable ? 'Show chart' : 'Show table'}
          </button>
        )}
      </header>
      {asTable && table ? (
        <div className="table-scroll" style={{ maxHeight: 300 }}>
          <table className="data">
            <thead><tr>{table.head.map((h, i) => <th key={h} className={i ? 'num' : ''}>{h}</th>)}</tr></thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i}>{r.map((c, j) => <td key={j} className={j ? 'num' : ''}>{c}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : children}
    </section>
  )
}
