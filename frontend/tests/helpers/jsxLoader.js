// Test-only Node loader: use Vite's installed Rolldown transformer, no server.
import { readFileSync } from 'node:fs'
import { registerHooks } from 'node:module'
import { fileURLToPath } from 'node:url'
import { transformSync } from 'rolldown/utils'

export function installJsxLoader() {
  return registerHooks({
    load(url, context, nextLoad) {
      if (url.endsWith('.jsx')) {
        const filename = fileURLToPath(url)
        const result = transformSync(filename, readFileSync(filename, 'utf8'), {
          lang: 'jsx', jsx: { runtime: 'automatic', development: true },
        })
        if (result.errors.length) throw new Error('Test JSX compilation failed')
        return { format: 'module', source: result.code, shortCircuit: true }
      }
      if (url.endsWith('.css')) {
        // jsdom exercises behavior, not styles; production CSS is checked by build.
        return { format: 'module', source: 'export default {}', shortCircuit: true }
      }
      return nextLoad(url, context)
    },
  })
}
