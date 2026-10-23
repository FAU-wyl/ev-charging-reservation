import { useEffect, useMemo, useState } from 'react'
import { authApi } from '../api'
import { AuthContext } from './AuthContextValue'

const STORAGE_KEY = 'ecurious_app_token'

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(STORAGE_KEY))
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(Boolean(token))

  useEffect(() => {
    let live = true
    if (!token) {
      return () => { live = false }
    }
    authApi.me(token)
      .then((value) => live && setUser(value))
      .catch(() => {
        if (live) {
          localStorage.removeItem(STORAGE_KEY)
          setToken(null)
          setUser(null)
        }
      })
      .finally(() => live && setLoading(false))
    return () => { live = false }
  }, [token])

  const acceptAuth = (result) => {
    localStorage.setItem(STORAGE_KEY, result.access_token)
    setToken(result.access_token)
    setUser(result.user)
  }

  const value = useMemo(() => ({
    token,
    user,
    loading,
    login: async (email, password) => acceptAuth(await authApi.login(email, password)),
    register: async (email, password) => acceptAuth(await authApi.register(email, password)),
    logout: async () => {
      try { if (token) await authApi.logout(token) } finally {
        localStorage.removeItem(STORAGE_KEY)
        setToken(null)
        setUser(null)
      }
    },
  }), [token, user, loading])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
