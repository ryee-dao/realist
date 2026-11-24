import { site } from '../site'

export default function About() {
  return (
    <>
      <div className="page-head"><div><h1>About</h1></div></div>
      <section className="card" style={{ maxWidth: '62ch' }}>
        <p style={{ marginTop: 0 }}>
          Built by <a href={site.authorUrl} target="_blank" rel="noreferrer">{site.author}</a>, a homebuyer who started this to research buying a property.
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>The data comes from public records. This is research, not advice.</p>
      </section>
    </>
  )
}
