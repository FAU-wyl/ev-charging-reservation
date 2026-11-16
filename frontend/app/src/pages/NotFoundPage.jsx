import { Link } from 'react-router-dom'

export default function NotFoundPage() {
  return <div className="page-state"><b>Page not found</b><span>The route does not exist.</span><Link className="btn primary compact" to="/">Back to map</Link></div>
}
