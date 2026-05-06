// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PatchaApp",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "PatchaApp",
            path: "Sources/PatchaApp",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("ApplicationServices"),
                .linkedFramework("SwiftUI"),
                .linkedLibrary("sqlite3"),
            ]
        ),
    ]
)
