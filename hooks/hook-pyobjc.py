from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

hiddenimports = (
    collect_submodules('objc')
    + collect_submodules('Foundation')
    + collect_submodules('AppKit')
    + collect_submodules('Quartz')
    + collect_submodules('ApplicationServices')
)
