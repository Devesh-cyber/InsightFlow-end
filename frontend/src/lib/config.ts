// Values come from .env (local) or the hosting dashboard (Vercel). All are public:
// the publishable key is designed to ship to browsers. The SECRET key never goes here.
function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`${name} is not set. Copy .env.example to .env and fill it in.`);
  }
  return value;
}

export const config = {
  apiUrl: required("VITE_API_URL", import.meta.env.VITE_API_URL).replace(/\/+$/, ""),
  supabaseUrl: required("VITE_SUPABASE_URL", import.meta.env.VITE_SUPABASE_URL),
  supabaseKey: required("VITE_SUPABASE_PUBLISHABLE_KEY", import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY),
};
