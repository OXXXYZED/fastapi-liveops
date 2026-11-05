"""Generate .meta files with deterministic GUIDs for the Unity package.

Unity ignores assets without .meta files in immutable (git/registry) packages, so they must
be committed. Run after adding files: python scripts/gen_unity_meta.py
"""

import hashlib
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "sdk/unity/com.oxxxyzed.liveops"

FOLDER = "fileFormatVersion: 2\nguid: {guid}\nfolderAsset: yes\nDefaultImporter:\n  externalObjects: {{}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
SCRIPT = "fileFormatVersion: 2\nguid: {guid}\nMonoImporter:\n  externalObjects: {{}}\n  serializedVersion: 2\n  defaultReferences: []\n  executionOrder: 0\n  icon: {{instanceID: 0}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
ASMDEF = "fileFormatVersion: 2\nguid: {guid}\nAssemblyDefinitionImporter:\n  externalObjects: {{}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
TEXT = "fileFormatVersion: 2\nguid: {guid}\nTextScriptImporter:\n  externalObjects: {{}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"
PACKAGE = "fileFormatVersion: 2\nguid: {guid}\nPackageManifestImporter:\n  externalObjects: {{}}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n"


def guid(rel: str) -> str:
    return hashlib.md5(f"com.oxxxyzed.liveops/{rel}".encode()).hexdigest()


def template(p: Path) -> str:
    if p.is_dir():
        return FOLDER
    if p.name == "package.json":
        return PACKAGE
    return {".cs": SCRIPT, ".asmdef": ASMDEF}.get(p.suffix, TEXT)


def main() -> None:
    for p in sorted(PKG.rglob("*")):
        if p.suffix == ".meta" or any(part.endswith("~") or part.startswith(".") for part in p.relative_to(PKG).parts):
            continue
        rel = p.relative_to(PKG).as_posix()
        meta = p.with_name(p.name + ".meta")
        content = template(p).format(guid=guid(rel))
        if not meta.exists() or meta.read_text() != content:
            meta.write_text(content)
            print("wrote", meta.relative_to(PKG))


if __name__ == "__main__":
    main()
