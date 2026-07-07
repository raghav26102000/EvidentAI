import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, bootstrapSession, setAccessToken, subscribeAuth } from "./api";

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null);
    const [tenant, setTenant] = useState(null);
    const [loading, setLoading] = useState(true);

    const refreshMe = useCallback(async () => {
        try {
            const r = await api.get("/auth/me");
            setUser(r.data.user);
            setTenant(r.data.tenant);
            return r.data;
        } catch (_) {
            setUser(null);
            setTenant(null);
            return null;
        }
    }, []);

    useEffect(() => {
        (async () => {
            const ok = await bootstrapSession();
            if (ok) await refreshMe();
            setLoading(false);
        })();
        const unsub = subscribeAuth((t) => {
            if (!t) {
                setUser(null);
                setTenant(null);
            }
        });
        return unsub;
    }, [refreshMe]);

    const login = async (email, password) => {
        const r = await api.post("/auth/login", { email, password });
        setAccessToken(r.data.access_token);
        setUser(r.data.user);
        await refreshMe();
        return r.data;
    };

    const register = async (tenant_name, email, password) => {
        const r = await api.post("/auth/register", { tenant_name, email, password });
        setAccessToken(r.data.access_token);
        setUser(r.data.user);
        await refreshMe();
        return r.data;
    };

    const logout = async () => {
        try {
            await api.post("/auth/logout");
        } catch (_) {
            /* ignore */
        }
        setAccessToken(null);
        setUser(null);
        setTenant(null);
    };

    return (
        <AuthCtx.Provider value={{ user, tenant, loading, login, register, logout, refreshMe }}>
            {children}
        </AuthCtx.Provider>
    );
}

export const useAuth = () => useContext(AuthCtx);
