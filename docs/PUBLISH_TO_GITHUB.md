# Publishing this prepared commit to GitHub

The GitHub account connection used while preparing this patch set has read-only permission to the upstream `dolphin-creator/VideoContext-Engine` repository, and cannot create a new repository through the available connector actions. This patch set therefore includes a locally prepared Git commit bundle; it has not been pushed to GitHub by this workflow.

Suggested destination: a new repository in your own GitHub account named `VideoContext-Engine-Apple-Silicon` (or another name you choose). Keep the upstream attribution in README/UPSTREAM.md.

1. In GitHub, create a new empty repository named `VideoContext-Engine-Apple-Silicon`. Do not initialize it with README/license/gitignore (they are already included).
2. Download `VideoContext-Engine-Apple-Silicon.bundle` from the release package and run:

   ```bash
   git clone ~/Downloads/VideoContext-Engine-Apple-Silicon.bundle
   cd VideoContext-Engine-Apple-Silicon
   git remote add origin https://github.com/YOUR_GITHUB_LOGIN/VideoContext-Engine-Apple-Silicon.git
   git push -u origin main
   ```

   Replace `YOUR_GITHUB_LOGIN` with the account name, or copy the exact remote URL from the empty GitHub repository page.
3. Check the `main` branch on GitHub. The first commit contains the README, attribution, benchmark report, patch scripts, settings profile, and macOS launcher/install resources.

This method keeps changes separate from the upstream author's repository. Do not push a third-party customization directly to upstream without their permission; open a pull request from a fork only if the upstream maintainers would welcome these changes.
