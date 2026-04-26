const $ = (id) => {
  const el = document.getElementById(id)
  if (!el) throw new Error(`Missing element: ${id}`)
  return el
}

const storePathEl = $("storePath")
const reloadBtn = $("reloadBtn")
const searchEl = $("search")
const metaSearchEl = $("metaSearch")
const leafSearchEl = $("leafSearch")
const topicSearchEl = $("topicSearch")
const clearTopicBtnEl = $("clearTopicBtn")
const sortEl = $("sort")
const lockedOnlyEl = $("lockedOnly")
const suggestedOnlyEl = $("suggestedOnly")
const moreBtnEl = $("moreBtn")
const statusEl = $("status")
const metaSummaryEl = $("metaSummary")
const metaListEl = $("metaList")
const panelTitleEl = $("panelTitle")
const panelSubtitleEl = $("panelSubtitle")
const leafListEl = $("leafList")
const repoTitleEl = $("repoTitle")
const repoMetaEl = $("repoMeta")
const repoListEl = $("repoList")
const topicSummaryEl = $("topicSummary")
const topicListEl = $("topicList")

const state = {
  data: null,
  selectedMetaId: null,
  selectedLeafId: null,
  search: "",
  metaFilter: "",
  leafFilter: "",
  topicFilter: "",
  topicQuery: "",
  sortKey: "score",
  lockedOnly: false,
  suggestedOnly: false,
  renderLimit: 200,
}

const fmt = {
  n: (x) => new Intl.NumberFormat().format(Number(x || 0)),
  score: (x) => (typeof x === "number" ? x.toFixed(3) : ""),
}

function resetPagination() {
  state.renderLimit = 200
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" })
  if (!res.ok) throw new Error(`Fetch failed: ${res.status} ${res.statusText} (${url})`)
  return res.json()
}

function normalizeLabelName(s) {
  if (typeof s !== "string") return ""
  return s.replaceAll("claude-code", "coding-agent").replaceAll("llm", "genai")
}

function buildIndex(catalog) {
  const repos = new Map()
  for (const r of catalog?.repos || []) {
    if (!r || typeof r !== "object") continue
    const id = r.full_name
    if (typeof id !== "string" || !id) continue
    repos.set(id, r)
  }
  return repos
}

function repoUrl(repoId, repo) {
  if (repo && typeof repo.html_url === "string" && repo.html_url) return repo.html_url
  return `https://github.com/${repoId}`
}

function repoBadges(repoId, repo, assignment) {
  const out = []
  const topics = Array.isArray(repo?.topics) ? repo.topics.filter((t) => typeof t === "string" && t).slice(0, 6) : []
  const lang = typeof repo?.language === "string" ? repo.language : ""
  const stars = repo?.signals?.stars ?? repo?.stats?.stargazers_count
  const recency = repo?.signals?.recency_days
  if (lang) out.push({ text: lang, kind: "badge blue", type: "lang" })
  if (typeof stars === "number") out.push({ text: `★ ${fmt.n(stars)}`, kind: "badge", type: "stars" })
  if (typeof recency === "number") out.push({ text: `last ${recency}d`, kind: "badge", type: "recency" })
  for (const t of topics) out.push({ text: t, kind: "badge", type: "topic" })
  if (assignment?.locked) out.push({ text: "locked", kind: "badge accent", type: "locked" })
  if (assignment?.suggested_meta_id) out.push({ text: `suggest ${assignment.suggested_meta_id}`, kind: "badge warn", type: "suggest" })
  if (typeof assignment?.suggested_score === "number") out.push({ text: `score ${fmt.score(assignment.suggested_score)}`, kind: "badge warn", type: "suggest" })
  return out
}

