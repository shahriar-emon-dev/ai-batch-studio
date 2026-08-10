/**
 * AI Batch Studio - API & Authentication Module
 * Requires Supabase JS CDN script to be loaded before this script.
 * Expects window.SUPABASE_URL and window.SUPABASE_KEY to be set globally.
 */

class ApiClient {
    constructor() {
        if (!window.supabase) {
            console.error("Supabase client not loaded. Please include the CDN script.");
            return;
        }

        if (!window.SUPABASE_URL || !window.SUPABASE_KEY) {
            console.error("Missing Supabase configuration. Ensure template variables are injected.");
            return;
        }

        this.supabase = window.supabase.createClient(window.SUPABASE_URL, window.SUPABASE_KEY);
    }

    /**
     * Helper to get the current auth session
     */
    async getSession() {
        const { data, error } = await this.supabase.auth.getSession();
        if (error) {
            console.error("Error getting session:", error);
            return null;
        }
        return data.session;
    }

    /**
     * Check if user is authenticated and optionally redirect to login
     */
    async requireAuth() {
        const session = await this.getSession();
        if (!session || !session.user) {
            window.location.href = "login.html";
            return null;
        }
        return session.user;
    }

    /**
     * Check if user is already authenticated (for login/register pages)
     */
    async requireGuest() {
        const session = await this.getSession();
        if (session) {
            window.location.href = "dashboard.html";
        }
    }

    /**
     * Core fetch wrapper that injects the Supabase JWT token
     */
    async fetchWithAuth(url, options = {}) {
        const session = await this.getSession();
        if (!session) {
            throw new Error("Unauthorized. Please log in.");
        }

        const headers = {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${session.access_token}`,
            ...options.headers
        };

        // If body is FormData, remove Content-Type so browser sets it with boundary
        if (options.body instanceof FormData) {
            delete headers['Content-Type'];
        }

        const config = {
            ...options,
            headers
        };

        const response = await fetch(url, config);
        
        // Handle 401 Unauthorized globally
        if (response.status === 401) {
            await this.logout();
            window.location.href = "/login";
        }

        return response;
    }

    /**
     * Login using Supabase Auth
     */
    async login(email, password) {
        return await this.supabase.auth.signInWithPassword({
            email: email,
            password: password,
        });
    }

    /**
     * Register using Supabase Auth
     */
    async register(email, password) {
        return await this.supabase.auth.signUp({
            email: email,
            password: password,
        });
    }

    /**
     * Logout using Supabase Auth
     */
    async logout() {
        return await this.supabase.auth.signOut();
    }

    /**
     * Send password reset email
     */
    async resetPasswordEmail(email) {
        return await this.supabase.auth.resetPasswordForEmail(email, {
            redirectTo: window.location.origin + '/reset-password',
        });
    }

    /**
     * Update user password (used on the reset password page after clicking the email link)
     */
    async updatePassword(newPassword) {
        return await this.supabase.auth.updateUser({ password: newPassword });
    }
}

// Instantiate a global client
window.api = new ApiClient();
