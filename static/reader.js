document.querySelectorAll('.print-button').forEach(button => button.addEventListener('click', () => window.print()));
document.querySelectorAll('audio').forEach(audio => audio.addEventListener('play', () => {
  document.querySelectorAll('audio').forEach(other => { if (other !== audio) other.pause(); });
}));
