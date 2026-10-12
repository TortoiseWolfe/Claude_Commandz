export type AfaModsBand = { lanes: number; inbox: number; client: number }

declare module 'claude-code' {
  interface PluginState {
    'afa-mods': {
      isHub: boolean
      prepRan: boolean
      percent: number | null
      band: AfaModsBand
    }
  }
}
