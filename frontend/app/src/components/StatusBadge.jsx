import { statusTone } from '../api'

export default function StatusBadge({ value }) {
  return <span className={`status-badge ${statusTone(value)}`}>{String(value || 'Unknown').replaceAll('_', ' ')}</span>
}

