const API_BASE = "https://clipper-api-final.onrender.com";


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
    const response = await fetch(`${API_BASE}/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_url: videoUrl })
    });

    const data = await response.json();

    if (data.check_url) {
      status.innerHTML = `✅ Transcription started. <a href="${data.check_url}" target="_blank" class="underline text-blue-400">Check progress</a>`;
    } else {
      status.innerText = "❌ Failed to start transcription.";
      console.error(data);
    }
  } catch (err) {
    status.innerText = "🚨 Error contacting backend.";
    console.error(err);
  }
}
