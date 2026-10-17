import { createClient } from "@supabase/supabase-js";
import { config } from "./config";

// Supabase is used only for sign-in. All data goes through our own API,
// which verifies the access token on every request.
export const supabase = createClient(config.supabaseUrl, config.supabaseKey, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: "pkce" },
});

export async function accessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}
