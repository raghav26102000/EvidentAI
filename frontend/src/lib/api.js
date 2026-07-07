import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

let accessToken = null;
let refreshInFlight = null;
const listeners = new Set();

export function setAccessToken(tok) {
    accessToken = tok || null;
    for (const l of listeners) l(accessToken);
}

export function getAccessToken() {
    return accessToken;
}

export function subscribeAuth(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
}

export const api = axios.create({
    baseURL: API,
    withCredentials: true, // send httpOnly refresh cookie on /api/auth calls
});

api.interceptors.request.use((cfg) => {
    if (accessToken) {
        cfg.headers = cfg.headers || {};
        cfg.headers.Authorization = `Bearer ${accessToken}`;
    }
    return cfg;
});

async function performRefresh() {
    if (!refreshInFlight) {
        refreshInFlight = axios
            .post(`${API}/auth/refresh`, {}, { withCredentials: true })
            .then((r) => {
                setAccessToken(r.data.access_token);
                return r.data;
            })
            .catch((e) => {
                setAccessToken(null);
                throw e;
            })
            .finally(() => {
                refreshInFlight = null;
            });
    }
    return refreshInFlight;
}

api.interceptors.response.use(
    (r) => r,
    async (err) => {
        const cfg = err.config || {};
        const status = err.response && err.response.status;
        const url = cfg.url || "";
        if (
            status === 401 &&
            !cfg._retried &&
            !url.includes("/auth/login") &&
            !url.includes("/auth/register") &&
            !url.includes("/auth/refresh")
        ) {
            cfg._retried = true;
            try {
                await performRefresh();
                return api(cfg);
            } catch (_) {
                setAccessToken(null);
            }
        }
        return Promise.reject(err);
    },
);

export async function bootstrapSession() {
    try {
        await performRefresh();
        return true;
    } catch (_) {
        return false;
    }
}
