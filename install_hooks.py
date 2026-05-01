"""
install_hooks.py — Install git hooks for the Trading Assistant project
Run once from your project root:
    python install_hooks.py
"""

import os
import shutil
import stat

HOOK_SOURCE = "post-commit"
HOOK_DEST   = os.path.join(".git", "hooks", "post-commit")

def install():
    if not os.path.exists(".git"):
        print("❌ Not a git repository. Run from project root.")
        return

    os.makedirs(os.path.join(".git", "hooks"), exist_ok=True)

    if not os.path.exists(HOOK_SOURCE):
        print(f"❌ {HOOK_SOURCE} not found. Place it in the project root first.")
        return

    shutil.copy(HOOK_SOURCE, HOOK_DEST)

    # Make executable (matters on Mac/Linux, ignored on Windows)
    st = os.stat(HOOK_DEST)
    os.chmod(HOOK_DEST, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    print(f"✅ Git hook installed: {HOOK_DEST}")
    print("   BUILD_LOG.md will now auto-update after every git commit.")
    print("   Test it: make a commit and check BUILD_LOG.md")

if __name__ == "__main__":
    install()
