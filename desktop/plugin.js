import { Badge, Button, Input } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const styles = {
  page: { display: 'flex', flexDirection: 'column', gap: 24, maxWidth: 760, width: '100%', paddingBottom: 24 },
  section: { display: 'flex', flexDirection: 'column', gap: 10 },
  title: { fontSize: 14, fontWeight: 600, margin: 0 },
  hint: { fontSize: 12, lineHeight: 1.5, color: 'var(--ui-text-tertiary)', margin: 0, overflowWrap: 'anywhere' },
  row: { display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' },
  fields: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 16 },
  field: { display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13 },
  source: { display: 'flex', gap: 12, alignItems: 'flex-start', padding: '8px 0' },
  label: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 },
  message: { margin: 0, fontSize: 13, lineHeight: 1.5, overflowWrap: 'anywhere' }
}

function errorText(error) {
  const raw = error instanceof Error ? error.message : String(error)
  try {
    const data = JSON.parse(raw.replace(/^\d{3}:\s*/, ''))
    return data.detail?.message || data.detail || raw
  } catch { return raw.slice(0, 400) }
}

function Section({ title, children }) {
  return jsxs('section', { style: styles.section, children: [jsx('h2', { style: styles.title, children: title }), children] })
}

function Select({ label, value, onChange, options }) {
  return jsxs('label', { style: styles.field, children: [label, jsx('select', {
    value, onChange: e => onChange(e.target.value),
    style: { color: 'var(--ui-text-primary)', background: 'var(--ui-bg-secondary)', padding: '8px', border: '1px solid var(--ui-border)', borderRadius: 4, font: 'inherit' },
    children: options.map(([key, text]) => jsx('option', { value: key, children: text }, key))
  })] })
}

const states = { connected: 'Connected', key_required: 'Key required', unauthorized: 'Key rejected',
  rate_limited: 'Rate limited', disabled: 'Disabled', network: 'Network unavailable', timeout: 'Timed out', upstream: 'Source unavailable' }

