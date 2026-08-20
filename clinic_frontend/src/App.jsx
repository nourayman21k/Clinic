import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import Login from "./pages/Login";
import DoctorView from "./pages/DoctorView";
import SecretaryView from "./pages/SecretaryView";

function ProtectedRoute({ role, children }) {
  const { user, token, loading } = useAuth();
  if (loading) return null;
  if (!token) return <Navigate to="/login" replace />;
  if (role && user?.role !== role) {
    return <Navigate to={user?.role === "doctor" ? "/doctor" : "/secretary"} replace />;
  }
  return children;
}

function AppRoutes() {
  const { token, user, loading } = useAuth();
  if (loading) return null;

  return (
    <Routes>
      <Route path="/login" element={token ? <Navigate to={user?.role === "doctor" ? "/doctor" : "/secretary"} /> : <Login />} />
      <Route path="/doctor" element={<ProtectedRoute role="doctor"><DoctorView /></ProtectedRoute>} />
      <Route path="/secretary" element={<ProtectedRoute role="secretary"><SecretaryView /></ProtectedRoute>} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}