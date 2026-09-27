import { storeUpload } from './storage.mjs'
const session = { access_token: 'local-ui-preview-only', user: { email: 'gestionnaire@example.test' } }
export const authConfigured = true
export const supabase = {
  auth: {
    getSession: async () => ({ data: { session } }),
    onAuthStateChange: () => ({ data: { subscription: { unsubscribe() {} } } }),
    signOut: async () => {}, signInWithPassword: async () => ({ error: null }),
  },
  storage: { from: () => ({ uploadToSignedUrl: async (path, _token, file) => {
    await storeUpload(path, file)
    return { error: null }
  } }) },
}
