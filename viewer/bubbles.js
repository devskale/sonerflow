const $ = (id) => {
  const el = document.getElementById(id)
  if (!el) throw new Error(`Missing element: ${id}`)
  return el
}

const canvas = $("c")
const storePathEl = $("storePath")
const reloadBtn = $("reloadBtn")
const statusEl = $("status")
const crumbsEl = $("crumbs")
const hudTitleEl = $("hudTitle")
const hudSubEl = $("hudSub")
const tipEl = $("tip")

const ctx = canvas.getContext("2d", { alpha: true })

const state = {
  store: "../.ghsorter_store",
  data: null,
  mode: "meta",
  metaId: null,
  focusMetaId: null,
  focusWantedId: null,
  focusWantedAt: 0,
  zoom: 1,
  panX: 0,
  panY: 0,
  dragging: false,
  dragStart: null,
  pointer: { x: 0, y: 0 },
  hoverId: null,
  nodes: [],
  edges: [],
  subNodes: [],
  subMetaId: null,
  subLeafCount: 0,
  running: false,
}

const fmt = {
  n: (x) => new Intl.NumberFormat().format(Number(x || 0)),
}

function smoothstep(edge0, edge1, x) {
  const t = Math.max(0, Math.min(1, (x - edge0) / (edge1 - edge0)))
  return t * t * (3 - 2 * t)
}

function setStatus(text, kind = "ok") {
  statusEl.textContent = text
  statusEl.style.color = kind === "err" ? "rgba(255,107,107,0.92)" : "rgba(255,255,255,0.66)"
}

function resize() {
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const rect = canvas.getBoundingClientRect()
  canvas.width = Math.max(1, Math.floor(rect.width * dpr))
  canvas.height = Math.max(1, Math.floor(rect.height * dpr))
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
}

function toWorld(px, py) {
  const cx = canvas.getBoundingClientRect().width / 2
  const cy = canvas.getBoundingClientRect().height / 2
  return {
    x: (px - cx) / state.zoom - state.panX,
    y: (py - cy) / state.zoom - state.panY,
  }
}

function toScreen(wx, wy) {
  const cx = canvas.getBoundingClientRect().width / 2
  const cy = canvas.getBoundingClientRect().height / 2
  return {
    x: (wx + state.panX) * state.zoom + cx,
    y: (wy + state.panY) * state.zoom + cy,
  }
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" })
  if (!res.ok) throw new Error(`Fetch failed: ${res.status} ${res.statusText} (${url})`)
  return res.json()
}

function normalizeLabelName(s) {
  if (typeof s !== "string") return ""
  return s.replaceAll("claude-code", "coding-agent")
}

function buildDerived(labels, assignments, catalog) {
  const labelsById = new Map()
  const meta = []
  const leaf = []
  for (const l of labels || []) {
    if (!l || typeof l !== "object") continue
    const id = l.id
    if (typeof id !== "string" || !id) continue
    const name = normalizeLabelName(l.name || id)
    const row = { ...l, name }
    labelsById.set(id, row)
    if (id.startsWith("meta-")) meta.push(row)
    if (id.startsWith("leaf-")) leaf.push(row)
  }

  const assignmentByRepo = new Map()
  for (const a of assignments || []) {
    if (!a || typeof a !== "object") continue
    const rid = a.repo_id
    if (typeof rid !== "string" || !rid) continue
    assignmentByRepo.set(rid, a)
  }

  const repoIndex = new Map()
  for (const r of catalog?.repos || []) {
    if (!r || typeof r !== "object") continue
    const rid = r.full_name
    if (typeof rid !== "string" || !rid) continue
    repoIndex.set(rid, r)
  }

  const metaCounts = new Map()
  const leafCounts = new Map()
  const reposByMeta = new Map()
  const reposByLeaf = new Map()
  const leafByMeta = new Map()
  for (const l of leaf) {
    const pid = typeof l.parent_id === "string" ? l.parent_id : null
    if (!pid) continue
    if (!leafByMeta.has(pid)) leafByMeta.set(pid, [])
    leafByMeta.get(pid).push(l)
  }

  for (const a of assignments || []) {
    const rid = typeof a?.repo_id === "string" ? a.repo_id : null
    const cids = Array.isArray(a?.category_ids) ? a.category_ids.filter((x) => typeof x === "string" && x) : []
    for (const cid of cids) {
      if (cid.startsWith("meta-")) metaCounts.set(cid, (metaCounts.get(cid) || 0) + 1)
      if (cid.startsWith("leaf-")) leafCounts.set(cid, (leafCounts.get(cid) || 0) + 1)
      if (rid) {
        if (cid.startsWith("meta-")) {
          if (!reposByMeta.has(cid)) reposByMeta.set(cid, [])
          reposByMeta.get(cid).push(rid)
        }
        if (cid.startsWith("leaf-")) {
          if (!reposByLeaf.has(cid)) reposByLeaf.set(cid, [])
          reposByLeaf.get(cid).push(rid)
        }
      }
    }
  }

  meta.sort((a, b) => (metaCounts.get(b.id) || 0) - (metaCounts.get(a.id) || 0) || a.name.localeCompare(b.name))
  for (const [k, v] of leafByMeta.entries()) v.sort((a, b) => (leafCounts.get(b.id) || 0) - (leafCounts.get(a.id) || 0) || a.name.localeCompare(b.name))

  return { labelsById, meta, leaf, assignmentByRepo, repoIndex, metaCounts, leafCounts, reposByMeta, reposByLeaf, leafByMeta }
}

