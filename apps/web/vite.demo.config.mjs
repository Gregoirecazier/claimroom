import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'
import { makeCases } from './demo/fixtures.mjs'

const local = path => fileURLToPath(new URL(path, import.meta.url))
const media = new Map(makeCases().flatMap(c => c.evidence.map(e => [e.storage_path, {
  path: e.mime_type === 'application/pdf' ? local('./demo/devis-demo.pdf') : local(`../api/claim_api/fixture_media/${c.scenario_id}/${e.original_filename}`),
  type: e.mime_type,
}])))

export default defineConfig(({ command }) => {
  if (command !== 'serve') throw new Error('The presentation preview is development-only; use the normal production build.')
  return {
    envDir: false, publicDir: false,
    resolve: { alias: [
      { find: /^\.{1,2}\/(?:\.\.\/)*lib\/depositTransport$/, replacement: local('./demo/depositTransport.mjs') },
      { find: /^\.{1,2}\/(?:\.\.\/)*lib\/api$/, replacement: local('./demo/api.mjs') },
      { find: /^\.{1,2}\/(?:\.\.\/)*lib\/supabase$/, replacement: local('./demo/supabase.mjs') },
    ] },
    plugins: [react(), {
      name: 'local-presentation',
      transformIndexHtml(html) {
        return html.replace('<body>', '<body><div style="position:fixed;bottom:0;left:0;right:0;z-index:120;background:#17392e;color:white;text-align:center;padding:6px;font:12px sans-serif">Aperçu local · scénario préparé · aucun agent ni envoi réel · <button type="button" id="reset-ui-preview" style="border:0;background:transparent;color:white;text-decoration:underline;cursor:pointer">Réinitialiser les exemples</button></div><script type="module" src="/demo/previewControls.mjs"></script>')
      },
      configureServer(server) {
        // Exact allowlist: never expose the fixture directory or uploaded evidence.
        server.middlewares.use(async (req, res, next) => {
          const entry = media.get(req.url?.split('?')[0])
          if (!entry) return next()
          try {
            const bytes = await readFile(entry.path)
            res.setHeader('Content-Type', entry.type)
            res.setHeader('Content-Length', bytes.length)
            res.end(bytes)
          } catch { res.statusCode = 404; res.end('Pièce de démonstration indisponible') }
        })
      },
    }],
    server: { host: '127.0.0.1', port: 5180, strictPort: true },
  }
})
