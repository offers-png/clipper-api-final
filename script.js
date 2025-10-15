// script.js
const API_BASE = "https://clipper-api-final.onrender.com";  // <-- Render URL

async function startTranscription() {
  const videoUrl = document.getElementById("videoUrl").value.trim();
  const status = document.getElementById("status");
  const result = document.getElementById("result");

  if (!videoUrl) {
    status.innerText = "⚠️ Please enter a video URL.";
    return;
  }

  status.innerText = "⏳ Sending video to Clipper...";
  result.classList.add("hidden");

  try {
    const resp = await fetch(`${API_BASE}/clip`, {        // <-- route must be /clip
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_url: videoUrl })       // matches backend
    });

    const data = await resp.json();

    if (resp.ok) {
      status.innerText = "✅ Transcription started.";
      // Optional: show a link if your API returns one
      if (data.check_url) {
        status.innerHTML = `✅ Transcription started. <a href="${data.check_url}" target="_blank" class="underline text-blue-400">Check progress</a>`;
      }
    } else {
      console.error(data);
      status.innerText = "❌ Failed to start transcription.";
    }
  } catch (err) {
    console.error(err);
    status.innerText = "🚨 Error contacting backend.";
  }
}
