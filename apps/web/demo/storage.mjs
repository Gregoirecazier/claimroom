// Local preview storage only. Files stay in this browser; there is no network upload.
export const uploads = new Map()
function database() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('claimroom-ui-preview-files', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('files')
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}
export async function storeUpload(path, file) {
  const previous = uploads.get(path)
  if (previous?.url) URL.revokeObjectURL(previous.url)
  uploads.set(path, { file, url: URL.createObjectURL(file) })
  const db = await database()
  await new Promise((resolve, reject) => {
    const tx = db.transaction('files', 'readwrite')
    tx.objectStore('files').put(file, path)
    tx.oncomplete = resolve
    tx.onerror = () => reject(tx.error)
  }).finally(() => db.close())
}
export async function readUpload(path) {
  if (uploads.has(path) || !path.startsWith('local-upload/')) return uploads.get(path)
  const db = await database()
  const file = await new Promise((resolve, reject) => {
    const request = db.transaction('files').objectStore('files').get(path)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  }).finally(() => db.close())
  if (file) uploads.set(path, { file, url: URL.createObjectURL(file) })
  return uploads.get(path)
}
