const form = document.querySelector("#form");
    const topic = document.querySelector("#topic");
    const duration = document.querySelector("#duration");
    const durationValue = document.querySelector("#duration-value");
    const languageInput = document.querySelector("#language");
    const voiceInput = document.querySelector("#voice");
    const speedInput = document.querySelector("#speed");
    const speedValue = document.querySelector("#speed-value");
    const submit = document.querySelector("#submit");
    const status = document.querySelector("#status");
    const results = document.querySelector("#results");
    const emptyState = document.querySelector("#empty-state");
    const preview = document.querySelector("#preview");
    const previewFrame = document.querySelector("#preview-frame");
    const expandPreview = document.querySelector("#expand-preview");
    expandPreview.addEventListener("click", async () => {
      try {
        if (document.fullscreenElement === previewFrame) await document.exitFullscreen();
        else if (previewFrame.requestFullscreen) await previewFrame.requestFullscreen();
        else window.open(preview.src, "_blank", "noopener");
      } catch (error) {
        window.open(preview.src, "_blank", "noopener");
      }
    });
    document.addEventListener("fullscreenchange", () => {
      const expanded = document.fullscreenElement === previewFrame;
      expandPreview.textContent = expanded ? "⤢ Reduce" : "⛶ Expand";
      expandPreview.setAttribute("aria-pressed", String(expanded));
      expandPreview.setAttribute("aria-label", expanded ? "Reduce lesson preview" : "Expand lesson preview");
    });
    const badge = document.querySelector("#badge");
    const resultTitle = document.querySelector("#result-title");
    const video = document.querySelector("#lesson-video");
    const videoLanguage = document.querySelector("#video-language");
    const videoNote = document.querySelector("#video-note");
    const downloadVideo = document.querySelector("#download-video");
    let generating = false;
    const voiceChoices = {
      "en-IN": [["hm_omega", "Omega"], ["hm_psi", "Psi"], ["hf_alpha", "Alpha"], ["hf_beta", "Beta"], ["af_heart", "Heart"], ["af_bella", "Bella"], ["am_adam", "Adam"], ["bf_emma", "Emma"], ["bm_george", "George"]],
      "hi-IN": [["hf_alpha", "Alpha"], ["hf_beta", "Beta"], ["hm_omega", "Omega"], ["hm_psi", "Psi"]],
    };

    function updateVoiceOptions() {
      const language = languageInput.value;
      const choices = voiceChoices[language] || [];
      const previous = voiceInput.value;
      voiceInput.replaceChildren();
      for (const [value, label] of choices) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        voiceInput.append(option);
      }
      if (choices.some(([value]) => value === previous)) voiceInput.value = previous;
      voiceInput.disabled = choices.length === 0;
      if (!choices.length) {
        const option = document.createElement("option");
        option.textContent = "Windows Tamil voice";
        voiceInput.append(option);
      }
    }
    languageInput.addEventListener("change", updateVoiceOptions);
    updateVoiceOptions();
    speedInput.addEventListener("input", () => { speedValue.value = `${Number(speedInput.value).toFixed(2).replace(/0$/, "")}×`; });

    function setStatus(message, kind = "") {
      status.textContent = message;
      status.className = `status visible ${kind}`.trim();
    }

    function clearVideo() {
      video.pause();
      video.removeAttribute("src");
      video.load();
      downloadVideo.removeAttribute("href");
      downloadVideo.hidden = true;
    }

    function clearResults() {
      if (document.fullscreenElement === previewFrame) document.exitFullscreen();
      results.classList.remove("visible");
      emptyState.hidden = false;
      preview.src = "about:blank";
      clearVideo();
    }

    function showPreview(data, language) {
      resultTitle.textContent = `${data.title || "Your lesson"} · ${data.duration || Number(duration.value)} sec`;
      badge.textContent = data.fallback ? "SAMPLE LESSON" : "LOCAL PLAN";
      badge.style.color = data.fallback ? "#85621c" : "#3453ce";
      badge.style.background = data.fallback ? "#fff3d4" : "#edf0ff";
      videoLanguage.textContent = ({ "en-IN": "English (India)", "hi-IN": "Hindi", "ta-IN": "Tamil" })[language] || language;
      emptyState.hidden = true;
      results.classList.add("visible");

      preview.onload = () => {
        const languageSelect = preview.contentDocument?.querySelector("#language");
        if (languageSelect && [...languageSelect.options].some((option) => option.value === language)) {
          languageSelect.value = language;
          languageSelect.dispatchEvent(new Event("change", { bubbles: true }));
        }
      };
      preview.src = `/preview?v=${Date.now()}`;
    }

    topic.addEventListener("input", () => {
      submit.disabled = !topic.value.trim() || generating;
      if (!generating) {
        status.className = "status";
        clearResults();
      }
    });

    document.querySelectorAll("[data-topic]").forEach((button) => {
      button.addEventListener("click", () => {
        topic.value = button.dataset.topic;
        topic.dispatchEvent(new Event("input", { bubbles: true }));
        topic.focus();
      });
    });

    duration.addEventListener("input", () => {
      durationValue.value = `${duration.value} sec`;
    });
    async function readGeneration(response, language) {
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(detail || "The lesson could not be prepared.");
      }
      if (!response.body) throw new Error("The lesson planner returned an empty response.");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let result = null;
      const consume = (block) => {
        const line = block.split(/\r?\n/).find((item) => item.startsWith("data:"));
        if (!line) return;
        const update = JSON.parse(line.slice(5).trim());
        if (update.stage === "preview") showPreview(update, language);
        if (update.stage) setStatus(update.message);
        if (update.done) result = update;
      };

      while (true) {
        const part = await reader.read();
        buffer += decoder.decode(part.value || new Uint8Array(), { stream: !part.done });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop();
        blocks.forEach(consume);
        if (part.done) break;
      }
      if (buffer.trim()) consume(buffer);
      if (!result) throw new Error("The lesson planner ended without a result. Please try again.");
      return result;
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (generating || !topic.value.trim()) return;

      generating = true;
      submit.disabled = true;
      submit.innerHTML = '<span aria-hidden="true">◌</span> Creating your lesson…';
      emptyState.hidden = true;
      results.classList.remove("visible");
      setStatus("Planning the lesson, creating narration, and rendering the video. This can take a few minutes.");

      const request = {
        topic: topic.value.trim(),
        level: document.querySelector("#level").value,
        language: document.querySelector("#language").value,
        voice: voiceInput.value,
        speed: Number(speedInput.value),
        duration: Number(duration.value),
      };

      try {
        const response = await fetch("/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
        });
        const data = await readGeneration(response, request.language);
        if (!data.ok) {
          throw new Error(data.error || "Lesson generation failed without an error message. Check the studio terminal.");
        }

        showPreview(data, request.language);
        if (data.videoReady) {
          const videoUrl = `/video/${encodeURIComponent(request.language)}?v=${Date.now()}`;
          video.src = videoUrl;
          video.load();
          downloadVideo.href = videoUrl;
          downloadVideo.download = `blu-lesson-${request.language}.mp4`;
          downloadVideo.hidden = false;
          videoNote.textContent = "Your matching video is ready. Click play to watch it, or download the MP4.";
          video.scrollIntoView({ behavior: "smooth", block: "center" });
        } else {
          clearVideo();
          videoNote.textContent = data.videoError || "The lesson preview is ready, but narration or video export was unavailable.";
        }

        const resultMessage = data.fallback
            ? `The local model could not create a prompt-specific lesson, so a topic-specific local fallback was used. ${data.fallbackReason || ""} ${data.videoReady ? "Your fallback video is ready." : videoNote.textContent}`
            : `${data.cached ? "Reused your cached lesson and video." : data.videoReady ? "Your lesson and video are ready." : "Your lesson preview is ready."}${data.videoError ? ` ${data.videoError}` : ""}`;
        const stageTimes = data.timings
          ? Object.entries(data.timings).map(([stage, seconds]) => `${stage} ${Number(seconds).toFixed(2)}s`).join(" · ")
          : "";
        setStatus(`${resultMessage}${stageTimes ? ` Stage times: ${stageTimes}.` : ""}`, data.fallback ? "warn" : "good");
      } catch (error) {
        emptyState.hidden = false;
        setStatus(`Could not create the lesson: ${error.message}`, "warn");
      } finally {
        generating = false;
        submit.disabled = !topic.value.trim();
        submit.innerHTML = '<span aria-hidden="true">✦</span> Generate my lesson';
      }
    });
