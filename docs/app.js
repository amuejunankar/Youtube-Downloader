(() => {
  const OWNER = "amuejunankar";
  const REPO = "Youtube-Downloader";
  const RELEASE_TAG = "1.0.0";

  const repoUrl = `https://github.com/${OWNER}/${REPO}`;
  const releaseBase = `${repoUrl}/releases/download/${RELEASE_TAG}`;

  const ASSET_URLS = {
    "YouTubeDownloader-Installer.exe": `${releaseBase}/YouTubeDownloader-Installer.exe`,
    "YouTubeDownloader_Portable.exe": `${releaseBase}/YouTubeDownloader_Portable.exe`,
  };

  document.querySelectorAll("[data-asset]").forEach((link) => {
    const asset = link.getAttribute("data-asset");
    link.href = ASSET_URLS[asset] || `${repoUrl}/releases`;
    link.setAttribute("rel", "noopener");
  });

  ["repo-link", "repo-link-2"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.href = repoUrl;
  });

  const year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());

  const human = (bytes) => {
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) {
      value /= 1024;
      i += 1;
    }
    return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  };

  const setText = (id, text) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  };

  fetch(`https://api.github.com/repos/${OWNER}/${REPO}/releases/tags/${RELEASE_TAG}`, {
    headers: { Accept: "application/vnd.github+json" },
  })
    .then((res) => (res.ok ? res.json() : null))
    .then((release) => {
      if (!release) return;

      const badge = document.getElementById("version-badge");
      if (badge && release.tag_name) badge.textContent = `v${release.tag_name}`;

      (release.assets || []).forEach((asset) => {
        const size = human(asset.size);
        if (asset.name === "YouTubeDownloader-Installer.exe") {
          setText("size-installer", size ? `${size} · Windows 64-bit` : "Windows 64-bit");
          setText("meta-installer", size ? `${size} download` : "");
        }
        if (asset.name === "YouTubeDownloader_Portable.exe") {
          setText("size-portable", size ? `${size} · no install needed` : "no install needed");
          setText("meta-portable", size ? `${size} download` : "");
        }
      });
    })
    .catch(() => {});
})();
