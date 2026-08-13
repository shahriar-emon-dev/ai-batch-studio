// AI ContentStudio — client configuration & auth helper.
//
// Values come from `env.js`, which the Vercel build generates from environment
// variables (see scripts/generate-frontend-env.js). The fallbacks below keep
// `python -m backend.app` working locally with no build step, where the API is
// served from the same origin as the pages.
const ENV = window.__ENV__ || {};

window.SUPABASE_URL = ENV.SUPABASE_URL || "https://fszerqcjcubmazbyliae.supabase.co";
window.SUPABASE_KEY = ENV.SUPABASE_KEY || "sb_publishable_gyZKNzUp53HJRbnsGEd62w_lwATr4Ml";

// Empty string = same origin. In a split deploy this points at the API host.
window.API_BASE_URL = (ENV.API_BASE_URL || "").replace(/\/$/, "");

// Warm the TCP/TLS connections to the two hosts every page talks to, so the
// first API call does not pay DNS + handshake on top of its round trip.
(function preconnect() {
    [window.SUPABASE_URL, window.API_BASE_URL].forEach((origin) => {
        if (!origin) return;
        const link = document.createElement("link");
        link.rel = "preconnect";
        link.href = origin;
        link.crossOrigin = "anonymous";
        document.head.appendChild(link);
    });
})();

// Initialize Supabase Client
if (window.supabase) {
    window.supabaseClient = window.supabase.createClient(window.SUPABASE_URL, window.SUPABASE_KEY);
    window.api = { supabase: window.supabaseClient };
} else {
    console.error("Supabase SDK failed to load — authentication will not work.");
}

// Global Auth helper
const Auth = {
    async getUser() {
        if (!window.supabaseClient) return null;
        const { data } = await window.supabaseClient.auth.getUser();
        return data?.user || null;
    },

    async requireAuth() {
        const user = await this.getUser();
        if (!user) {
            window.location.href = '/login.html';
            return null;
        }
        return user;
    },

    async signIn(email, password) {
        return await window.supabaseClient.auth.signInWithPassword({ email, password });
    },

    async signUp(email, password) {
        return await window.supabaseClient.auth.signUp({ email, password });
    },

    async signOut() {
        return await window.supabaseClient.auth.signOut();
    }
};