function computeDerived(labels, assignments, catalogRepos) {
  const labelsById = new Map()
  const meta = []
  const leaf = []
  let misc = null

  for (const l of labels || []) {
    if (!l || typeof l !== "object") continue
    const id = l.id
    if (typeof id !== "string" || !id) continue
    const name = normalizeLabelName(l.name || id)
    const row = { ...l, name }
    labelsById.set(id, row)
    if (id === "misc") misc = row
    else if (id.startsWith("meta-")) meta.push(row)
    else if (id.startsWith("leaf-")) leaf.push(row)
  }
  if (!misc) {
    misc = { id: "misc", name: "Misc", description: "", created_at: "", updated_at: "" }
    labelsById.set("misc", misc)
  }

  const assignmentByRepo = new Map()
  const reposByMeta = new Map()
  const reposByLeaf = new Map()
  const reposByMisc = new Set()

  for (const a of assignments || []) {
    if (!a || typeof a !== "object") continue
    const rid = a.repo_id
    if (typeof rid !== "string" || !rid) continue
    assignmentByRepo.set(rid, a)

    const cids = Array.isArray(a.category_ids) ? a.category_ids.filter((x) => typeof x === "string" && x) : []
    if (!cids.length) reposByMisc.add(rid)
    for (const cid of cids) {
      if (cid === "misc") reposByMisc.add(rid)
      if (cid.startsWith("meta-")) {
        if (!reposByMeta.has(cid)) reposByMeta.set(cid, new Set())
        reposByMeta.get(cid).add(rid)
      }
      if (cid.startsWith("leaf-")) {
        if (!reposByLeaf.has(cid)) reposByLeaf.set(cid, new Set())
        reposByLeaf.get(cid).add(rid)
      }
    }
  }

  const leafByMeta = new Map()
  for (const l of leaf) {
    const pid = typeof l.parent_id === "string" ? l.parent_id : null
    if (!pid) continue
    if (!leafByMeta.has(pid)) leafByMeta.set(pid, [])
    leafByMeta.get(pid).push(l)
  }

  for (const [k, v] of leafByMeta.entries()) {
    v.sort((a, b) => String(a.name).localeCompare(String(b.name)))
  }

  const metaCounts = new Map()
  for (const m of meta) metaCounts.set(m.id, (reposByMeta.get(m.id)?.size ?? 0))
  const leafCounts = new Map()
  for (const l of leaf) leafCounts.set(l.id, (reposByLeaf.get(l.id)?.size ?? 0))

  meta.sort((a, b) => (metaCounts.get(b.id) ?? 0) - (metaCounts.get(a.id) ?? 0) || String(a.name).localeCompare(String(b.name)))

  const repoIndex = buildIndex({ repos: catalogRepos })

  return {
    labelsById,
    meta,
    leaf,
    misc,
    leafByMeta,
    assignmentByRepo,
    reposByMeta,
    reposByLeaf,
    reposByMisc,
    metaCounts,
    leafCounts,
    repoIndex,
  }
}

function setStatus(text, kind = "ok") {
  statusEl.textContent = text
  statusEl.style.color = kind === "err" ? "rgba(255,107,107,0.92)" : "rgba(255,255,255,0.66)"
}

function isTextMatch(hay, q) {
  if (!q) return true
  return String(hay || "").toLowerCase().includes(q)
}

