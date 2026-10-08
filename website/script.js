const video = document.getElementById('researchos-video');
const playButton = document.getElementById('video-play');

if (video && playButton) {
  const hideOverlay = () => playButton.classList.add('is-hidden');

  playButton.addEventListener('click', async () => {
    try {
      await video.play();
      hideOverlay();
    } catch {
      video.controls = true;
    }
  });

  video.addEventListener('play', hideOverlay);
  video.addEventListener('ended', () => {
    video.currentTime = 0;
    playButton.classList.remove('is-hidden');
  });
}

const copyButton = document.getElementById('copy-quickstart');
const quickstart = document.getElementById('quickstart');

if (copyButton && quickstart) {
  copyButton.addEventListener('click', async () => {
    const original = copyButton.textContent;
    try {
      await navigator.clipboard.writeText(quickstart.innerText);
      copyButton.textContent = 'Copied';
    } catch {
      copyButton.textContent = 'Select & copy';
    }
    window.setTimeout(() => { copyButton.textContent = original; }, 1200);
  });
}