function addToMapCount(map, key, inc) {
  map.set(key, (map.get(key) || 0) + inc)
}

function topicVectorForRepos(repoIds, repoIndex) {
  const counts = new Map()
  for (const rid of repoIds || []) {
    const r = repoIndex.get(rid) || null
    const topics = Array.isArray(r?.topics) ? r.topics : []
    for (const t of topics) {
      if (typeof t !== "string" || !t) continue
      addToMapCount(counts, t, 1)
    }
    const lang = typeof r?.language === "string" ? r.language : ""
    if (lang) addToMapCount(counts, `lang:${lang}`, 0.25)
  }
  return counts
}

function cosineSim(a, b) {
  let dot = 0
  let na = 0
  let nb = 0
  for (const [k, va] of a.entries()) {
    na += va * va
    const vb = b.get(k)
    if (typeof vb === "number") dot += va * vb
  }
  for (const vb of b.values()) nb += vb * vb
  if (na <= 0 || nb <= 0) return 0
  return dot / (Math.sqrt(na) * Math.sqrt(nb))
}

function buildEdges(nodes, derived) {
  const vec = new Map()
  for (const n of nodes) {
    const repoIds =
      n.kind === "meta"
        ? derived.reposByMeta.get(n.id) || []
        : n.kind === "leaf"
          ? derived.reposByLeaf.get(n.id) || []
          : []
    vec.set(n.id, topicVectorForRepos(repoIds, derived.repoIndex))
  }

  const sims = []
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i]
      const b = nodes[j]
      const s = cosineSim(vec.get(a.id), vec.get(b.id))
      if (s > 0.06) sims.push({ a: a.id, b: b.id, w: s })
    }
  }

  const per = new Map()
  for (const e of sims) {
    if (!per.has(e.a)) per.set(e.a, [])
    if (!per.has(e.b)) per.set(e.b, [])
    per.get(e.a).push(e)
    per.get(e.b).push(e)
  }

  const keep = new Set()
  const k = state.mode === "meta" ? 6 : 5
  for (const n of nodes) {
    const arr = (per.get(n.id) || []).sort((x, y) => y.w - x.w)
    for (const e of arr.slice(0, k)) {
      const key = e.a < e.b ? `${e.a}|${e.b}` : `${e.b}|${e.a}`
      keep.add(key)
    }
  }

  return sims.filter((e) => {
    const key = e.a < e.b ? `${e.a}|${e.b}` : `${e.b}|${e.a}`
    return keep.has(key)
  })
}

