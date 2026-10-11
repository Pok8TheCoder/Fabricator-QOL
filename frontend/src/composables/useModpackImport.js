import { ref } from 'vue'
import { getProjectDetails, searchModpacks } from '../api/modrinth'
import { getCurseForgeProject, searchCurseForgeModpacks } from '../api/curseforge'
import { isLoaderCategory } from '../utils/loaderKind'

/**
 * Extract a Modrinth project slug or ID from a URL, slug, or raw ID.
 */
export function parseCurseForgeRef(input) {
  const raw = (input || '').trim()
  if (!raw) return { slug: '', modpack: true }

  // Prism Launcher import: curseforge://install?addonId=248787&fileId=4573208
  if (raw.startsWith('curseforge://')) {
    try {
      const parsed = new URL(raw.replace('curseforge://', 'https://curseforge.local/'))
      const addonId = parsed.searchParams.get('addonId') || parsed.searchParams.get('projectId')
      const fileId = parsed.searchParams.get('fileId')
      if (addonId && fileId) {
        return { slug: '', modpack: true, projectId: addonId, fileId }
      }
      if (addonId) {
        return { slug: addonId, modpack: true }
      }
    } catch {
      return { slug: '', modpack: true }
    }
  }

  if (!raw.startsWith('http://') && !raw.startsWith('https://')) {
    return { slug: raw.replace(/^@/, ''), modpack: true }
  }
  try {
    const parsed = new URL(raw)
    const parts = parsed.pathname.split('/').filter(Boolean)
    const idx = parts.indexOf('minecraft')
    if (idx >= 0 && parts[idx + 1] === 'modpacks' && parts[idx + 2]) {
      return { slug: parts[idx + 2], modpack: true }
    }
    if (idx >= 0 && parts[idx + 1] === 'mc-mods' && parts[idx + 2]) {
      return { slug: parts[idx + 2], modpack: false }
    }
  } catch {
    return { slug: '', modpack: true }
  }
  return { slug: '', modpack: true }
}

export function parseProjectRef(input) {
  const raw = (input || '').trim()
  if (!raw) {
    return ''
  }

  if (!raw.startsWith('http://') && !raw.startsWith('https://')) {
    return raw.replace(/^@/, '')
  }

  try {
    const parsed = new URL(raw)
    const parts = parsed.pathname.split('/').filter(Boolean)
    if ((parts[0] === 'modpack' || parts[0] === 'project') && parts[1]) {
      return parts[1]
    }
    return parts[parts.length - 1] || ''
  } catch {
    return ''
  }
}

/**
 * Normalize a Modrinth project/search-hit into a consistent shape.
 */
export function normalizeModpack(project) {
  return {
    id: project.project_id || project.id,
    slug: project.slug || '',
    catalog: project.catalog || 'modrinth',
    title: project.title || project.name || project.slug || 'Unknown Modpack',
    description: project.description || '',
    downloads: project.downloads || 0,
    iconUrl: project.icon_url || '',
    projectType: project.project_type || 'modpack',
    // What the pack actually supports. Kept because a selected pack is checked
    // against the form's version and loader — dropping these left the search
    // path with no way to know it was heading for an incompatible install
    // until the create request came back and failed.
    //
    // The two sources spell this differently and `versions` is a trap: on a
    // /project response it is a list of version IDs (hex), while on a /search
    // hit it is the game-version list. Read the project's explicit fields
    // first, so a link-resolved pack is never compared against hex IDs and
    // warned about every time.
    gameVersions: Array.isArray(project.game_versions)
      ? project.game_versions
      : (Array.isArray(project.versions) ? project.versions : []),
    loaders: (
      Array.isArray(project.loaders)
        ? project.loaders
        : (Array.isArray(project.categories) ? project.categories : []).filter(isLoaderCategory)
    ).map((value) => String(value).toLowerCase())
  }
}

function isNotFoundError(error) {
  if (!error) {
    return false
  }
  if (error.status === 404) {
    return true
  }
  return String(error.message || '').toLowerCase().includes('not found')
}

/**
 * Composable that encapsulates modpack search and link-resolution logic.
 *
 * @param {Object} options
 * @param {import('vue').Ref<string>|() => string} options.mcVersion
 * @param {import('vue').Ref<string>|() => string} options.loader
 */
