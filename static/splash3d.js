/* ==========================================================================
   LLMAGENT — Controlador Ultra-Fluido de la Portada de Video Estilo Bugatti
   - Precarga el MP4 completo en un Blob en RAM para loop sin tirones de red/disco.
   - Pausa automática si la pestaña se oculta.
   - Libera 100% el Blob y el decodificador de video al entrar al agente.
   ========================================================================== */

(() => {
  const splash = document.getElementById("splash-bugatti");
  const video = document.getElementById("splash-video");
  const btnEnter = document.getElementById("btn-splash-enter");
  const btnMenu = document.getElementById("btn-splash-menu");
  if (!splash || !btnEnter) return;

  let disposed = false;
  let blobUrl = null;

  if (video) {
    video.play().catch(() => {});
    // Cachear en Blob local para que cada bucle sea 100% instantáneo sin I/O
    fetch("/static/splash_bg.mp4?v=1")
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (!blob || disposed || !video) return;
        const curTime = video.currentTime || 0;
        blobUrl = URL.createObjectURL(blob);
        video.src = blobUrl;
        video.currentTime = curTime;
        video.play().catch(() => {});
      })
      .catch(() => {});
  }

  function onVisibility() {
    if (!video || disposed) return;
    if (document.hidden) {
      video.pause();
    } else {
      video.play().catch(() => {});
    }
  }
  document.addEventListener("visibilitychange", onVisibility);

  function enterAgent() {
    if (disposed) return;
    disposed = true;
    document.removeEventListener("visibilitychange", onVisibility);
    splash.classList.add("is-exiting");

    setTimeout(() => {
      if (video) {
        try {
          video.pause();
          video.removeAttribute("src");
          video.innerHTML = "";
          video.load();
        } catch (e) { }
      }
      if (blobUrl) {
        try { URL.revokeObjectURL(blobUrl); } catch (e) { }
        blobUrl = null;
      }
      splash.remove();
    }, 360);
  }

  btnEnter.addEventListener("click", enterAgent);
  if (btnMenu) btnMenu.addEventListener("click", enterAgent);

  window.addEventListener("keydown", function onKey(e) {
    if (disposed) {
      window.removeEventListener("keydown", onKey);
      return;
    }
    if (e.key === "Enter" || e.key === "Escape") {
      window.removeEventListener("keydown", onKey);
      enterAgent();
    }
  });
})();
