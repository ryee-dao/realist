import { site } from '../site'

export default function About() {
  return (
    <>
      <div className="page-head"><div><h1>About</h1></div></div>
      <section className="card" style={{ maxWidth: '62ch' }}>
        <p style={{ marginTop: 0 }}>
          Built by <a href={site.authorUrl} target="_blank" rel="noopener noreferrer">{site.author}</a>, a homebuyer who started this to research buying a property.
        </p>
        <p style={{ marginBottom: 6 }}>I also build small tools for local businesses:</p>
        <ul style={{ margin: '0 0 14px', paddingLeft: 20 }}>
          <li>The spreadsheet you retype every week</li>
          <li>The forms you copy by hand</li>
          <li>The report nobody has time to make</li>
        </ul>
        <p style={{ marginBottom: 0 }}>
          Tell me the task you'd most like to stop doing: <a href={`mailto:${site.email}`}>{site.email}</a>
        </p>
      </section>
    </>
  )
}