export function LawSettingsPage({ ctx }) {
  const [data, setData] = useState(null)
  const [form, setForm] = useState(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState('Loading')
  const [message, setMessage] = useState(null)
  const [dirty, setDirty] = useState(false)
  const rest = (path, options) => ctx.rest(path, { timeoutMs: 120000, ...options })
  useEffect(() => {
    let active = true
    ctx.rest('status').then(next => {
      if (active) { setData(next); setForm(next.settings) }
    }).catch(error => { if (active) setMessage({ error: true, text: errorText(error) }) })
      .finally(() => { if (active) setBusy('') })
    return () => { active = false }
  }, [ctx])
  const change = (name, value) => { setForm(current => ({ ...current, [name]: value })); setDirty(true) }
  const run = async (label, action) => {
    setBusy(label); setMessage(null)
    try { await action() } catch (error) { setMessage({ error: true, text: errorText(error) }) }
    finally { setBusy('') }
  }
  const save = async () => {
    const next = await rest('config', { method: 'POST', body: { settings: form, api_key: key || null } })
    setData(next); setForm(next.settings); setKey(''); setDirty(false)
    return next
  }
  const check = () => run('Checking sources', async () => {
    if (dirty || key) await save()
    setData(await rest('check', { method: 'POST' }))
    setMessage({ text: 'Source checks completed. Each source shows its result below.' })
  })
  const exportDiagnostics = () => run('Exporting', async () => {
    const report = await rest('diagnostics')
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }))
    try {
      const link = document.createElement('a'); link.href = url; link.download = 'bg-law-diagnostics.json'
      document.body.append(link); link.click(); link.remove()
    } finally { setTimeout(() => URL.revokeObjectURL(url), 1000) }
  })
  const button = (text, onClick, primary = false, disabled = false) => jsx(Button, {
    type: 'button', size: 'sm', variant: primary ? 'default' : 'outline', disabled: !!busy || disabled,
    onClick, children: text
  })
  const source = (name, setting, checkId, hint) => {
    const last = data.sources?.[checkId]
    const state = !form[setting] ? 'Disabled' : last?.state === 'disabled' ? 'Not tested since enabling' : states[last?.state] || 'Not tested'
    return jsxs('label', { style: styles.source, htmlFor: `bg-law-${setting}`, children: [
      jsx('input', { id: `bg-law-${setting}`, 'aria-label': name, type: 'checkbox', checked: !!form[setting],
        onChange: e => change(setting, e.target.checked) }),
      jsxs('div', { style: { display: 'flex', flexDirection: 'column', gap: 4, flex: 1 }, children: [
        jsxs('div', { style: styles.row, children: [jsx('span', { children: name }),
          jsx(Badge, { variant: 'muted', children: state })] }),
        jsx('p', { style: styles.hint, children: hint }),
        last?.checked_at ? jsx('p', { style: styles.hint, children: `Checked ${last.checked_at}${last.next_step ? '. ' + last.next_step : ''}` }) : null
      ] })
    ] }, setting)
  }
  return jsxs('div', { style: styles.page, children: [
    jsx('p', { style: styles.hint, children: 'Bulgarian and EU legal texts, revision history and court decisions. Source wording and version details accompany each result.' }),
    (busy || message) ? jsx('div', { role: message?.error ? 'alert' : 'status', 'aria-live': 'polite', children:
      jsx('p', { style: { ...styles.message, color: message?.error ? 'var(--ui-red)' : 'var(--ui-text-primary)' },
        children: busy || message?.text || '' }) }) : null,
    !form ? (!busy ? button('Retry loading', () => run('Loading', async () => {
      const next = await rest('status'); setData(next); setForm(next.settings)
    })) : null) : jsxs('fieldset', { 'aria-label': 'Bulgarian and EU law settings', disabled: !!busy, style: { border: 0, padding: 0, margin: 0, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 24 }, children: [
      jsx(Section, { title: 'Connection', children: jsxs('div', { style: styles.section, children: [
        jsxs('label', { style: styles.field, children: ['lsource.net API key', jsx(Input, {
          type: 'password', value: key, autoComplete: 'off', spellCheck: false,
          placeholder: data.key_stored ? 'Key stored. Leave blank to keep it.' : 'Paste your free API key',
          onChange: e => setKey(e.target.value)
        })] }),
        jsxs('p', { style: styles.hint, children: ['Needed for Bulgarian legislation. ', jsx('a', {
          href: 'https://lsource.net/developers', target: '_blank', rel: 'noreferrer', children: 'Create a free key'
        }), '. EU and court sources do not require it.'] }),
        jsxs('div', { style: styles.row, children: [button('Save settings', () => run('Saving', async () => {
          await save(); setMessage({ text: 'Settings saved.' })
        }), true, !dirty && !key), button('Test connection', check)] })
      ] }) }),
      jsx(Section, { title: 'Sources', children: [
        source('Bulgarian legislation', 'bg_law', 'bg', 'lsource.net: reconstructed consolidated texts and history.'),
        source('EU legislation', 'eu_law', 'eu', 'Publications Office: acts, recitals and annexes in Bulgarian or English.'),
        source('Constitutional Court', 'bg_cases', 'court', 'Published Constitutional Court acts. Other Bulgarian courts are outside this plugin.'),
        source('EU courts', 'eu_cases', 'eu', 'CJEU and General Court judgments from the Publications Office.')
      ] }),
      jsx(Section, { title: 'Language', children: jsxs('div', { style: styles.fields, children: [
        jsx(Select, { label: 'EU text language', value: form.eu_language, onChange: v => change('eu_language', v), options: [['bg', 'Bulgarian'], ['en', 'English']] }),
        jsx(Select, { label: 'Attribution language', value: form.language, onChange: v => change('language', v), options: [['auto', 'Match the source text'], ['bg', 'Bulgarian'], ['en', 'English']] })
      ] }) }),
      jsx(Section, { title: 'Cache and freshness', children: jsxs('div', { style: styles.section, children: [
        jsxs('div', { style: styles.fields, children: [
          jsxs('label', { style: styles.field, children: ['Recheck after (hours)', jsx(Input, { type: 'number', min: 0, max: 8760, step: 1, value: form.max_age_hours,
            onChange: e => change('max_age_hours', e.target.value === '' ? '' : Number(e.target.value)) })] }),
          jsxs('label', { style: styles.field, children: ['Cache target (MB)', jsx(Input, { type: 'number', min: 5, max: 10000, step: 1, value: form.cache_max_mb,
            onChange: e => change('cache_max_mb', e.target.value === '' ? '' : Number(e.target.value)) })] })
        ] }),
        jsx('p', { style: styles.hint, children: 'Applies to every source. Set 0 to recheck every lookup. The document currently being read may exceed the cache target.' }),
        jsx(Select, { label: 'When a source is unavailable', value: form.allow_stale ? 'allow' : 'require', onChange: v => change('allow_stale', v === 'allow'),
          options: [['allow', 'Allow cached copy with a warning'], ['require', 'Require a fresh source check']] }),
        jsx('p', { style: styles.hint, children: `${(data.cache.bytes / 1048576).toFixed(1)} MB in ${data.cache.entries} cached responses.` }),
        jsxs('div', { style: styles.row, children: [
          button('Recheck on next lookup', () => run('Expiring cache', async () => {
            setData(await rest('cache/recheck', { method: 'POST' })); setMessage({ text: 'Cached responses will be rechecked on their next lookup.' })
          })),
          button('Clear cache', () => run('Clearing cache', async () => {
            setData(await rest('cache/clear', { method: 'POST' })); setMessage({ text: 'Cached legal texts cleared. Future lookups will download them again.' })
          }))
        ] })
      ] }) }),
      jsx(Section, { title: 'Diagnostics', children: jsxs('div', { style: styles.section, children: [
        jsx('p', { style: styles.hint, children: `bg-law ${data.version}. Diagnostics include settings and source checks; the API key and cached legal text are excluded.` }),
        jsx('div', { style: styles.row, children: button('Export diagnostics', exportDiagnostics) })
      ] }) })
    ] })
  ] })
}

export default {
  id: 'bg-law', name: 'Bulgarian and EU law',
  register(ctx) {
    ctx.registerSettingsPage?.({ id: 'settings', title: 'Bulgarian and EU law', icon: 'law',
      order: 120, render: () => jsx(LawSettingsPage, { ctx }) })
  }
}
