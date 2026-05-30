import Foundation
import Supabase

// autoRefreshToken is disabled because the SDK gates its background refresh on
// NSApplication active/resign notifications. Patcha is an .accessory menu-bar app
// that is rarely "active", so the SDK's refresh stays stopped and tokens expire.
// AuthManager drives refresh on its own timer instead.
let supabase = SupabaseClient(
    supabaseURL: URL(string: PatchaConfig.supabaseURL)!,
    supabaseKey: PatchaConfig.supabaseAnonKey,
    options: SupabaseClientOptions(
        auth: .init(autoRefreshToken: false)
    )
)

