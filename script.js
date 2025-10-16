document
  .getElementById("start-btn")
  .addEventListener("click", async () => {
    const url = document.getElementById("video-url").value.trim();
    const status = document.getElementById("status");
    status.style.color = "#fff";
    status.textContent = "⏳ Transcribing... Please wait...";

    if (!url) {
      status.style.color = "#ff5555";
      status.textContent = "❌ Please enter a YouTube URL.";
      return;
    }

    try {
      const response = await fetch("https://clipper-api-final.onrender.com/transcribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });

      const data = await response.json();

      if (data.error) {
        status.style.color = "#ff5555";
        status.textContent = "❌ " + data.error;
      } else if (data.text) {
        status.style.color = "#00ff88";
        status.textContent = "✅ Transcription completed!";
        const textBox = document.createElement("textarea");
        textBox.value = data.text;
        textBox.rows = 15;
        textBox.style.width = "100%";
        textBox.style.marginTop = "1rem";
        document.querySelector(".card").appendChild(textBox);
      } else {
        status.style.color = "#ffaa00";
        status.textContent = "⚠️ No transcript found.";
      }
    } catch (err) {
      status.style.color = "#ff5555";
      status.textContent = "❌ Failed: " + err.message;
    }
  });