function renderMetaList(derived) {
  metaListEl.innerHTML = ""

  const totalRepos = state.data.assignments.length
  const totalMeta = derived.meta.length
  const totalLeaf = derived.leaf.length
  const miscCount = derived.reposByMisc.size

  metaSummaryEl.textContent = `${fmt.n(totalRepos)} repos · ${fmt.n(totalMeta)} meta · ${fmt.n(totalLeaf)} leaf`

  const q = state.metaFilter.trim().toLowerCase()

  for (const m of derived.meta) {
    if (q) {
      const leafs = derived.leafByMeta.get(m.id) || []
      const leafHit = leafs.some((l) => isTextMatch(l.name, q))
      const metaHit = isTextMatch(m.name, q)
      if (!metaHit && !leafHit) continue
    }
    const count = derived.metaCounts.get(m.id) ?? 0
    const item = document.createElement("div")
    item.className = `meta-item ${state.selectedMetaId === m.id ? "active" : ""}`

    const name = document.createElement("div")
    name.className = "meta-name"
    name.textContent = m.name

    const badge = document.createElement("div")
    badge.className = "meta-count"
    badge.textContent = fmt.n(count)

    item.appendChild(name)
    item.appendChild(badge)
    item.addEventListener("click", () => {
      state.selectedMetaId = m.id
      state.selectedLeafId = null
      state.search = ""
      searchEl.value = ""
      resetPagination()
      render()
    })
    metaListEl.appendChild(item)
  }

  const miscItem = document.createElement("div")
  miscItem.className = `meta-item ${state.selectedMetaId === "misc" ? "active" : ""}`
  const miscName = document.createElement("div")
  miscName.className = "meta-name"
  miscName.textContent = "Misc / Outliers"
  const miscBadge = document.createElement("div")
  miscBadge.className = "meta-count"
  miscBadge.textContent = fmt.n(miscCount)
  miscItem.appendChild(miscName)
  miscItem.appendChild(miscBadge)
  miscItem.addEventListener("click", () => {
    state.selectedMetaId = "misc"
    state.selectedLeafId = null
    state.search = ""
    searchEl.value = ""
    resetPagination()
    render()
  })
  metaListEl.appendChild(miscItem)
}

function renderLeafList(derived) {
  leafListEl.innerHTML = ""

  if (!state.selectedMetaId) {
    panelTitleEl.textContent = "Pick a top-level category"
    panelSubtitleEl.textContent = ""
    return
  }

  if (state.selectedMetaId === "misc") {
    panelTitleEl.textContent = "Misc / Outliers"
    panelSubtitleEl.textContent = `${fmt.n(derived.reposByMisc.size)} repos`
    return
  }

  const metaLabel = derived.labelsById.get(state.selectedMetaId)
  const leafs = derived.leafByMeta.get(state.selectedMetaId) || []
  const metaCount = derived.metaCounts.get(state.selectedMetaId) ?? 0

  panelTitleEl.textContent = metaLabel ? metaLabel.name : state.selectedMetaId
  panelSubtitleEl.textContent = `${fmt.n(metaCount)} repos · ${fmt.n(leafs.length)} leaf clusters`

  const q = state.leafFilter.trim().toLowerCase()

  for (const l of leafs) {
    if (q && !isTextMatch(l.name, q)) continue
    const count = derived.leafCounts.get(l.id) ?? 0
    const item = document.createElement("div")
    item.className = `leaf-item ${state.selectedLeafId === l.id ? "active" : ""}`

    const name = document.createElement("div")
    name.className = "leaf-name"
    name.textContent = l.name

    const badge = document.createElement("div")
    badge.className = "meta-count"
    badge.textContent = fmt.n(count)

    item.appendChild(name)
    item.appendChild(badge)
    item.addEventListener("click", () => {
      state.selectedLeafId = l.id
      resetPagination()
      render()
    })
    leafListEl.appendChild(item)
  }
}

function scoreFor(repoId, derived) {
  const a = derived.assignmentByRepo.get(repoId) || null
  if (typeof a?.confidence === "number") return a.confidence
  if (typeof a?.suggested_score === "number") return a.suggested_score
  return 0
}

function starsFor(repoId, derived) {
  const repo = derived.repoIndex.get(repoId) || null
  const v = repo?.signals?.stars ?? repo?.stats?.stargazers_count
  return typeof v === "number" ? v : 0
}

function recencyFor(repoId, derived) {
  const repo = derived.repoIndex.get(repoId) || null
  const v = repo?.signals?.recency_days
  return typeof v === "number" ? v : 10_000
}

