// MediaRecorder's webm output carries no duration in its container header
// (Chromium issue 642012): the <audio> element reports duration as Infinity
// (sometimes NaN) and playback can stick at 0:00. Forcing a seek past the end
// makes Chrome scan the stream for the real end, which repairs duration and
// seeking for the rest of the element's life; seeking back to 0 leaves it
// ready to play from the start.
//
// This only drives the <audio> element's own playback pipeline -- it never
// re-encodes or re-parses the blob, so it avoids the codec gaps that
// AudioContext.decodeAudioData has for webm/opus (that path throws
// EncodingError on the very same blob Groq Whisper transcribes fine).
export function fixStuckDuration(audioEl) {
  if (!audioEl) return;
  const d = audioEl.duration;
  if (Number.isFinite(d) && d > 0) return; // already has a usable duration
  audioEl.currentTime = 1e101;
  const onTimeUpdate = () => {
    audioEl.removeEventListener("timeupdate", onTimeUpdate);
    audioEl.currentTime = 0;
  };
  audioEl.addEventListener("timeupdate", onTimeUpdate);
}

// Temporary diagnostics: surfaces why an <audio> element refuses to play.
// MediaError codes: 1=ABORTED 2=NETWORK 3=DECODE 4=SRC_NOT_SUPPORTED.
export function logAudioDiagnostics(audioEl, label) {
  if (!audioEl) return;
  const err = audioEl.error;
  // console.error, not console.log -- Vite only forwards warn/error to the
  // dev-server terminal.
  console.error(
    `[audio:${label}] duration=${audioEl.duration} readyState=${audioEl.readyState}` +
      ` networkState=${audioEl.networkState} src=${audioEl.currentSrc?.slice(0, 60)}` +
      (err ? ` ERROR code=${err.code} message=${err.message}` : "")
  );
}
