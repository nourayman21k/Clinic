import { createContext, useContext, useState, useEffect } from "react";
import { login as apiLogin, apiFetch } from "../api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem("clinic_token"));
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    apiFetch("/api/auth/me", token)
      .then(setUser)
      .catch(() => {
        localStorage.removeItem("clinic_token");
        setToken(null);
      })
      .finally(() => setLoading(false));
  }, [token]);

  async function login(username, password) {
    const data = await apiLogin(username, password);
    localStorage.setItem("clinic_token", data.access_token);
    setToken(data.access_token);
    setUser({ role: data.role, full_name: data.full_name });
    return data;
  }

  function logout() {
    localStorage.removeItem("clinic_token");
    setToken(null);
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ token, user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}