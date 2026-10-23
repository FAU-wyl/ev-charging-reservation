import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import ProtectedRoute from './components/ProtectedRoute'
import { AuthProvider } from './context/AuthContext'
import AdminPage from './pages/AdminPage'
import AuthPage from './pages/AuthPage'
import MapPage from './pages/MapPage'
import NotFoundPage from './pages/NotFoundPage'
import ReservationDetailPage from './pages/ReservationDetailPage'
import ReservationsPage from './pages/ReservationsPage'
import StationPage from './pages/StationPage'
import VehiclesPage from './pages/VehiclesPage'
import './index.css'

const DriverOnly = ({ children }) => <ProtectedRoute>{children}</ProtectedRoute>
const AdminOnly = ({ children }) => <ProtectedRoute admin>{children}</ProtectedRoute>

export default function App() {
  return <BrowserRouter><AuthProvider><Routes>
    <Route element={<Layout />}>
      <Route index element={<MapPage />} />
      <Route path="stations/:stationId" element={<StationPage />} />
      <Route path="login" element={<AuthPage mode="login" />} />
      <Route path="register" element={<AuthPage mode="register" />} />
      <Route path="vehicles" element={<DriverOnly><VehiclesPage /></DriverOnly>} />
      <Route path="reservations" element={<DriverOnly><ReservationsPage /></DriverOnly>} />
      <Route path="reservations/:reservationId" element={<DriverOnly><ReservationDetailPage /></DriverOnly>} />
      <Route path="admin" element={<AdminOnly><AdminPage /></AdminOnly>} />
      <Route path="admin/bays" element={<AdminOnly><AdminPage section="bays" /></AdminOnly>} />
      <Route path="admin/reservations" element={<AdminOnly><AdminPage section="reservations" /></AdminOnly>} />
      <Route path="admin/events" element={<AdminOnly><AdminPage section="events" /></AdminOnly>} />
      <Route path="admin/alerts" element={<AdminOnly><AdminPage section="alerts" /></AdminOnly>} />
      <Route path="admin/analytics" element={<AdminOnly><AdminPage section="analytics" /></AdminOnly>} />
      <Route path="*" element={<NotFoundPage />} />
    </Route>
  </Routes></AuthProvider></BrowserRouter>
}