function buildSubNodesForMeta(derived, metaNode, desiredLeafCount) {
  const allLeafs = derived.leafByMeta.get(metaNode.id) || []
  const leafs = allLeafs.slice(0, desiredLeafCount)
  const rest = allLeafs.slice(desiredLeafCount)
  const maxCount = Math.max(1, ...leafs.map((l) => derived.leafCounts.get(l.id) || 0))

  const out = []
  const base = Math.max(80, metaNode.r * 1.4)
  for (let i = 0; i < leafs.length; i++) {
    const l = leafs[i]
    const count = derived.leafCounts.get(l.id) || 0
    const r = 8 + Math.sqrt(count / maxCount) * (base * 0.16)
    const a = (i / Math.max(1, leafs.length)) * Math.PI * 2
    out.push({
      id: l.id,
      kind: "leaf",
      name: l.name || l.id,
      count,
      r,
      x: metaNode.x + Math.cos(a) * base * 0.18 + (Math.random() - 0.5) * 20,
      y: metaNode.y + Math.sin(a) * base * 0.18 + (Math.random() - 0.5) * 20,
      vx: 0,
      vy: 0,
      color: colorFor(l.name, 1),
    })
  }

  if (rest.length) {
    let restCount = 0
    for (const l of rest) restCount += derived.leafCounts.get(l.id) || 0
    out.push({
      id: "leaf-more",
      kind: "more",
      name: "more…",
      count: restCount,
      r: 10 + Math.sqrt(restCount / Math.max(1, restCount + maxCount)) * (base * 0.12),
      x: metaNode.x + (Math.random() - 0.5) * 20,
      y: metaNode.y + (Math.random() - 0.5) * 20,
      vx: 0,
      vy: 0,
      color: colorFor("more", 1),
    })
  }

  let sumArea = 0
  for (const n of out) sumArea += Math.PI * n.r * n.r
  const bound = Math.max(26, metaNode.r * 0.86)
  const avail = Math.PI * bound * bound
  const scale = Math.min(1, Math.sqrt((avail * 0.54) / Math.max(1, sumArea)))
  if (scale < 1) {
    for (const n of out) n.r *= scale
  }

  return out
}

function tickSubNodes(subNodes, metaNode, alpha) {
  if (!subNodes.length) return
  const damping = 0.86
  const centerPull = 0.010 * Math.max(0.25, alpha)
  const padExtra = 8
  const bound = Math.max(26, metaNode.r * 0.86)

  for (const n of subNodes) {
    n.vx += (metaNode.x - n.x) * centerPull
    n.vy += (metaNode.y - n.y) * centerPull
  }

  for (let i = 0; i < subNodes.length; i++) {
    for (let j = i + 1; j < subNodes.length; j++) {
      const a = subNodes[i]
      const b = subNodes[j]
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.hypot(dx, dy) || 0.0001
      const nx = dx / dist
      const ny = dy / dist
      const min = a.r + b.r + padExtra
      if (dist >= min) continue
      const push = (min - dist) * 0.08
      a.vx -= nx * push
      a.vy -= ny * push
      b.vx += nx * push
      b.vy += ny * push
    }
  }

  for (const n of subNodes) {
    n.vx *= damping
    n.vy *= damping
    n.x += n.vx
    n.y += n.vy
    const dx = n.x - metaNode.x
    const dy = n.y - metaNode.y
    const dist = Math.hypot(dx, dy) || 0.0001
    const max = bound - n.r
    if (dist > max) {
      const nx = dx / dist
      const ny = dy / dist
      n.x = metaNode.x + nx * max
      n.y = metaNode.y + ny * max
      n.vx *= 0.3
      n.vy *= 0.3
    }
  }
}

function colorFor(name, depth) {
  const s = String(name || "")
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  const hue = (h % 360) | 0
  const sat = depth === 0 ? 78 : 70
  const lit = depth === 0 ? 55 : 52
  return { hue, sat, lit }
}

