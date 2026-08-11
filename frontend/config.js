// AI Content Studio - Client Configuration & Auth Helper

window.SUPABASE_URL = "https://fszerqcjcubmazbyliae.supabase.co";
window.SUPABASE_KEY = "sb_publishable_gyZKNzUp53HJRbnsGEd62w_lwATr4Ml";
window.API_BASE_URL = "";

// Initialize Supabase Client
if (window.supabase) {
    window.supabaseClient = window.supabase.createClient(window.SUPABASE_URL, window.SUPABASE_KEY);
    window.api = { supabase: window.supabaseClient };
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