function applyAssignmentFilters(list, derived) {
  if (!state.lockedOnly && !state.suggestedOnly) return list
  const out = []
  for (const rid of list) {
    const a = derived.assignmentByRepo.get(rid) || null
    const locked = a?.locked === true
    const suggested = Boolean(a?.suggested_meta_id || a?.suggested_category_id)
    if (state.lockedOnly && !locked) continue
    if (state.suggestedOnly && !suggested) continue
    out.push(rid)
  }
  return out
}

function applyTopicFilter(list, derived) {
  const t = state.topicFilter.trim()
  if (!t) return list
  const out = []
  for (const rid of list) {
    const repo = derived.repoIndex.get(rid) || null
    const topics = Array.isArray(repo?.topics) ? repo.topics : []
    if (topics.includes(t)) out.push(rid)
  }
  return out
}

function sortRepos(list, derived) {
  const key = state.sortKey
  const copy = [...list]
  if (key === "stars") {
    copy.sort((a, b) => starsFor(b, derived) - starsFor(a, derived) || a.localeCompare(b))
    return copy
  }
  if (key === "recency") {
    copy.sort((a, b) => recencyFor(a, derived) - recencyFor(b, derived) || a.localeCompare(b))
    return copy
  }
  if (key === "score") {
    copy.sort((a, b) => scoreFor(b, derived) - scoreFor(a, derived) || a.localeCompare(b))
    return copy
  }
  copy.sort((a, b) => a.localeCompare(b))
  return copy
}

function getContextRepoIds(derived) {
  const q = state.search.trim().toLowerCase()
  if (q) {
    const hits = []
    for (const [rid, repo] of derived.repoIndex.entries()) {
      const desc = typeof repo.description === "string" ? repo.description : ""
      const topics = Array.isArray(repo.topics) ? repo.topics.join(" ") : ""
      const hay = `${rid} ${desc} ${topics}`.toLowerCase()
      if (hay.includes(q)) hits.push(rid)
    }
    return hits
  }
  if (state.selectedMetaId === "misc") return Array.from(derived.reposByMisc)
  if (state.selectedLeafId) return Array.from(derived.reposByLeaf.get(state.selectedLeafId) || [])
  if (state.selectedMetaId) return Array.from(derived.reposByMeta.get(state.selectedMetaId) || [])
  return []
}

function renderTopics(derived) {
  topicListEl.innerHTML = ""
  const context = getContextRepoIds(derived)
  const filtered = applyAssignmentFilters(context, derived)
  const counts = new Map()
  for (const rid of filtered) {
    const repo = derived.repoIndex.get(rid) || null
    const topics = Array.isArray(repo?.topics) ? repo.topics : []
    for (const t of topics) {
      if (typeof t !== "string" || !t) continue
      counts.set(t, (counts.get(t) || 0) + 1)
    }
  }

  const entries = Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  const q = state.topicQuery.trim().toLowerCase()
  const active = state.topicFilter.trim()
  const shown = []
  for (const [t, n] of entries) {
    if (q && !t.toLowerCase().includes(q)) continue
    shown.push([t, n])
    if (shown.length >= 300) break
  }

  topicSummaryEl.textContent = `${fmt.n(entries.length)} topics · ${fmt.n(filtered.length)} repos`
  clearTopicBtnEl.textContent = active ? `Clear (${active})` : "Clear"

  if (!shown.length) {
    const empty = document.createElement("div")
    empty.className = "repo-desc"
    empty.textContent = "No topics found for this selection."
    topicListEl.appendChild(empty)
    return
  }

  for (const [t, n] of shown) {
    const item = document.createElement("button")
    item.className = `topic-item ${active === t ? "active" : ""}`
    item.type = "button"

    const name = document.createElement("div")
    name.className = "topic-name"
    name.textContent = t

    const badge = document.createElement("div")
    badge.className = "topic-count"
    badge.textContent = fmt.n(n)

    item.appendChild(name)
    item.appendChild(badge)
    item.addEventListener("click", () => {
      state.topicFilter = t
      resetPagination()
      render()
    })
    topicListEl.appendChild(item)
  }
}

