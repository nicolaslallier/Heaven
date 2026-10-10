const locale = 'fr-CA'

export function relative(iso, now = Date.now()) {
  if (!iso) return '—'
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000)
  const units = [['day', 86400], ['hour', 3600], ['minute', 60]]
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit)
  }
  return formatter.format(seconds, 'second')
}

export function exact(iso) {
  return iso ? new Date(iso).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' }) : ''
}

export function bytes(value) {
  if (value == null) return '—'
  const units = ['o', 'Kio', 'Mio', 'Gio', 'Tio']
  let size = Math.abs(value)
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index++
  }
  return `${value < 0 ? '-' : ''}${size.toLocaleString(locale, { maximumFractionDigits: index ? 1 : 0 })} ${units[index]}`
}

export function duration(seconds) {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds} s`
  return `${Math.round(seconds / 60)} min`
}
