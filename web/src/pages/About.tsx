import { site } from '../site'

export default function About() {
  return (
    <>
      <div className="page-head"><div><h1>About</h1></div></div>
      <section className="card" style={{ maxWidth: '62ch' }}>
        <p style={{ marginTop: 0 }}>
          Built by <a href={site.authorUrl} target="_blank" rel="noopener noreferrer">{site.author}</a>, a homebuyer who started this to research buying a property.
        </p>
        <p>
          I build tools like this for small businesses: pulling scattered records together and automating the paperwork around them.
          Get in touch at <a href={`mailto:${site.email}`}>{site.email}</a>.
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>The data comes from public records. This is research, not advice.</p>
      </section>
    </>
  )
}