function renderRepos(derived) {
  repoListEl.innerHTML = ""
  moreBtnEl.style.display = "none"

  const q = state.search.trim().toLowerCase()
  if (q) {
    repoTitleEl.textContent = "Search Results"
    const hits = getContextRepoIds(derived)
    const filtered = applyTopicFilter(applyAssignmentFilters(hits, derived), derived)
    const sorted = sortRepos(filtered, derived)
    const shown = sorted.slice(0, state.renderLimit)
    repoMetaEl.textContent = `${fmt.n(filtered.length)} matches · showing ${fmt.n(shown.length)}`
    for (const rid of shown) repoListEl.appendChild(renderRepoCard(derived, rid, q))
    if (shown.length < sorted.length) moreBtnEl.style.display = "inline-flex"
    return
  }

  let list = []
  if (state.selectedMetaId === "misc") {
    list = Array.from(derived.reposByMisc)
    repoTitleEl.textContent = "Misc Repos"
  } else if (state.selectedLeafId) {
    list = Array.from(derived.reposByLeaf.get(state.selectedLeafId) || [])
    const leafLabel = derived.labelsById.get(state.selectedLeafId)
    repoTitleEl.textContent = leafLabel ? leafLabel.name : "Repos"
  } else if (state.selectedMetaId) {
    list = Array.from(derived.reposByMeta.get(state.selectedMetaId) || [])
    const metaLabel = derived.labelsById.get(state.selectedMetaId)
    repoTitleEl.textContent = metaLabel ? metaLabel.name : "Repos"
  } else {
    repoTitleEl.textContent = "Repos"
    repoMetaEl.textContent = ""
    return
  }

  const filtered = applyTopicFilter(applyAssignmentFilters(list, derived), derived)
  const sorted = sortRepos(filtered, derived)
  const shown = sorted.slice(0, state.renderLimit)
  repoMetaEl.textContent = `${fmt.n(filtered.length)} repos · showing ${fmt.n(shown.length)}`
  for (const rid of shown) repoListEl.appendChild(renderRepoCard(derived, rid, ""))
  if (shown.length < sorted.length) moreBtnEl.style.display = "inline-flex"
}

function appendHighlighted(parent, text, q) {
  if (!q) {
    parent.textContent = text
    return
  }
  const s = String(text || "")
  const lower = s.toLowerCase()
  const ql = q.toLowerCase()
  const idx = lower.indexOf(ql)
  if (idx < 0) {
    parent.textContent = s
    return
  }
  const before = s.slice(0, idx)
  const match = s.slice(idx, idx + q.length)
  const after = s.slice(idx + q.length)
  if (before) parent.appendChild(document.createTextNode(before))
  const mark = document.createElement("span")
  mark.className = "mark"
  mark.textContent = match
  parent.appendChild(mark)
  if (after) parent.appendChild(document.createTextNode(after))
}

function jumpToSuggestion(derived, assignment) {
  const meta = assignment?.suggested_meta_id
  const leaf = assignment?.suggested_category_id
  if (typeof meta === "string" && derived.labelsById.has(meta)) state.selectedMetaId = meta
  if (typeof leaf === "string" && derived.labelsById.has(leaf)) state.selectedLeafId = leaf
  state.topicFilter = ""
  resetPagination()
  render()
}

function jumpToCategory(derived, categoryId) {
  if (typeof categoryId !== "string" || !categoryId) return
  if (categoryId === "misc") {
    state.selectedMetaId = "misc"
    state.selectedLeafId = null
    state.topicFilter = ""
    resetPagination()
    render()
    return
  }
  const label = derived.labelsById.get(categoryId)
  if (!label) return
  if (categoryId.startsWith("meta-")) {
    state.selectedMetaId = categoryId
    state.selectedLeafId = null
    state.topicFilter = ""
    resetPagination()
    render()
    return
  }
  if (categoryId.startsWith("leaf-")) {
    const pid = typeof label.parent_id === "string" ? label.parent_id : null
    if (pid && derived.labelsById.has(pid)) state.selectedMetaId = pid
    state.selectedLeafId = categoryId
    state.topicFilter = ""
    resetPagination()
    render()
  }
}

