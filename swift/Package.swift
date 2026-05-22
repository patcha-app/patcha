// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PatchaApp",
    platforms: [.macOS(.v13)],
    dependencies: [
        .package(url: "https://github.com/supabase/supabase-swift", from: "2.0.0"),
    ],
    targets: [
        .target(
            name: "PatchaAppLib",
            dependencies: [
                .product(name: "Supabase", package: "supabase-swift"),
            ],
            path: "Sources/PatchaApp",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("ApplicationServices"),
                .linkedFramework("SwiftUI"),
                .linkedFramework("ServiceManagement"),
                .linkedLibrary("sqlite3"),
            ]
        ),
        .executableTarget(
            name: "PatchaApp",
            dependencies: ["PatchaAppLib"],
            path: "Sources/PatchaEntry"
        ),
    ]
)