function mkNodes(derived) {
  const nodes = []
  const w = canvas.getBoundingClientRect().width
  const h = canvas.getBoundingClientRect().height
  const base = Math.min(w, h) * 0.38
  const packRadius = Math.min(w, h) * 0.44

  if (state.mode === "meta") {
    const items = derived.meta
    const maxCount = Math.max(1, ...items.map((m) => derived.metaCounts.get(m.id) || 0))
    for (let i = 0; i < items.length; i++) {
      const m = items[i]
      const count = derived.metaCounts.get(m.id) || 0
      const subCount = derived.leafByMeta.get(m.id)?.length || 0
      const r = 18 + Math.sqrt(count / maxCount) * (base * 0.26)
      const a = (i / Math.max(1, items.length)) * Math.PI * 2
      nodes.push({
        id: m.id,
        kind: "meta",
        name: m.name || m.id,
        count,
        subCount,
        r,
        x: Math.cos(a) * base * 0.55 + (Math.random() - 0.5) * 60,
        y: Math.sin(a) * base * 0.55 + (Math.random() - 0.5) * 60,
        vx: 0,
        vy: 0,
        color: colorFor(m.name, 0),
      })
    }
  } else {
    const allLeafs = derived.leafByMeta.get(state.metaId) || []
    const maxLeaf = 60
    const leafs = allLeafs.slice(0, maxLeaf)
    const rest = allLeafs.slice(maxLeaf)
    const maxCount = Math.max(1, ...leafs.map((l) => derived.leafCounts.get(l.id) || 0))
    for (let i = 0; i < leafs.length; i++) {
      const l = leafs[i]
      const count = derived.leafCounts.get(l.id) || 0
      const r = 14 + Math.sqrt(count / maxCount) * (base * 0.20)
      const a = (i / Math.max(1, leafs.length)) * Math.PI * 2
      nodes.push({
        id: l.id,
        kind: "leaf",
        name: l.name || l.id,
        count,
        r,
        x: Math.cos(a) * base * 0.50 + (Math.random() - 0.5) * 60,
        y: Math.sin(a) * base * 0.50 + (Math.random() - 0.5) * 60,
        vx: 0,
        vy: 0,
        color: colorFor(l.name, 1),
      })
    }
    if (rest.length) {
      let restCount = 0
      for (const l of rest) restCount += derived.leafCounts.get(l.id) || 0
      nodes.push({
        id: "leaf-more",
        kind: "more",
        name: "more…",
        count: restCount,
        r: 18 + Math.sqrt(restCount / Math.max(1, restCount + (derived.leafCounts.get(leafs[0]?.id) || 1))) * (base * 0.16),
        x: (Math.random() - 0.5) * 80,
        y: (Math.random() - 0.5) * 80,
        vx: 0,
        vy: 0,
        color: colorFor("more", 1),
      })
    }
  }

  let sumArea = 0
  for (const n of nodes) sumArea += Math.PI * n.r * n.r
  const availArea = Math.PI * packRadius * packRadius
  const scale = Math.min(1, Math.sqrt((availArea * 0.58) / Math.max(1, sumArea)))
  if (scale < 1) {
    for (const n of nodes) n.r *= scale
  }

  return nodes
}

function tick(nodes) {
  const dt = 0.92
  const w = canvas.getBoundingClientRect().width
  const h = canvas.getBoundingClientRect().height
  const centerPull = 0.0026
  const damping = 0.90
  const edges = state.edges || []
  const padExtra = state.mode === "meta" ? 30 : 22

  for (const a of nodes) {
    a.vx += (-a.x) * centerPull
    a.vy += (-a.y) * centerPull
  }

  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i]
      const b = nodes[j]
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.hypot(dx, dy) || 0.0001
      const nx = dx / dist
      const ny = dy / dist
      const min = a.r + b.r + padExtra
      const repel = dist < min ? 0.20 : 0.035 / (dist * dist)
      a.vx -= nx * repel
      a.vy -= ny * repel
      b.vx += nx * repel
      b.vy += ny * repel
    }
  }

  for (const e of edges) {
    const a = nodes.find((n) => n.id === e.a)
    const b = nodes.find((n) => n.id === e.b)
    if (!a || !b) continue
    const dx = b.x - a.x
    const dy = b.y - a.y
    const dist = Math.hypot(dx, dy) || 0.0001
    const nx = dx / dist
    const ny = dy / dist
    const target = 140 + (1 - Math.min(0.95, e.w)) * 360
    const pull = (dist - target) / Math.max(1, target)
    const k = 0.008 * Math.min(1, e.w * 1.20)
    a.vx += nx * pull * k
    a.vy += ny * pull * k
    b.vx -= nx * pull * k
    b.vy -= ny * pull * k
  }

  for (const n of nodes) {
    n.vx *= damping
    n.vy *= damping
    n.x += n.vx * dt
    n.y += n.vy * dt
    const pad = Math.min(w, h) * 0.62
    n.x = Math.max(-pad, Math.min(pad, n.x))
    n.y = Math.max(-pad, Math.min(pad, n.y))
  }

  const relax = 3
  for (let step = 0; step < relax; step++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]
        const b = nodes[j]
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.hypot(dx, dy) || 0.0001
        const min = a.r + b.r + padExtra
        if (dist >= min) continue
        const nx = dx / dist
        const ny = dy / dist
        const push = (min - dist) * 0.52
        a.x -= nx * push
        a.y -= ny * push
        b.x += nx * push
        b.y += ny * push
      }
    }
  }
}