export function useModpackImport({ mcVersion, loader, catalogSource } = {}) {
  const searchQuery = ref('')
  const modpackLink = ref('')
  const searchResults = ref([])
  const selectedModpack = ref(null)
  const loading = ref(false)
  const resolving = ref(false)
  const searchDone = ref(false)
  const errorMessage = ref('')
  // True while the results on screen are the popular list rather than an answer
  // to something typed — the two need different headings and empty-state copy.
  const showingPopular = ref(false)

  const getVersion = () => (typeof mcVersion === 'function' ? mcVersion() : mcVersion?.value) || ''
  const getLoader = () => (typeof loader === 'function' ? loader() : loader?.value) || ''
  const getCatalog = () => (typeof catalogSource === 'function' ? catalogSource() : catalogSource?.value) || 'modrinth'

  function selectPack(pack) {
    selectedModpack.value = normalizeModpack(pack)
    errorMessage.value = ''
  }

  function clearSelection() {
    selectedModpack.value = null
  }

  function resetAll() {
    searchQuery.value = ''
    modpackLink.value = ''
    searchResults.value = []
    selectedModpack.value = null
    loading.value = false
    resolving.value = false
    searchDone.value = false
    errorMessage.value = ''
    showingPopular.value = false
  }

  /**
   * Populate the results with the most-downloaded packs.
   *
   * Not a separate endpoint: the backend already sorts by downloads
   * (index='downloads'), so an empty query IS the popular list. Callers pass
   * the version/loader constraint implicitly — the browser modal is bound to a
   * real server and must stay filtered to what it can actually install, while
   * the create modal has nothing chosen yet and gets everything.
   */
  function loadPopular() {
    return performSearch('', { allowEmpty: true })
  }

  async function performSearch(queryOverride, { allowEmpty = false } = {}) {
    const query = (queryOverride ?? searchQuery.value ?? '').trim()
    // An empty query is only meaningful when it was asked for. Otherwise it
    // means the box was cleared, and showing the popular list there would look
    // like search results for nothing.
    if (!query && !allowEmpty) {
      searchResults.value = []
      searchDone.value = false
      showingPopular.value = false
      return
    }
    showingPopular.value = !query

    loading.value = true
    searchDone.value = false
    errorMessage.value = ''
    try {
      const response = getCatalog() === 'curseforge'
        ? await searchCurseForgeModpacks({
          query,
          version: getVersion(),
          loader: getLoader(),
          limit: 12
        })
        : await searchModpacks({
          query,
          version: getVersion(),
          loader: getLoader(),
          limit: 12
        })
      searchResults.value = Array.isArray(response.hits) ? response.hits : []
    } catch (error) {
      searchResults.value = []
      errorMessage.value = error.message || 'Failed to search modpacks.'
    } finally {
      loading.value = false
      searchDone.value = true
    }
  }

  async function resolveByLink() {
    if (getCatalog() === 'curseforge') {
      const ref = parseCurseForgeRef(modpackLink.value)
      const slug = ref.slug || ref.projectId
      if (!slug) {
        errorMessage.value = 'Enter a valid CurseForge modpack link, curseforge:// URL, or slug.'
        return
      }
      resolving.value = true
      errorMessage.value = ''
      try {
        const project = await getCurseForgeProject(slug)
        if (ref.fileId) {
          project.preferred_file_id = ref.fileId
        }
        searchResults.value = [project]
        selectPack(project)
      } catch (error) {
        errorMessage.value = error.message || 'Could not resolve CurseForge modpack.'
      } finally {
        resolving.value = false
      }
      return
    }

    const projectRef = parseProjectRef(modpackLink.value)
    if (!projectRef) {
      errorMessage.value = 'Enter a valid Modrinth modpack link, slug, or project ID.'
      return
    }

    resolving.value = true
    errorMessage.value = ''
    try {
      const project = await getProjectDetails(projectRef)
      if (project.project_type !== 'modpack') {
        errorMessage.value = 'This project exists but is not a modpack.'
        return
      }
      searchResults.value = [project]
      selectPack(project)
    } catch (error) {
      if (isNotFoundError(error)) {
        errorMessage.value = `No exact modpack found for "${projectRef}". Showing search results instead.`
        searchQuery.value = projectRef
        await performSearch(projectRef)
      } else {
        errorMessage.value = error.message || 'Could not resolve modpack from link.'
      }
    } finally {
      resolving.value = false
    }
  }

  return {
    searchQuery,
    modpackLink,
    searchResults,
    selectedModpack,
    loading,
    resolving,
    searchDone,
    showingPopular,
    errorMessage,
    selectPack,
    clearSelection,
    resetAll,
    performSearch,
    loadPopular,
    resolveByLink
  }
}
