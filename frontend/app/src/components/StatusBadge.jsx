import { statusLabel, statusTone } from '../api'

export default function StatusBadge({ value }) {
  return <span className={`status-badge ${statusTone(value)}`}>{statusLabel(value)}</span>
}