function draw(nodes) {
  const w = canvas.getBoundingClientRect().width
  const h = canvas.getBoundingClientRect().height
  ctx.clearRect(0, 0, w, h)
  ctx.save()
  ctx.translate(w / 2, h / 2)
  ctx.scale(state.zoom, state.zoom)
  ctx.translate(state.panX, state.panY)

  const grd = ctx.createRadialGradient(0, 0, 10, 0, 0, Math.min(w, h) * 0.6)
  grd.addColorStop(0, "rgba(124,247,192,0.08)")
  grd.addColorStop(0.55, "rgba(111,183,255,0.06)")
  grd.addColorStop(1, "rgba(0,0,0,0)")
  ctx.fillStyle = grd
  ctx.beginPath()
  ctx.arc(0, 0, Math.min(w, h) * 0.6, 0, Math.PI * 2)
  ctx.fill()

  const focusMeta = state.focusMetaId ? nodes.find((n) => n.id === state.focusMetaId) : null
  const subAlpha = state.mode === "meta" ? smoothstep(1.15, 1.95, state.zoom) : 0
  const hover =
    state.hoverId && state.hoverId.startsWith("sub:")
      ? state.subNodes.find((n) => `sub:${n.id}` === state.hoverId)
      : state.hoverId
        ? nodes.find((n) => n.id === state.hoverId)
        : null

  for (const n of nodes) {
    const isHover = hover && hover.id === n.id
    const c = n.color
    const glow = isHover ? 0.22 : 0.14
    const fillA = isHover ? 0.22 : 0.14
    const dim = focusMeta && subAlpha > 0 ? (n.id === focusMeta.id ? 1 : 0.35) : 1
    const fill = `hsla(${c.hue} ${c.sat}% ${c.lit}% / ${fillA})`
    const stroke = `hsla(${c.hue} ${c.sat}% ${c.lit + 8}% / ${isHover ? 0.88 : 0.50})`

    ctx.shadowColor = `hsla(${c.hue} ${c.sat}% ${c.lit + 12}% / ${glow * dim})`
    ctx.shadowBlur = isHover ? 26 : 18
    ctx.fillStyle = fill
    ctx.strokeStyle = stroke
    ctx.lineWidth = isHover ? 2.2 : 1.4
    ctx.globalAlpha = dim
    ctx.beginPath()
    ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2)
    ctx.fill()
    ctx.stroke()

    ctx.shadowBlur = 0
    const showLabel = isHover || n.r * state.zoom >= (state.mode === "meta" ? 34 : 30)
    ctx.textAlign = "center"
    ctx.textBaseline = "middle"

    if (showLabel) {
      ctx.fillStyle = "rgba(255,255,255,0.88)"
      ctx.font = `650 ${Math.max(10, Math.min(12, n.r * 0.25))}px ${getComputedStyle(document.documentElement).getPropertyValue("--sans")}`
      const text = String(n.name || n.id)
      const maxChars = Math.max(9, Math.floor(n.r * 0.66))
      const label = text.length > maxChars ? text.slice(0, maxChars - 1) + "…" : text
      ctx.fillText(label, n.x, n.y - 6)

      ctx.fillStyle = "rgba(255,255,255,0.62)"
      ctx.font = `500 ${Math.max(9, Math.min(11, n.r * 0.22))}px ${getComputedStyle(document.documentElement).getPropertyValue("--mono")}`
      ctx.fillText(n.kind === "meta" ? fmt.n(n.subCount || 0) : fmt.n(n.count), n.x, n.y + 12)
    } else {
      ctx.fillStyle = "rgba(255,255,255,0.55)"
      ctx.font = `500 ${Math.max(9, Math.min(11, n.r * 0.24))}px ${getComputedStyle(document.documentElement).getPropertyValue("--mono")}`
      ctx.fillText(n.kind === "meta" ? fmt.n(n.subCount || 0) : fmt.n(n.count), n.x, n.y + 2)
    }
  }

  ctx.globalAlpha = 1
  if (focusMeta && subAlpha > 0 && state.subNodes.length) {
    for (const n of state.subNodes) {
      const isHover = hover && hover.id === n.id
      const c = n.color
      const fill = `hsla(${c.hue} ${c.sat}% ${c.lit}% / ${isHover ? 0.26 : 0.18})`
      const stroke = `hsla(${c.hue} ${c.sat}% ${c.lit + 10}% / ${isHover ? 0.92 : 0.62})`
      ctx.globalAlpha = subAlpha
      ctx.shadowColor = `hsla(${c.hue} ${c.sat}% ${c.lit + 12}% / ${0.18})`
      ctx.shadowBlur = isHover ? 20 : 12
      ctx.fillStyle = fill
      ctx.strokeStyle = stroke
      ctx.lineWidth = isHover ? 2.0 : 1.2
      ctx.beginPath()
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.shadowBlur = 0

      const showLabel = isHover || (n.r * state.zoom >= 22 && subAlpha > 0.55)
      if (showLabel) {
        ctx.fillStyle = "rgba(255,255,255,0.86)"
        ctx.font = `650 ${Math.max(9, Math.min(11, n.r * 0.26))}px ${getComputedStyle(document.documentElement).getPropertyValue(
          "--sans"
        )}`
        ctx.textAlign = "center"
        ctx.textBaseline = "middle"
        const text = String(n.name || n.id)
        const maxChars = Math.max(8, Math.floor(n.r * 0.70))
        const label = text.length > maxChars ? text.slice(0, maxChars - 1) + "…" : text
        ctx.fillText(label, n.x, n.y - 5)
        ctx.fillStyle = "rgba(255,255,255,0.60)"
        ctx.font = `500 ${Math.max(8, Math.min(10, n.r * 0.22))}px ${getComputedStyle(document.documentElement).getPropertyValue("--mono")}`
        ctx.fillText(n.kind === "meta" ? fmt.n(n.subCount || 0) : fmt.n(n.count), n.x, n.y + 10)
      } else {
        ctx.fillStyle = "rgba(255,255,255,0.56)"
        ctx.font = `500 ${Math.max(8, Math.min(10, n.r * 0.24))}px ${getComputedStyle(document.documentElement).getPropertyValue("--mono")}`
        ctx.textAlign = "center"
        ctx.textBaseline = "middle"
        ctx.fillText(n.kind === "meta" ? fmt.n(n.subCount || 0) : fmt.n(n.count), n.x, n.y + 2)
      }
    }
  }

  ctx.globalAlpha = 1
  ctx.restore()
}

