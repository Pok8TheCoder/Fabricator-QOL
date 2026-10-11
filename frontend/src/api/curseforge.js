import { get, post } from './client'

export async function getCurseForgeStatus() {
  return get('/api/curseforge/status')
}

export async function searchCurseForgeMods({
  query = '',
  version = '',
  loader = '',
  sort = 'relevance',
  limit = 20,
  offset = 0
}, options = {}) {
  return get('/api/curseforge/search', {
    query,
    mc_version: version,
    loader,
    index: sort,
    limit,
    offset
  }, { signal: options.signal })
}

export async function searchCurseForgeModpacks({
  query = '',
  version = '',
  loader = '',
  sort = 'downloads',
  limit = 12,
  offset = 0
}) {
  return get('/api/curseforge/modpacks/search', {
    query,
    mc_version: version,
    loader,
    index: sort,
    limit,
    offset
  })
}

export async function getCurseForgeProject(projectId) {
  return get(`/api/curseforge/project/${encodeURIComponent(projectId)}`)
}

export async function getCurseForgeModDependencies(modId, { mc_version, loader, server_id }) {
  return get(`/api/curseforge/mod/${encodeURIComponent(modId)}/dependencies`, {
    mc_version,
    loader,
    server_id
  })
}

export async function installCurseForgeMod(
  modId,
  { mc_version, loader, server_id, install_dependencies = true } = {}
) {
  return post(`/api/curseforge/mod/${encodeURIComponent(modId)}/install`, {
    mc_version,
    loader,
    server_id,
    install_dependencies
  })
}

export async function installCurseForgeModpack(projectId, body) {
  return post(`/api/curseforge/modpack/${encodeURIComponent(projectId)}/install`, body)
}

export async function getCurseForgeModpackInstallProgress(serverId) {
  return get(`/api/curseforge/modpack/install-progress/${encodeURIComponent(serverId)}`)
}
