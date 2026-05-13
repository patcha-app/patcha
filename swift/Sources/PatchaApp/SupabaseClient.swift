import Foundation
import Supabase

let supabase = SupabaseClient(
    supabaseURL: URL(string: PatchaConfig.supabaseURL)!,
    supabaseKey: PatchaConfig.supabaseAnonKey
)

enum PatchaConfig {
    static let supabaseURL = "https://bfethkbnuncuztxocvrk.supabase.co"
    static let supabaseAnonKey = "sb_publishable_YrHVPZaWIcY5PdcGrZ2l8w_gVfvcyxd"
}