function findNodeAt(nodes, px, py) {
  const wpt = toWorld(px, py)
  const subAlpha = state.mode === "meta" ? smoothstep(1.15, 1.95, state.zoom) : 0
  if (subAlpha > 0.35 && state.subNodes.length) {
    for (let i = state.subNodes.length - 1; i >= 0; i--) {
      const n = state.subNodes[i]
      const dx = wpt.x - n.x
      const dy = wpt.y - n.y
      if (dx * dx + dy * dy <= n.r * n.r) return { ...n, id: `sub:${n.id}` }
    }
  }
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i]
    const dx = wpt.x - n.x
    const dy = wpt.y - n.y
    if (dx * dx + dy * dy <= n.r * n.r) return n
  }
  return null
}

function setTooltip(node, px, py) {
  if (!node) {
    tipEl.classList.remove("on")
    return
  }
  const sub = typeof node?.subCount === "number" && node.kind === "meta" ? `${fmt.n(node.subCount)} sub · ` : ""
  tipEl.classList.add("on")
  tipEl.style.left = `${px}px`
  tipEl.style.top = `${py}px`
  tipEl.innerHTML = `<div class="t">${node.name}</div><div class="m">${node.id} · ${sub}${fmt.n(node.count)} repos</div>`
}

function setCrumbs(derived) {
  if (state.mode === "meta") {
    crumbsEl.innerHTML = `<b>meta</b> · click to zoom`
    hudTitleEl.textContent = `Top-level categories`
    hudSubEl.textContent = `${fmt.n(derived.meta.length)} metas`
    return
  }
  const m = derived.labelsById.get(state.metaId)
  crumbsEl.innerHTML = `<b>${m?.name || state.metaId}</b> · Esc to go back`
  hudTitleEl.textContent = `${m?.name || state.metaId}`
  hudSubEl.textContent = `${fmt.n(derived.leafByMeta.get(state.metaId)?.length || 0)} leaf clusters`
}

