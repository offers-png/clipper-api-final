<script>
document.getElementById("start-btn").addEventListener("click", async () => {
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

    if (!response.ok) {
      throw new Error(`Server error: ${response.status}`);
    }

    const data = await response.json();
    console.log("Response data:", data); // Debugging info

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

      // Add download + copy buttons
      const btnContainer = document.createElement("div");
      btnContainer.style.marginTop = "1rem";

      const downloadBtn = document.createElement("button");
      downloadBtn.textContent = "⬇️ Download Transcript (.txt)";
      downloadBtn.style.marginRight = "10px";
      downloadBtn.onclick = () => {
        const blob = new Blob([data.text], { type: "text/plain" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "transcript.txt";
        a.click();
      };

      const copyBtn = document.createElement("button");
      copyBtn.textContent = "📋 Copy Transcript";
      copyBtn.onclick = async () => {
        await navigator.clipboard.writeText(data.text);
        alert("Copied to clipboard!");
      };

      btnContainer.appendChild(downloadBtn);
      btnContainer.appendChild(copyBtn);

      const card = document.querySelector(".card");
      card.appendChild(textBox);
      card.appendChild(btnContainer);

    } else {
      status.style.color = "#ffaa00";
      status.textContent = "⚠️ No transcript found.";
    }
  } catch (err) {
    console.error(err);
    status.style.color = "#ff5555";
    status.textContent = "❌ Failed: " + err.message;
  }
});
</script>
