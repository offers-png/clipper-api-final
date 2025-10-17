const API_URL = "https://clipper-api-final-1.onrender.com";

const fileBtn = document.getElementById("file-btn");
const linkBtn = document.getElementById("link-btn");
const whisperBtn = document.getElementById("whisper-btn");
const fileUpload = document.getElementById("file-upload");
const linkUpload = document.getElementById("link-upload");
const form = document.getElementById("clip-form");
const statusEl = document.getElementById("status");

let mode = "file";

fileBtn.onclick = () => switchMode("file");
linkBtn.onclick = () => switchMode("link");
whisperBtn.onclick = () => switchMode("whisper");

function switchMode(newMode) {
  mode = newMode;
  [fileBtn, linkBtn, whisperBtn].forEach(b => b.classList.remove("active"));
  [fileUpload, linkUpload].forEach(d => d.classList.add("hidden"));
  if (mode === "file") { fileBtn.classList.add("active"); fileUpload.classList.remove("hidden"); }
  if (mode === "link") { linkBtn.classList.add("active"); linkUpload.classList.remove("hidden"); }
  if (mode === "whisper") { whisperBtn.classList.add("active"); fileUpload.classList.remove("hidden"); }
}

form.addEventListener("submit", async e => {
  e.preventDefault();
  statusEl.textContent = "Processing...";

  const start = document.getElementById("start").value;
  const end = document.getElementById("end").value;

  try {
    if (mode === "file") {
      const file = document.getElementById("file-input").files[0];
      const fd = new FormData();
      fd.append("file", file);
      fd.append("start", start);
      fd.append("end", end);

      const r = await fetch(`${API_URL}/clip`, { method: "POST", body: fd });
      const blob = await r.blob();
      download(URL.createObjectURL(blob), "trimmed.mp4");
      statusEl.textContent = "✅ Trim done!";
    }

    if (mode === "link") {
      const url = document.getElementById("video-url").value;
      const r = await fetch(`${API_URL}/clip_link`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, start, end })
      });
      const blob = await r.blob();
      download(URL.createObjectURL(blob), "link_trimmed.mp4");
      statusEl.textContent = "✅ Trim from link done!";
    }

    if (mode === "whisper") {
      const file = document.getElementById("file-input").files[0];
      const fd = new FormData();
      fd.append("file", file);

      const r = await fetch(`${API_URL}/whisper`, { method: "POST", body: fd });
      const data = await r.json();
      statusEl.textContent = "🧠 Transcript: " + data.transcript.slice(0, 300);
    }
  } catch (e) {
    console.error(e);
    statusEl.textContent = "❌ " + e.message;
  }
});

function download(url, name) {
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
}