function zoomTo(targetZoom, focusPx, focusPy) {
  const before = toWorld(focusPx, focusPy)
  state.zoom = Math.max(0.2, Math.min(20.0, targetZoom))
  const after = toWorld(focusPx, focusPy)
  state.panX += after.x - before.x
  state.panY += after.y - before.y
}

function resetView() {
  state.zoom = 1
  state.panX = 0
  state.panY = 0
}

function runLoop() {
  if (!state.running) return
  tick(state.nodes)
  if (state.mode === "meta" && state.data?.derived) {
    const subAlpha = smoothstep(1.15, 1.95, state.zoom)
    const reveal = smoothstep(1.35, 6.0, state.zoom)
    const now = performance.now()
    const baseFocus = state.nodes.find((n) => n.kind === "meta") || null

    let candidate = null
    if (typeof state.hoverId === "string" && state.hoverId.startsWith("sub:")) {
      candidate = state.subMetaId || state.focusMetaId
    } else if (typeof state.hoverId === "string" && state.nodes.some((n) => n.id === state.hoverId && n.kind === "meta")) {
      candidate = state.hoverId
    } else {
      candidate = state.focusMetaId || baseFocus?.id || null
    }

    if (candidate !== state.focusMetaId) {
      if (candidate !== state.focusWantedId) {
        state.focusWantedId = candidate
        state.focusWantedAt = now
      } else if (now - state.focusWantedAt > 160) {
        state.focusMetaId = candidate
        state.focusWantedId = null
        state.focusWantedAt = 0
      }
    } else {
      state.focusWantedId = null
      state.focusWantedAt = 0
    }

    const focus = state.focusMetaId ? state.nodes.find((n) => n.id === state.focusMetaId && n.kind === "meta") : null
    const focusNode = focus || baseFocus

    if (focusNode && subAlpha > 0.05) {
      const allLeafs = state.data.derived.leafByMeta.get(focusNode.id) || []
      const total = allLeafs.length
      const minShow = Math.min(10, total)
      const raw = Math.floor(minShow + (total - minShow) * reveal)
      const step = 5
      const desired = Math.min(total, Math.max(minShow, Math.ceil(raw / step) * step))

      if (state.subMetaId !== focusNode.id || desired !== state.subLeafCount) {
        state.subMetaId = focusNode.id
        state.subLeafCount = desired
        state.subNodes = buildSubNodesForMeta(state.data.derived, focusNode, desired)
      }
      tickSubNodes(state.subNodes, focusNode, subAlpha)
    } else {
      state.subMetaId = null
      state.subNodes = []
      state.subLeafCount = 0
    }
  }
  draw(state.nodes)
  requestAnimationFrame(runLoop)
}