function renderRepoCard(derived, repoId, q) {
  const repo = derived.repoIndex.get(repoId) || null
  const a = derived.assignmentByRepo.get(repoId) || null

  const card = document.createElement("div")
  card.className = "repo"

  const top = document.createElement("div")
  top.className = "repo-top"

  const link = document.createElement("a")
  link.href = repoUrl(repoId, repo)
  link.target = "_blank"
  link.rel = "noreferrer"
  appendHighlighted(link, repoId, q)

  const cids = Array.isArray(a?.category_ids) ? a.category_ids.filter((x) => typeof x === "string" && x) : []

  top.appendChild(link)
  const actions = document.createElement("div")
  actions.className = "repo-actions"

  const pills = cids.length ? cids : ["misc"]
  for (const cid of pills) {
    const label = derived.labelsById.get(cid)
    const pill = document.createElement("button")
    pill.type = "button"
    pill.className = "chip"
    pill.textContent = label?.name || cid
    pill.addEventListener("click", (e) => {
      e.preventDefault()
      e.stopPropagation()
      jumpToCategory(derived, cid)
    })
    actions.appendChild(pill)
  }

  if (a?.suggested_meta_id || a?.suggested_category_id) {
    const chip = document.createElement("button")
    chip.className = "chip"
    chip.type = "button"
    chip.textContent = "jump"
    chip.addEventListener("click", (e) => {
      e.preventDefault()
      e.stopPropagation()
      jumpToSuggestion(derived, a)
    })
    actions.appendChild(chip)
  }
  top.appendChild(actions)
  card.appendChild(top)

  const desc = typeof repo?.description === "string" ? repo.description.trim() : ""
  if (desc) {
    const p = document.createElement("div")
    p.className = "repo-desc"
    appendHighlighted(p, desc, q)
    card.appendChild(p)
  }

  const badges = repoBadges(repoId, repo, a)
  if (badges.length) {
    const row = document.createElement("div")
    row.className = "badges"
    for (const b of badges) {
      const el = document.createElement("span")
      el.className = b.kind
      el.textContent = b.text
      if (b.type === "topic") {
        el.style.cursor = "pointer"
        el.addEventListener("click", (e) => {
          e.preventDefault()
          e.stopPropagation()
          state.topicFilter = b.text
          resetPagination()
          render()
        })
      }
      row.appendChild(el)
    }
    card.appendChild(row)
  }

  return card
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

  const derived = computeDerived(labels, assignments, catalog?.repos || [])
  return { labels, assignments, catalog, derived, store: base }
}

function render() {
  const d = state.data?.derived
  if (!d) return
  renderMetaList(d)
  renderLeafList(d)
  renderTopics(d)
  renderRepos(d)
}

function applyUrlParamsOnce(derived) {
  const qs = new URLSearchParams(window.location.search || "")
  if (![...qs.keys()].length) return

  const cat = qs.get("cat")
  const meta = qs.get("meta")
  const leaf = qs.get("leaf")
  const topic = qs.get("topic")

  let did = false

  if (cat && derived.labelsById.has(cat)) {
    jumpToCategory(derived, cat)
    did = true
  } else {
    if (meta && derived.labelsById.has(meta)) {
      state.selectedMetaId = meta
      state.selectedLeafId = null
      did = true
    }
    if (leaf && derived.labelsById.has(leaf)) {
      const lbl = derived.labelsById.get(leaf)
      const pid = typeof lbl?.parent_id === "string" ? lbl.parent_id : null
      if (pid && derived.labelsById.has(pid)) state.selectedMetaId = pid
      state.selectedLeafId = leaf
      did = true
    }
  }

  if (topic) {
    state.topicFilter = topic
    did = true
  }

  if (did) {
    resetPagination()
    history.replaceState(null, "", window.location.pathname)
  }
}

