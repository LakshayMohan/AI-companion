document.addEventListener('DOMContentLoaded', () => {
    // --- UI Elements ---
    const recordBtn = document.getElementById('recordBtn');
    const recordBtnText = document.getElementById('recordBtnText');
    const statusDisplay = document.getElementById("status");
    const responseAudio = document.getElementById("responseAudio");
    const audioVisualizer = document.getElementById('audioVisualizer');
    const transcriptionContainer = document.getElementById("transcription-container");
    const transcriptionOutput = document.getElementById("transcriptionOutput");
    const llmResponseContainer = document.getElementById("llm-response-container");
    const llmResponseOutput = document.getElementById("llmResponseOutput");
    // Settings sidebar elements
    const openSettingsBtn = document.getElementById('openSettingsBtn');
    const settingsSidebar = document.getElementById('settingsSidebar');
    const settingsOverlay = document.getElementById('settingsOverlay');
    const closeSettingsBtn = document.getElementById('closeSettingsBtn');
    const saveSettingsBtn = document.getElementById('saveSettingsBtn');
    const clearSettingsBtn = document.getElementById('clearSettingsBtn');
    const settingsSaveStatus = document.getElementById('settingsSaveStatus');
    const murfKeyInput = document.getElementById('murfKey');
    const assemblyKeyInput = document.getElementById('assemblyKey');
    const geminiKeyInput = document.getElementById('geminiKey');
    const opencageKeyInput = document.getElementById('opencageKey');
    const spotifyClientIdInput = document.getElementById('spotifyClientId');
    const spotifyClientSecretInput = document.getElementById('spotifyClientSecret');

    // --- State Variables ---
    let mediaRecorder, audioChunks = [], sessionId = null, isRecording = false;
    
    // --- Audio Visualization Variables ---
    let audioContext, analyser, microphoneSource, audioEleSource, dataArray, animationFrameId;

    // --- Session Management ---
    window.onload = () => {
        const params = new URLSearchParams(window.location.search);
        sessionId = params.get('session_id') || `session_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const newUrl = `${window.location.pathname}?session_id=${sessionId}`;
        window.history.replaceState({ path: newUrl }, '', newUrl);
        // Preload saved settings
        safeLoadSettingsToForm();
    };
    
    // --- Core Functionality ---
    recordBtn.addEventListener('click', () => {
        // Ensure AudioContext is created and resumed on the first user click
        if (!audioContext) {
            setupAudioContext();
        }
        audioContext.resume();

        if (isRecording) {
            stopRecording();
        } else {
            startRecording();
        }
    });

    // --- Initialize Audio Context and Analyser ---
    function setupAudioContext() {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 128;
        dataArray = new Uint8Array(analyser.frequencyBinCount);
        // Connect the shared analyser to the final output
        analyser.connect(audioContext.destination);
    }

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            resetUIState();

            // Connect Microphone to Analyser
            microphoneSource = audioContext.createMediaStreamSource(stream);
            microphoneSource.connect(analyser);
            startVisualizer();

            mediaRecorder = new MediaRecorder(stream);
            audioChunks = [];
            mediaRecorder.ondataavailable = event => audioChunks.push(event.data);
            mediaRecorder.onstop = () => {
                stream.getTracks().forEach(track => track.stop()); // Stop mic access
                processAudio();
            };
            mediaRecorder.start();
            isRecording = true;
            updateUIRecording(true);
        } catch (error) {
            statusDisplay.textContent = "Microphone access denied.";
            statusDisplay.classList.add('error');
        }
    }

    function stopRecording() {
        if (mediaRecorder && mediaRecorder.state !== "inactive") {
            mediaRecorder.stop();
            stopVisualizer();
        }
    }

    async function processAudio() {
        isRecording = false;
        updateUIRecording(false);
        statusDisplay.textContent = "Processing audio...";
        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        if (audioBlob.size < 1000) {
            statusDisplay.textContent = "Recording too short.";
            statusDisplay.classList.add('error');
            return;
        }
        const formData = new FormData();
        formData.append("file", audioBlob, "user_recording.webm");
        try {
            const response = await fetch(`/agent/chat/${sessionId}`, { method: "POST", body: formData });
            const data = await response.json();
            updateTextOutputs(data.transcription, data.llm_response);
            if (!response.ok) throw data;
            if (data.audio_url) {
                statusDisplay.textContent = "Response received!";
                await playResponseAudio(data.audio_url);
            } else {
                throw new Error("Response received, but audio link is missing.");
            }
        } catch (error) {
            const errorMessage = error.error || "An unexpected error occurred.";
            statusDisplay.textContent = errorMessage;
            statusDisplay.classList.add('error');
            if (error.audio_url) await playResponseAudio(error.audio_url, true);
        }
    }
    
    // MODIFIED: This function is now more robust for autoplay
    async function playResponseAudio(audioUrl, isErrorMessage = false) {
        try {
            // Ensure context is active before attempting to play
            if (audioContext.state === 'suspended') {
                await audioContext.resume();
            }

            // Disconnect the microphone source if it's still connected
            if (microphoneSource) {
                microphoneSource.disconnect();
                microphoneSource = null;
            }

            // Create a source for the <audio> element if it doesn't exist
            if (!audioEleSource) {
                audioEleSource = audioContext.createMediaElementSource(responseAudio);
                audioEleSource.connect(analyser);
            }
            
            responseAudio.src = audioUrl;
            await responseAudio.play();
            startVisualizer();
            
            responseAudio.onended = () => {
                stopVisualizer();
                statusDisplay.textContent = isErrorMessage ? "Error finished." : "Ready for your next question.";
            };
        } catch (e) {
            console.error("Failed to play response audio:", e);
            statusDisplay.textContent = "Could not play AI response. Click 'Record' to enable audio.";
            statusDisplay.classList.add('error');
        }
    }

    // --- UI and Visualizer Control Functions ---
    function startVisualizer() {
        audioVisualizer.style.display = 'block';
        const canvasCtx = audioVisualizer.getContext('2d');
        const centerX = audioVisualizer.width / 2;
        const centerY = audioVisualizer.height / 2;
        const radius = 75;

        function draw() {
            animationFrameId = requestAnimationFrame(draw);
            analyser.getByteFrequencyData(dataArray);
            canvasCtx.clearRect(0, 0, audioVisualizer.width, audioVisualizer.height);

            for (let i = 0; i < analyser.frequencyBinCount; i++) {
                const barHeight = dataArray[i] * 0.25;
                const angle = (i / analyser.frequencyBinCount) * 2 * Math.PI;
                const startX = centerX + radius * Math.cos(angle);
                const startY = centerY + radius * Math.sin(angle);
                const endX = centerX + (radius + barHeight) * Math.cos(angle);
                const endY = centerY + (radius + barHeight) * Math.sin(angle);
                const gradient = canvasCtx.createLinearGradient(startX, startY, endX, endY);
                gradient.addColorStop(0, '#9966ff');
                gradient.addColorStop(1, '#00ffcc');

                canvasCtx.strokeStyle = gradient;
                canvasCtx.lineWidth = 3;
                canvasCtx.beginPath();
                canvasCtx.moveTo(startX, startY);
                canvasCtx.lineTo(endX, endY);
                canvasCtx.stroke();
            }
        }
        draw();
    }

    function stopVisualizer() {
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
        }
        const canvasCtx = audioVisualizer.getContext('2d');
        canvasCtx.clearRect(0, 0, audioVisualizer.width, audioVisualizer.height);
        audioVisualizer.style.display = 'none';
    }
    
    function resetUIState() {
        transcriptionContainer.style.display = "none";
        llmResponseContainer.style.display = "none";
        transcriptionOutput.textContent = "";
        llmResponseOutput.textContent = "";
        statusDisplay.classList.remove('error');
    }

    function updateUIRecording(isRec) {
        recordBtn.classList.toggle('recording', isRec);
        recordBtnText.textContent = isRec ? "Stop" : "Record";
        statusDisplay.textContent = isRec ? "Listening..." : "Ready to start";
    }

    function updateTextOutputs(transcription, llmResponse) {
        if (transcription) {
            transcriptionOutput.textContent = transcription;
            transcriptionContainer.style.display = "block";
        }
        if (llmResponse) {
            llmResponseOutput.textContent = llmResponse;
            llmResponseContainer.style.display = "block";
        }
    }

    // ---------------- Settings Sidebar Logic ----------------
    function openSettings() {
        if (settingsOverlay) settingsOverlay.style.display = 'block';
        if (settingsSidebar) {
            settingsSidebar.classList.add('open');
            settingsSidebar.setAttribute('aria-hidden', 'false');
        }
        safeLoadSettingsToForm();
    }
    function closeSettings() {
        if (settingsOverlay) settingsOverlay.style.display = 'none';
        if (settingsSidebar) {
            settingsSidebar.classList.remove('open');
            settingsSidebar.setAttribute('aria-hidden', 'true');
        }
    }

    function safeLoadSettingsToForm() {
        try {
            const ls = window.localStorage;
            if (!ls) return;
            murfKeyInput && (murfKeyInput.value = ls.getItem('MURF_API_KEY') || '');
            assemblyKeyInput && (assemblyKeyInput.value = ls.getItem('ASSEMBLYAI_API_KEY') || '');
            geminiKeyInput && (geminiKeyInput.value = ls.getItem('GEMINI_API_KEY') || '');
            opencageKeyInput && (opencageKeyInput.value = ls.getItem('OPENCAGE_API_KEY') || '');
            spotifyClientIdInput && (spotifyClientIdInput.value = ls.getItem('CLIENT_ID') || '');
            spotifyClientSecretInput && (spotifyClientSecretInput.value = ls.getItem('CLIENT_SECRET') || '');
        } catch (_) {}
    }

    function saveSettingsFromForm() {
        try {
            const ls = window.localStorage;
            if (!ls) return;
            if (murfKeyInput) ls.setItem('MURF_API_KEY', murfKeyInput.value.trim());
            if (assemblyKeyInput) ls.setItem('ASSEMBLYAI_API_KEY', assemblyKeyInput.value.trim());
            if (geminiKeyInput) ls.setItem('GEMINI_API_KEY', geminiKeyInput.value.trim());
            if (opencageKeyInput) ls.setItem('OPENCAGE_API_KEY', opencageKeyInput.value.trim());
            if (spotifyClientIdInput) ls.setItem('CLIENT_ID', spotifyClientIdInput.value.trim());
            if (spotifyClientSecretInput) ls.setItem('CLIENT_SECRET', spotifyClientSecretInput.value.trim());
            if (settingsSaveStatus) {
                settingsSaveStatus.textContent = 'Saved your settings locally.';
                setTimeout(() => settingsSaveStatus.textContent = '', 1600);
            }
            // Notify app parts if needed
            document.dispatchEvent(new CustomEvent('configUpdated'));
        } catch (_) {}
    }

    function clearSettings() {
        try {
            const ls = window.localStorage;
            if (!ls) return;
            ['MURF_API_KEY','ASSEMBLYAI_API_KEY','GEMINI_API_KEY','OPENCAGE_API_KEY','CLIENT_ID','CLIENT_SECRET']
                .forEach(k => ls.removeItem(k));
            safeLoadSettingsToForm();
            if (settingsSaveStatus) {
                settingsSaveStatus.textContent = 'Cleared saved settings.';
                setTimeout(() => settingsSaveStatus.textContent = '', 1600);
            }
            document.dispatchEvent(new CustomEvent('configUpdated'));
        } catch (_) {}
    }

    if (openSettingsBtn) openSettingsBtn.addEventListener('click', openSettings);
    if (closeSettingsBtn) closeSettingsBtn.addEventListener('click', closeSettings);
    if (settingsOverlay) settingsOverlay.addEventListener('click', closeSettings);
    if (saveSettingsBtn) saveSettingsBtn.addEventListener('click', saveSettingsFromForm);
    if (clearSettingsBtn) clearSettingsBtn.addEventListener('click', clearSettings);
});