async function loadAll() {
  const store = storePathEl.value.trim() || "../.ghsorter_store"
  const base = store.endsWith("/") ? store.slice(0, -1) : store
  setStatus("Loading JSON…")
  const [labels, assignments, catalog] = await Promise.all([
    fetchJson(`${base}/labels.json`),
    fetchJson(`${base}/assignments.json`),
    fetchJson(`${base}/catalog.json`),
  ])
  const derived = buildDerived(labels, assignments, catalog)
  return { derived, store: base }
}

function rebuild() {
  if (!state.data) return
  state.nodes = mkNodes(state.data.derived)
  state.edges = buildEdges(state.nodes, state.data.derived)
  state.hoverId = null
  state.focusMetaId = null
  state.focusWantedId = null
  state.focusWantedAt = 0
  state.subNodes = []
  state.subMetaId = null
  state.subLeafCount = 0
  setCrumbs(state.data.derived)
}

async function reload() {
  try {
    state.data = await loadAll()
    state.store = state.data.store
    localStorage.setItem("ghsorter.bubbles.store", storePathEl.value.trim())
    setStatus(`ok · ${state.store}`)
    state.mode = "meta"
    state.metaId = null
    resetView()
    rebuild()
    if (!state.running) {
      state.running = true
      runLoop()
    }
  } catch (e) {
    setStatus(String(e?.message || e), "err")
  }
}

function drill(node) {
  if (!node || !state.data) return
  const resolved = typeof node?.id === "string" && node.id.startsWith("sub:") ? { ...node, id: node.id.slice(4) } : node
  if (resolved.kind === "meta") {
    state.mode = "leaf"
    state.metaId = resolved.id
    resetView()
    rebuild()
    return
  }
  if (resolved.kind === "leaf") {
    const meta = state.mode === "leaf" ? state.metaId : state.subMetaId || state.metaId || ""
    const url = `./?meta=${encodeURIComponent(meta)}&leaf=${encodeURIComponent(resolved.id)}`
    window.location.href = url
  }
  if (resolved.kind === "more") {
    const meta = state.mode === "leaf" ? state.metaId : state.subMetaId || state.metaId || ""
    const url = `./?meta=${encodeURIComponent(meta)}`
    window.location.href = url
  }
}

function goBack() {
  if (state.mode === "leaf") {
    state.mode = "meta"
    state.metaId = null
    resetView()
    rebuild()
  }
}

canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  state.pointer.x = x
  state.pointer.y = y

  if (state.dragging && state.dragStart) {
    const dx = (x - state.dragStart.x) / state.zoom
    const dy = (y - state.dragStart.y) / state.zoom
    state.panX = state.dragStart.panX + dx
    state.panY = state.dragStart.panY + dy
    return
  }

  const hit = findNodeAt(state.nodes, x, y)
  state.hoverId = hit ? hit.id : null
  setTooltip(hit, e.clientX, e.clientY)
})

canvas.addEventListener("mouseleave", () => {
  state.hoverId = null
  setTooltip(null, 0, 0)
})

canvas.addEventListener("mousedown", (e) => {
  const rect = canvas.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  const hit = findNodeAt(state.nodes, x, y)
  if (hit) return
  state.dragging = true
  state.dragStart = { x, y, panX: state.panX, panY: state.panY }
})

window.addEventListener("mouseup", () => {
  state.dragging = false
  state.dragStart = null
})

canvas.addEventListener("click", (e) => {
  const rect = canvas.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  const hit = findNodeAt(state.nodes, x, y)
  if (!hit) {
    goBack()
    return
  }
  drill(hit)
})

canvas.addEventListener("wheel", (e) => {
  e.preventDefault()
  const rect = canvas.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  const delta = Math.sign(e.deltaY)
  const z = state.zoom * (delta > 0 ? 0.92 : 1.08)
  zoomTo(z, x, y)
})

reloadBtn.addEventListener("click", () => reload())
storePathEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") reload()
})

window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") goBack()
  if (e.key === "r") reload()
})

window.addEventListener("resize", () => {
  resize()
})

const savedStore = localStorage.getItem("ghsorter.bubbles.store")
if (savedStore) storePathEl.value = savedStore

resize()
reload()