async function reload() {
  try {
    state.data = await loadAll()
    setStatus(`ok · ${state.data.store}`)
    localStorage.setItem("ghsorter.viewer.store", storePathEl.value.trim())

    applyUrlParamsOnce(state.data.derived)

    if (!state.selectedMetaId) {
      const firstMeta = state.data.derived.meta[0]?.id
      state.selectedMetaId = firstMeta || "misc"
      state.selectedLeafId = null
    }
    panelTitleEl.textContent = "Ready"
    panelSubtitleEl.textContent = ""
    render()
  } catch (e) {
    setStatus(String(e?.message || e), "err")
    panelTitleEl.textContent = "Failed to load"
    panelSubtitleEl.textContent = String(e?.message || e)
    metaListEl.innerHTML = ""
    leafListEl.innerHTML = ""
    repoListEl.innerHTML = ""
  }
}

reloadBtn.addEventListener("click", () => reload())
searchEl.addEventListener("input", () => {
  state.search = searchEl.value || ""
  state.topicFilter = ""
  resetPagination()
  render()
})
metaSearchEl.addEventListener("input", () => {
  state.metaFilter = metaSearchEl.value || ""
  render()
})
leafSearchEl.addEventListener("input", () => {
  state.leafFilter = leafSearchEl.value || ""
  render()
})
topicSearchEl.addEventListener("input", () => {
  state.topicQuery = topicSearchEl.value || ""
  render()
})
clearTopicBtnEl.addEventListener("click", () => {
  state.topicFilter = ""
  resetPagination()
  render()
})
sortEl.addEventListener("change", () => {
  state.sortKey = sortEl.value || "score"
  localStorage.setItem("ghsorter.viewer.sort", state.sortKey)
  resetPagination()
  render()
})
lockedOnlyEl.addEventListener("change", () => {
  state.lockedOnly = lockedOnlyEl.checked
  localStorage.setItem("ghsorter.viewer.lockedOnly", state.lockedOnly ? "1" : "0")
  resetPagination()
  render()
})
suggestedOnlyEl.addEventListener("change", () => {
  state.suggestedOnly = suggestedOnlyEl.checked
  localStorage.setItem("ghsorter.viewer.suggestedOnly", state.suggestedOnly ? "1" : "0")
  resetPagination()
  render()
})
moreBtnEl.addEventListener("click", () => {
  state.renderLimit += 200
  render()
})
storePathEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") reload()
})

document.addEventListener("keydown", (e) => {
  const tag = String(e.target?.tagName || "").toLowerCase()
  const typing = tag === "input" || tag === "textarea" || tag === "select"
  if (typing) return
  if (e.key === "/") {
    e.preventDefault()
    searchEl.focus()
  } else if (e.key === "m") {
    e.preventDefault()
    metaSearchEl.focus()
  } else if (e.key === "l") {
    e.preventDefault()
    leafSearchEl.focus()
  } else if (e.key === "r") {
    e.preventDefault()
    reload()
  }
})

const savedStore = localStorage.getItem("ghsorter.viewer.store")
if (savedStore) storePathEl.value = savedStore
const savedSort = localStorage.getItem("ghsorter.viewer.sort")
if (savedSort) {
  state.sortKey = savedSort
  sortEl.value = savedSort
}
lockedOnlyEl.checked = localStorage.getItem("ghsorter.viewer.lockedOnly") === "1"
state.lockedOnly = lockedOnlyEl.checked
suggestedOnlyEl.checked = localStorage.getItem("ghsorter.viewer.suggestedOnly") === "1"
state.suggestedOnly = suggestedOnlyEl.checked

reload()
