import Foundation
import Supabase

let supabase = SupabaseClient(
    supabaseURL: URL(string: PatchaConfig.supabaseURL)!,
    supabaseKey: PatchaConfig.supabaseAnonKey
)

