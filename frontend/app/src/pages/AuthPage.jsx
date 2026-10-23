import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/useAuth'

export default function AuthPage({ mode }) {
  const isRegister = mode === 'register'
  const { login, register } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      if (isRegister) await register(email, password); else await login(email, password)
      navigate(location.state?.from || '/reservations', { replace: true })
    } catch (reason) { setError(reason.message) } finally { setBusy(false) }
  }
  return <div className="auth-page">
    <section className="auth-story"><span className="eyebrow">A smoother charging day</span><h1>{isRegister ? 'Your next charge starts here.' : 'Welcome back.'}</h1><p>Book a protected bay, arrive with confidence, and follow every step from reservation to release.</p><div className="story-steps"><span>01 <b>Reserve</b></span><span>02 <b>Auto check-in</b></span><span>03 <b>Charge</b></span></div></section>
    <form className="auth-card" onSubmit={submit}>
      <span className="eyebrow">{isRegister ? 'Create driver account' : 'Secure sign in'}</span>
      <h2>{isRegister ? 'Get on the road' : 'Sign in to Ecurious'}</h2>
      {error && <div className="alert error">{error}</div>}
      <label>Email<input type="email" required autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label>Password<input type="password" required minLength="8" autoComplete={isRegister ? 'new-password' : 'current-password'} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      <button className="btn primary wide" disabled={busy}>{busy ? 'Please wait…' : isRegister ? 'Create account' : 'Log in'}</button>
      <p>{isRegister ? 'Already registered?' : 'New to Ecurious?'} <Link to={isRegister ? '/login' : '/register'}>{isRegister ? 'Log in' : 'Create an account'}</Link></p>
      <small className="privacy-note">Your password is hashed server-side. Vehicle plates are only used for reservation matching.</small>
    </form>
  </div>
}
